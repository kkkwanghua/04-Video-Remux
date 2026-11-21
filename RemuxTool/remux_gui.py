#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频批量重封装工具 (Video Batch Remuxer)
===========================================
批量扫描文件夹下的视频文件，无损重封装（stream copy，不重新编码）到
指定的容器格式（MOV / MP4 / MKV），可保留子目录层级。

主要用途：
  - MXF -> MOV（专业剪辑素材，跳过 MXF 的元数据/时间码数据轨）
  - HEVC 标签自动修正（hev1 -> hvc1），解决 macOS「快速查看」无法预览
  - MKV -> MP4 等任意视频容器互转

特性：
  - 图形界面（tkinter，无需第三方库）
  - 只拷贝音视频流，画质零损失，速度快
  - 临时文件 + 验证 + 原子替换，单个失败不影响其他文件
  - 自动探测 ffmpeg（PATH / WorkBuddy 内置 / 常见安装路径）
  - 断点友好的线程化处理，UI 不卡顿

用法：
  python3 remux_gui.py
"""

import os
import re
import subprocess
import threading
import queue
import shutil
import sys
import glob
import traceback
import tempfile
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ----------------------------- 配置 -----------------------------

# 需要扫描的视频扩展名（小写）
VIDEO_EXTS = {
    ".mp4", ".mov", ".mxf", ".mkv", ".avi", ".ts", ".mts", ".m2ts",
    ".m4v", ".webm", ".flv", ".wmv", ".mpg", ".mpeg", ".vob", ".ogv",
    ".3gp", ".divx", ".f4v", ".rmvb", ".rm", ".dat", ".dv", ".hevc", ".h264",
}

# 目标容器 -> (ffmpeg muxer 名, 输出扩展名, 常用视频标签修正)
CONTAINERS = {
    "MOV": {"muxer": "mov", "ext": ".mov"},
    "MP4": {"muxer": "mp4", "ext": ".mp4"},
    "MKV": {"muxer": "matroska", "ext": ".mkv"},
}

# 版本号（与 .app 的 Info.plist 保持一致）
APP_VERSION = "1.2.8"
APP_NAME = "视频批量重封装工具"

# 无窗口模式启动 ffmpeg（Windows 下隐藏控制台）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------- 崩溃日志 -----------------------------
# 任何未捕获异常（含子线程）都写到日志文件，避免"无声闪退"无从排查。
_CRASH_LOG = os.path.join(tempfile.gettempdir(), "remux_crash.log")
_FAULT_LOG = os.path.join(tempfile.gettempdir(), "remux_fault.log")


def _enable_faulthandler():
    """捕获 native 层致命信号(SIGSEGV/SIGABRT等)，把 Python 栈写入独立日志。
    这类崩溃绕开 sys.excepthook（如 Tcl/Tk 跨线程访问），必须有 faulthandler 才能定位。
    """
    try:
        import faulthandler
        fh = open(_FAULT_LOG, "a", encoding="utf-8")
        fh.write("\n===== faulthandler 启动 %s =====\n" % (
            __import__("datetime").datetime.now().isoformat(timespec="seconds")))
        fh.flush()
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        pass


def _crash_writer(tag):
    def _write(exc_type, exc, tb):
        try:
            with open(_CRASH_LOG, "a", encoding="utf-8") as f:
                f.write("\n===== %s | %s =====\n" % (
                    tag, __import__("datetime").datetime.now().isoformat(timespec="seconds")))
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
        # 保留默认行为（打印到 stderr），便于命令行调试
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass
    return _write


def install_crash_hooks():
    """安装主线程与子线程的未捕获异常钩子 + native 信号栈转储。"""
    _enable_faulthandler()
    sys.excepthook = _crash_writer("主线程未捕获异常")
    try:
        threading.excepthook = lambda args: _crash_writer("子线程未捕获异常")(
            args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


def log_crash(msg):
    """手动记录一条错误到崩溃日志。"""
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\n[%s] %s\n" % (
                __import__("datetime").datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


def resource_dir():
    """
    返回资源根目录（内置 ffmpeg 所在层的父目录）。
    - .app 内运行时由 launcher 设置 REMUX_RESOURCE_DIR 指向 Resources/
    - 直接运行 py 时回退到脚本所在目录
    """
    env = os.environ.get("REMUX_RESOURCE_DIR")
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.abspath(__file__))


def _verify_ffmpeg(path):
    """实际执行一次 ffmpeg -version，确认候选可用（能跑通才算数）。"""
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return False
    try:
        proc = subprocess.run(
            [path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW, timeout=10,
        )
        return proc.returncode == 0 and b"ffmpeg version" in proc.stdout
    except Exception:
        return False


def find_ffmpeg():
    """
    按优先级探测 ffmpeg，并对每个候选做真实执行验证。
    优先级：
      1. 工具自身 bin/ 目录（随工具打包，离线可用）—— 核心
      2. 系统 PATH
      3. WorkBuddy 内置
      4. 常见安装路径
    返回 (path, is_bundled)；找不到返回 (None, False)。
    """
    home = os.path.expanduser("~")
    # 资源根目录（.app 内由 launcher 指定，否则脚本目录）
    tool_dir = resource_dir()

    candidates = []
    # 1. 随工具打包的 ffmpeg（最重要：无 ffmpeg / 无网络时靠它）
    candidates += [
        os.path.join(tool_dir, "bin", "ffmpeg"),
        os.path.join(tool_dir, "bin", "ffmpeg.exe"),  # Windows 场景预留
    ]
    # 2. PATH
    p = shutil.which("ffmpeg")
    if p:
        candidates.append(p)
    # 3. WorkBuddy 内置
    candidates += sorted(glob.glob(
        os.path.join(home, ".workbuddy", "binaries", "python", "versions", "*", "bin", "ffmpeg")
    ), reverse=True)
    # 4. 常见路径
    candidates += [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        os.path.join(home, "bin", "ffmpeg"),
    ]

    seen = set()
    for c in candidates:
        real = os.path.realpath(c) if c else c
        if not c or c in seen or real in seen:
            continue
        seen.add(c); seen.add(real)
        if _verify_ffmpeg(c):
            bundled = os.path.normpath(c).startswith(os.path.normpath(tool_dir))
            return c, bundled
    return None, False


def human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def probe_streams(ffmpeg, path):
    """
    用 ffmpeg -i 探测流信息（不解码、只读取头部，速度快）。
    返回 dict：
      duration_sec, video_codec, video_tag, width, height,
      is_hevc, is_hev1, audio_streams, video_streams
    探测失败返回 None。
    """
    cmd = [ffmpeg, "-hide_banner", "-i", path]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW, timeout=60,
        )
    except Exception:
        return None
    text = proc.stderr.decode("utf-8", "replace") + proc.stdout.decode("utf-8", "replace")

    duration = None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        duration = h * 3600 + mi * 60 + s

    video_codec = None
    video_tag = None
    width = height = None
    is_hev1 = False
    video_streams = 0
    audio_streams = 0

    for line in text.splitlines():
        if "Stream #" not in line:
            continue
        if "Video:" in line:
            video_streams += 1
            if video_codec is None:  # 取第一条视频轨作为代表
                vm = re.search(r"Video:\s*([a-zA-Z0-9_]+)", line)
                if vm:
                    video_codec = vm.group(1).lower()
                tm = re.search(r"\(([a-z0-9]{4})\s*/\s*0x", line)
                if tm:
                    video_tag = tm.group(1).lower()
                dm = re.search(r"(\d{3,5})x(\d{3,5})", line)
                if dm:
                    width, height = int(dm.group(1)), int(dm.group(2))
                if "hev1" in line.lower():
                    is_hev1 = True
        elif "Audio:" in line:
            audio_streams += 1

    if video_streams == 0 and audio_streams == 0:
        return None

    return {
        "duration": duration,
        "video_codec": video_codec,
        "video_tag": video_tag,
        "width": width,
        "height": height,
        "is_hevc": video_codec in ("hevc", "h265"),
        "is_hev1": is_hev1,
        "video_streams": video_streams,
        "audio_streams": audio_streams,
    }


def build_remux_cmd(ffmpeg, src, dst, container, fix_hev1, is_hev1=False):
    """
    构造无损重封装命令。
    -map 0:v:0 -map 0:a? 只取第一条视频轨 + 所有音轨，
    避免把 MXF 的元数据/时间码数据轨带进 MOV/MP4 导致失败。
    不使用 moov 前置(faststart)：它会在写完后把整个文件再读写一遍，
    在网络存储（NAS/Xsan）上会让耗时成倍增加。本地/网络存储均走同一最快路径。
    """
    muxer = CONTAINERS[container]["muxer"]
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-map", "0:v:0",       # 主视频轨
        "-map", "0:a?",        # 所有音频轨（不存在则跳过）
        "-c", "copy",          # 无损：直接拷贝码流
        "-map_metadata", "0",  # 保留元数据
        "-f", muxer,           # 显式指定封装格式（不依赖扩展名）
    ]
    # MOV/MP4：把全部音轨标为默认/启用。
    # ffmpeg 默认只给第一条音频写 default=1，Premiere/部分播放器会因此只显示/选中一条音轨；
    # MXF 原片的多条 mono 音轨在 MOV 中应保持全部可见，所以这里显式设置所有音频轨为 default。
    # MKV 不改，避免多个同类型 default 轨影响播放器的自动选轨逻辑。
    if muxer in ("mov", "mp4"):
        cmd += ["-disposition:a", "1"]
    # 仅当源视频是 HEVC 且带 hev1 标签时才修正为 hvc1。
    # 绝不能对 ProRes/DNxHD/H.264 等编码强制打 hvc1，否则文件损坏。
    if fix_hev1 and is_hev1 and muxer in ("mov", "mp4"):
        cmd += ["-tag:v", "hvc1"]
    cmd.append(dst)
    return cmd


class RemuxApp:
    def __init__(self, root, ffmpeg_path):
        self.root = root
        self.ffmpeg = ffmpeg_path
        self.scan_results = []   # list of dict
        # 格式筛选：ext(小写) -> BooleanVar；None 表示尚未按格式过滤（默认全选）
        self.ext_vars = {}
        self.running = False
        self.scanning = False
        self.cancel_flag = threading.Event()
        self.scan_cancel = threading.Event()
        self.msg_queue = queue.Queue()

        root.title(f"{APP_NAME}  v{APP_VERSION}  ·  Video Batch Remuxer")
        root.geometry("980x720")
        root.minsize(860, 620)

        # macOS 应用菜单 + 「关于」对话框
        self._build_app_menu(root)

        self.style = ttk.Style()
        # 尽量使用系统原生主题
        try:
            available = self.style.theme_names()
            for pref in ("aqua", "clam", "vista", "default"):
                if pref in available:
                    self.style.theme_use(pref)
                    break
        except Exception:
            pass

        self.src_var = tk.StringVar()
        self.dst_var = tk.StringVar()
        self.container_var = tk.StringVar(value="MOV")
        self.recursive_var = tk.BooleanVar(value=True)
        self.keep_tree_var = tk.BooleanVar(value=True)
        self.fix_hev1_var = tk.BooleanVar(value=True)
        self.skip_existing_var = tk.BooleanVar(value=True)
        self.overwrite_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._poll_queue()
        src_note = "（工具内置·离线可用）" if self._is_bundled(self.ffmpeg) else "（系统/已安装）"
        self._log(f"已加载 ffmpeg{src_note}：{self.ffmpeg}")
        self._log("请选择源文件夹和目标格式，然后扫描。")

    def _build_app_menu(self, root):
        """构建应用菜单（含关于对话框）。"""
        try:
            menubar = tk.Menu(root)
            # 在 macOS 上，第一个菜单会成为应用菜单
            app_menu = tk.Menu(menubar, tearoff=0)
            app_menu.add_command(label=f"关于 {APP_NAME}", command=self._show_about)
            app_menu.add_separator()
            app_menu.add_command(label="退出", command=root.destroy)
            menubar.add_cascade(label=APP_NAME, menu=app_menu)

            help_menu = tk.Menu(menubar, tearoff=0)
            help_menu.add_command(label=f"关于 {APP_NAME}", command=self._show_about)
            menubar.add_cascade(label="帮助", menu=help_menu)
            root.config(menu=menubar)
        except Exception:
            pass

    @staticmethod
    def _show_about():
        messagebox.showinfo(
            "关于",
            f"{APP_NAME}\n"
            f"Video Batch Remuxer\n\n"
            f"版本：v{APP_VERSION}\n"
            f"功能：批量无损重封装视频（MXF→MOV / HEVC 标签修复 / MKV→MP4）\n\n"
            f"完全离线运行 · ffmpeg 已内置",
        )

    @staticmethod
    def _is_bundled(path):
        tool_dir = resource_dir()
        return os.path.normpath(path).startswith(os.path.normpath(tool_dir))

    # ------------------------- UI -------------------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 5}

        # -- 路径选择 --
        path_frame = ttk.LabelFrame(self.root, text="① 选择路径")
        path_frame.pack(fill="x", **pad)

        ttk.Label(path_frame, text="源文件夹：").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.src_entry = ttk.Entry(path_frame, textvariable=self.src_var, width=76)
        self.src_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(path_frame, text="浏览…", command=self._pick_src).grid(row=0, column=2, padx=6)

        ttk.Label(path_frame, text="目标文件夹：").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.dst_entry = ttk.Entry(path_frame, textvariable=self.dst_var, width=76)
        self.dst_entry.grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(path_frame, text="浏览…", command=self._pick_dst).grid(row=1, column=2, padx=6)

        hint = ttk.Label(path_frame, text="提示：可直接把文件夹拖入上方路径栏", foreground="#888")
        hint.grid(row=2, column=1, sticky="w", padx=4, pady=(2, 4))

        path_frame.columnconfigure(1, weight=1)

        # 启用原生拖放（在不阻塞 UI 的情况下，失败也不影响主功能）
        self.root.after(150, self._setup_drop)

        # -- 选项 --
        opt_frame = ttk.LabelFrame(self.root, text="② 转换选项")
        opt_frame.pack(fill="x", **pad)

        ttk.Label(opt_frame, text="目标格式：").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        fmt_cb = ttk.Combobox(
            opt_frame, textvariable=self.container_var,
            values=list(CONTAINERS.keys()), state="readonly", width=10,
        )
        fmt_cb.grid(row=0, column=1, sticky="w", padx=4)
        fmt_cb.bind("<<ComboboxSelected>>", lambda e: self._on_container_change())

        ttk.Checkbutton(opt_frame, text="扫描子文件夹", variable=self.recursive_var).grid(
            row=0, column=2, sticky="w", padx=14)
        ttk.Checkbutton(opt_frame, text="保留子目录层级", variable=self.keep_tree_var).grid(
            row=0, column=3, sticky="w", padx=14)
        ttk.Checkbutton(opt_frame, text="修复 HEVC 标签(hvc1)", variable=self.fix_hev1_var).grid(
            row=1, column=2, sticky="w", padx=14, pady=6)
        ttk.Checkbutton(opt_frame, text="跳过已存在文件", variable=self.skip_existing_var).grid(
            row=1, column=3, sticky="w", padx=14, pady=6)

        self.hint_var = tk.StringVar(value="MOV 输出：*.mov")
        ttk.Label(opt_frame, textvariable=self.hint_var, foreground="#555").grid(
            row=0, column=4, sticky="w", padx=14)

        # -- 操作按钮 --
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self.scan_btn = ttk.Button(btn_frame, text="扫描文件", command=self._scan)
        self.scan_btn.pack(side="left", padx=5)
        self.start_btn = ttk.Button(btn_frame, text="开始转换", command=self._start)
        self.start_btn.pack(side="left", padx=5)
        self.cancel_btn = ttk.Button(btn_frame, text="取消", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=5)
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side="left", padx=5)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(btn_frame, textvariable=self.status_var, foreground="#0a6").pack(side="right", padx=10)

        # -- 文件列表 --
        list_frame = ttk.LabelFrame(self.root, text="③ 待转换文件")
        list_frame.pack(fill="both", expand=True, padx=8, pady=5)

        # 格式筛选栏（扫描完成后按实际扩展名动态生成）
        filter_frame = ttk.Frame(list_frame)
        filter_frame.pack(fill="x", padx=4, pady=(4, 2))
        ttk.Label(filter_frame, text="格式筛选：").pack(side="left")
        self.filter_box = ttk.Frame(filter_frame)
        self.filter_box.pack(side="left", fill="x", expand=True)
        ttk.Button(filter_frame, text="全选", width=6,
                   command=lambda: self._set_all_filter(True)).pack(side="left", padx=2)
        ttk.Button(filter_frame, text="全不选", width=8,
                   command=lambda: self._set_all_filter(False)).pack(side="left", padx=2)
        self.filter_count_var = tk.StringVar(value="")
        ttk.Label(filter_frame, textvariable=self.filter_count_var,
                  foreground="#555", width=20).pack(side="left", padx=(8, 2))

        cols = ("sel", "file", "codec", "resolution", "duration", "size", "status")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=10)
        headings = {
            "sel": ("选", 42), "file": ("文件", 350), "codec": ("视频编码", 120),
            "resolution": ("分辨率", 100), "duration": ("时长", 85),
            "size": ("大小", 85), "status": ("状态", 88),
        }
        for c in cols:
            self.tree.heading(c, text=headings[c][0])
            self.tree.column(c, width=headings[c][1], anchor="w",
                             stretch=(c in ("file",)))
        self.tree.column("sel", anchor="center", stretch=False)
        # 单击"选"列切换勾选；单击用 <ButtonRelease-1> 避免与默认选中冲突
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        vs = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        vs.pack(side="right", fill="y", pady=4)

        # -- 进度条 --
        prog_frame = ttk.Frame(self.root)
        prog_frame.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(prog_frame, mode="determinate", value=0, maximum=100)
        self.progress.pack(fill="x", side="left", expand=True, padx=(0, 8))
        self.prog_label = ttk.Label(prog_frame, text="0%", width=6)
        self.prog_label.pack(side="right")

        # -- 日志 --
        log_frame = ttk.LabelFrame(self.root, text="日志")
        log_frame.pack(fill="both", expand=True, padx=8, pady=5)
        self.log = scrolledtext.ScrolledText(log_frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    def _on_container_change(self):
        ext = CONTAINERS[self.container_var.get()]["ext"]
        self.hint_var.set(f"输出：*{ext}")

    def _pick_src(self):
        d = filedialog.askdirectory(title="选择源文件夹")
        if d:
            self.src_var.set(d)
            if not self.dst_var.get():
                self.dst_var.set(os.path.join(os.path.dirname(d.rstrip("/\\")), "remuxed"))

    def _pick_dst(self):
        d = filedialog.askdirectory(title="选择目标文件夹")
        if d:
            self.dst_var.set(d)

    def _normalize_dropped(self, path):
        """拖入项若是文件则取其所在目录；规范化路径。"""
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            path = os.path.dirname(path)
        return path

    def _setup_drop(self):
        """为源/目标路径栏启用 macOS 原生拖放。"""
        try:
            import drop_target
        except Exception as e:
            self._log(f"（拖放不可用：{e}）")
            return

        def make_drop(var):
            def cb(path):
                # 该回调在 AppKit/ctypes 的【临时 tstate】里执行。绝对不能在这里
                # 调用 root.after()/任何 Tk() 方法——Tk 命令会绑定临时 tstate，
                # 回调返回后 tstate 被销毁，Tcl 执行排队命令时
                # PyEval_RestoreThread 用悬空 tstate -> native SIGSEGV。
                # 只做纯数据计算并入队，UI 更新交给主线程 _poll_queue。
                try:
                    p = self._normalize_dropped(path)
                    self.msg_queue.put(("setvar", var, p))
                except Exception:
                    pass
            return cb

        def make_click(entry):
            # 同理：mouseDown 原生回调也在临时 tstate 里，UI 操作一律入队。
            return lambda: self.msg_queue.put(("focus", entry))

        ok1 = drop_target.enable_drop(
            self.root, self.src_entry, make_drop(self.src_var), make_click(self.src_entry)
        )
        ok2 = drop_target.enable_drop(
            self.root, self.dst_entry, make_drop(self.dst_var), make_click(self.dst_entry)
        )
        if ok1 and ok2:
            self._log("拖放已启用：可把文件夹直接拖入路径栏。")

    # ------------------------- 日志 -------------------------
    def _log(self, msg):
        self.msg_queue.put(("log", msg))

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _poll_queue(self):
        """主线程轮询队列，安全更新 UI。"""
        try:
            while True:
                item = self.msg_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", item[1] + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "setvar":
                    # 主线程安全更新路径栏（来自原生拖放回调）
                    item[1].set(item[2])
                elif kind == "focus":
                    # 主线程安全聚焦输入框（来自原生 mouseDown 回调）
                    item[1].focus_set()
                elif kind == "progress":
                    pct = item[1]
                    self.progress["value"] = pct
                    self.prog_label.configure(text=f"{int(pct)}%")
                elif kind == "tree":
                    iid, status = item[1], item[2]
                    if self.tree.exists(iid):
                        vals = list(self.tree.item(iid, "values"))
                        vals[6] = status  # 状态列（"选"列插入后索引为 6）
                        self.tree.item(iid, values=vals)
                elif kind == "worker_done":
                    # 转换线程结束，回到主线程恢复按钮（子线程禁止直接操作 Tk）
                    self._on_worker_done()
                elif kind == "scan_row":
                    self._append_scan_row(item[1])
                elif kind == "scan_result":
                    collected = item[1]
                    self.status_var.set("扫描完成：%d 个文件" % len(collected))
                    self._log("扫描完成，共 %d 个可转换文件。" % len(collected))
                    if not collected:
                        self._log("没有找到可转换的视频文件。")
                    self._on_scan_done()
        except queue.Empty:
            pass
        except Exception:
            # 轮询循环绝不能因单次 UI 更新异常而停摆
            log_crash("_poll_queue 异常:\n" + traceback.format_exc())
        self.root.after(120, self._poll_queue)

    # ------------------------- 扫描 -------------------------
    def _scan(self):
        src = self.src_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showwarning("提示", "请先选择有效的源文件夹。")
            return
        if self.scanning:
            return

        self._clear_log()
        for row in self.tree.get_children():
            self.tree.delete(row)
        for w in self.filter_box.winfo_children():
            w.destroy()
        self.ext_vars = {}
        self.filter_count_var.set("")
        self.scan_results = []
        self._log(f"开始扫描：{src}")

        # 收集文件路径（只读目录项，很快；NAS 上也很少阻塞）
        recursive = self.recursive_var.get()
        files = []
        try:
            if recursive:
                for root, dirs, names in os.walk(src):
                    for n in names:
                        if Path(n).suffix.lower() in VIDEO_EXTS:
                            files.append(os.path.join(root, n))
            else:
                for n in os.listdir(src):
                    fp = os.path.join(src, n)
                    if os.path.isfile(fp) and Path(n).suffix.lower() in VIDEO_EXTS:
                        files.append(fp)
        except Exception as e:
            self._log(f"  [错误] 遍历文件夹失败：{e}")
            log_crash("_scan 遍历失败: " + traceback.format_exc())
            return

        files.sort()
        self._log(f"发现 {len(files)} 个视频文件，正在读取编码信息…")
        if not files:
            self.status_var.set("扫描完成：0 个文件")
            self._log("没有找到可转换的视频文件。")
            return

        # 探测每个文件的编码较慢（NAS 上尤其慢），放到后台线程，界面不冻结
        self.scanning = True
        self.scan_cancel.clear()
        self.scan_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.status_var.set("扫描中… 0/%d" % len(files))
        threading.Thread(target=self._scan_worker, args=(src, files), daemon=True).start()

    def _scan_worker(self, src, files):
        """后台线程：逐个探测编码，结果通过队列回报，绝不直接操作 Tk。"""
        total = len(files)
        collected = []
        try:
            for i, fp in enumerate(files, 1):
                if self.scan_cancel.is_set():
                    self.msg_queue.put(("log", "扫描已取消。"))
                    break
                self.msg_queue.put(("status", "扫描中… %d/%d" % (i, total)))
                info = probe_streams(self.ffmpeg, fp)
                if info is None:
                    self.msg_queue.put(("log", "  [跳过] 无法识别：%s" % os.path.basename(fp)))
                    continue
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = 0
                try:
                    rel = os.path.relpath(fp, src)
                except ValueError:
                    rel = os.path.basename(fp)
                info.update({"path": fp, "rel": rel, "size": size})
                collected.append(info)
                self.msg_queue.put(("scan_row", info))
        except Exception:
            log_crash("_scan_worker 异常:\n" + traceback.format_exc())
            self.msg_queue.put(("log", "  [错误] 扫描过程出现异常，详见崩溃日志。"))
        finally:
            self.msg_queue.put(("scan_result", collected))

    def _on_scan_done(self):
        self.scanning = False
        self._build_ext_filter()
        self.scan_btn.configure(state="normal")
        self.start_btn.configure(state="normal")

    def _append_scan_row(self, info):
        """在主线程把单条扫描结果插入列表。"""
        codec = (info["video_codec"] or "?").upper()
        if info["is_hev1"]:
            codec += " (hev1→hvc1)"
        dur = self._fmt_duration(info["duration"])
        res = f"{info['width']}x{info['height']}" if info["width"] else "-"
        iid = self.tree.insert("", "end", values=(
            "☑", info["rel"], codec, res, dur, human_size(info["size"]), "待转换"
        ))
        info["iid"] = iid
        info["ext"] = os.path.splitext(info["path"])[1].lower()
        info["row_enabled"] = True   # 逐行勾选开关（与格式勾选独立）
        info["enabled"] = True       # 最终结果 = 格式勾选 AND row_enabled
        self.scan_results.append(info)

    @staticmethod
    def _fmt_duration(sec):
        if sec is None:
            return "-"
        sec = int(sec)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # ------------------------- 格式筛选 -------------------------
    def _build_ext_filter(self):
        """扫描完成后，按实际扫到的扩展名生成筛选复选框（默认全选）。"""
        for w in self.filter_box.winfo_children():
            w.destroy()
        self.ext_vars = {}
        exts = sorted({info["ext"] for info in self.scan_results if info.get("ext")})
        if not exts:
            self.filter_count_var.set("")
            return
        for ext in exts:
            var = tk.BooleanVar(value=True)
            self.ext_vars[ext] = var
            ttk.Checkbutton(
                self.filter_box, text=ext.lstrip(".").upper(), variable=var,
                command=self._apply_filter,
            ).pack(side="left", padx=(0, 8))
        self._apply_filter()

    def _refresh_rows(self):
        """把每行的 enabled 结果同步到 '选' 列与状态列。"""
        for info in self.scan_results:
            iid = info.get("iid")
            if iid and self.tree.exists(iid):
                vals = list(self.tree.item(iid, "values"))
                vals[0] = "☑" if info.get("enabled", True) else "☐"
                # 只改尚未开始转换的行的状态文字，不覆盖已转换结果
                if vals[6] in ("待转换", "已排除"):
                    vals[6] = "待转换" if info.get("enabled", True) else "已排除"
                self.tree.item(iid, values=vals)
        self._update_filter_count()

    def _apply_filter(self):
        """最终 enabled = 格式勾选 AND 逐行勾选，两者互不覆盖。"""
        for info in self.scan_results:
            var = self.ext_vars.get(info.get("ext", ""))
            ext_ok = bool(var.get()) if var is not None else True
            info["enabled"] = ext_ok and info.get("row_enabled", True)
        self._refresh_rows()

    def _update_filter_count(self):
        enabled = sum(1 for info in self.scan_results if info.get("enabled", True))
        total = len(self.scan_results)
        self.filter_count_var.set(f"选中 {enabled}/{total} 个")

    def _set_all_filter(self, value):
        for var in self.ext_vars.values():
            var.set(value)
        self._apply_filter()

    def _on_tree_click(self, event):
        """单击 '选' 列（第 0 列）切换单个文件的勾选。"""
        if self.running or self.scanning:
            return
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != "#1":  # 第一列 = sel
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        for info in self.scan_results:
            if info.get("iid") == iid:
                # 逐行勾选与格式勾选是两道独立的开关，不能互相覆盖
                info["row_enabled"] = not info.get("row_enabled", True)
                self._apply_filter()
                break

    # ------------------------- 转换 -------------------------
    def _start(self):
        if self.running:
            return
        if not self.scan_results:
            messagebox.showwarning("提示", "请先扫描文件。")
            return
        dst = self.dst_var.get().strip()
        if not dst:
            messagebox.showwarning("提示", "请选择目标文件夹。")
            return

        container = self.container_var.get()
        ext = CONTAINERS[container]["ext"]
        src = self.src_var.get().strip()

        # 计算每个文件的目标路径，预先过滤
        jobs = []
        skipped_existing = 0
        skipped_filtered = 0
        for info in self.scan_results:
            # 格式筛选/逐行勾选未选中的文件不参与转换
            if not info.get("enabled", True):
                skipped_filtered += 1
                continue
            rel = info["rel"]
            if self.keep_tree_var.get():
                out_rel = os.path.splitext(rel)[0] + ext
            else:
                out_rel = os.path.splitext(os.path.basename(rel))[0] + ext
            dst_path = os.path.join(dst, out_rel)

            # 防止源=目标同一路径
            if os.path.abspath(dst_path) == os.path.abspath(info["path"]):
                self._log(f"  [跳过] 源与目标相同：{rel}")
                self._mark(info["iid"], "跳过")
                continue
            if self.overwrite_var.get() is False and self.skip_existing_var.get() and os.path.exists(dst_path):
                skipped_existing += 1
                self._log(f"  [跳过] 目标已存在：{out_rel}")
                self._mark(info["iid"], "已存在")
                continue
            jobs.append((info, dst_path, out_rel))

        if skipped_filtered:
            self._log(f"  [筛选] 跳过未勾选的文件：{skipped_filtered} 个")
        if not jobs:
            messagebox.showinfo("提示", "没有需要转换的文件（请检查格式筛选/勾选）。")
            return

        self.running = True
        self.cancel_flag.clear()
        self.scan_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0
        self.prog_label.configure(text="0%")
        self._log(f"开始转换 {len(jobs)} 个文件 → {container} ({ext})")

        # 关键：所有 Tk 变量必须在主线程读取成普通值再传给 worker。
        # tkinter 的 Variable.get() 会进入 Tcl 解释器，而 Tcl/Tk 不是线程安全的：
        # 从子线程调用会导致 native 崩溃(SIGSEGV)，sys.excepthook 根本抓不到。
        # worker  thread 内不得出现任何 .get()/Tk 方法调用。
        fix_hev1 = self.fix_hev1_var.get()

        t = threading.Thread(
            target=self._worker, args=(jobs, container, fix_hev1), daemon=True
        )
        t.start()

    def _mark(self, iid, status):
        self.msg_queue.put(("tree", iid, status))

    def _worker(self, jobs, container, fix_hev1):
        """后台转换线程。禁止访问任何 tkinter 对象——状态一律用入参 + 队列传递。"""
        total = len(jobs)
        ok = fail = 0
        try:
            for idx, (info, dst_path, out_rel) in enumerate(jobs, 1):
                if self.cancel_flag.is_set():
                    self._log("用户取消，停止转换。")
                    break

                # 单个文件的全部处理都包在 try 内：任何一个文件出错都不影响后续文件，
                # 更不会让异常冒到线程顶层导致进程崩溃。
                tmp_path = None
                try:
                    pct = (idx - 1) / total * 100
                    self.msg_queue.put(("progress", pct))
                    self.msg_queue.put(("status", f"正在转换 {idx}/{total}"))
                    self._log(f"[{idx}/{total}] {info['rel']}")
                    self._mark(info["iid"], "转换中…")

                    # 网络路径（NAS）上创建目录可能失败，单独捕获
                    try:
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    except Exception as e:
                        self._log(f"  [失败] 无法创建输出目录：{e}")
                        self._mark(info["iid"], "✗ 失败")
                        log_crash("makedirs 失败 %s: %s" % (dst_path, traceback.format_exc()))
                        fail += 1
                        continue

                    # 关键：临时文件必须保留目标容器扩展名，否则 ffmpeg 无法确定 muxer。
                    base, ext = os.path.splitext(dst_path)
                    tmp_path = base + ".remux" + ext

                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

                    cmd = build_remux_cmd(
                        self.ffmpeg, info["path"], tmp_path, container,
                        fix_hev1, info.get("is_hev1", False),
                    )
                    try:
                        proc = subprocess.run(
                            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW,
                        )
                        stderr = proc.stderr.decode("utf-8", "replace").strip()
                    except Exception as e:
                        proc = None
                        stderr = str(e)

                    if self.cancel_flag.is_set():
                        if tmp_path and os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                        self._log("已取消，清理临时文件。")
                        break

                    # 验证：临时文件存在且非空且能被识别
                    valid = False
                    if (proc is not None and proc.returncode == 0
                            and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0):
                        v = probe_streams(self.ffmpeg, tmp_path)
                        if v and (v["video_streams"] > 0 or v["audio_streams"] > 0):
                            if (fix_hev1 and container in ("MOV", "MP4")
                                    and info["is_hev1"]):
                                if v.get("video_tag") == "hvc1":
                                    valid = True
                                else:
                                    self._log(f"  [警告] hvc1 标签校验未通过（实际 {v.get('video_tag')}）")
                            else:
                                valid = True

                    if valid:
                        # NAS 上 os.replace 可能因权限/网络中断失败：先尝试原子替换，
                        # 失败则退化为「删除目标 + 复制」，再失败则记为该文件失败。
                        replaced = False
                        try:
                            os.replace(tmp_path, dst_path)
                            replaced = True
                        except Exception:
                            try:
                                if os.path.exists(dst_path):
                                    os.remove(dst_path)
                                shutil.copy2(tmp_path, dst_path)
                                os.remove(tmp_path)
                                replaced = True
                            except Exception as e2:
                                log_crash("落盘失败 %s: %s" % (dst_path, traceback.format_exc()))
                                self._log(f"  [失败] 写入目标失败：{e2}")
                        if replaced:
                            self._log(f"  [成功] → {out_rel}  ({human_size(os.path.getsize(dst_path))})")
                            self._mark(info["iid"], "✓ 成功")
                            ok += 1
                        else:
                            self._mark(info["iid"], "✗ 失败")
                            fail += 1
                    else:
                        self._log(f"  [失败] {info['rel']}  {stderr[:300]}")
                        self._mark(info["iid"], "✗ 失败")
                        fail += 1

                except Exception:
                    # 兜底：任何未预料的异常都只记日志，不让它冲出 for 循环
                    log_crash("单文件处理异常 %s:\n%s" % (info.get("rel"), traceback.format_exc()))
                    self._log(f"  [失败] {info['rel']}  处理异常（详见崩溃日志）")
                    self._mark(info["iid"], "✗ 失败")
                    fail += 1
                finally:
                    # 清理可能残留的临时文件（成功路径下它已被 replace/copy 移走，不会存在）
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
        except Exception:
            log_crash("_worker 顶层异常:\n" + traceback.format_exc())
        finally:
            self.msg_queue.put(("progress", 100))
            self.msg_queue.put(("status", "完成"))
            self._log(f"===== 完成：成功 {ok}，失败 {fail} =====")
            self.running = False
            # 关键：子线程绝不直接操作 Tk，改用队列消息回到主线程恢复按钮
            self.msg_queue.put(("worker_done",))

    def _on_worker_done(self):
        self.scan_btn.configure(state="normal")
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

    def _cancel(self):
        if self.running:
            self.cancel_flag.set()
            self.cancel_btn.configure(state="disabled")
            self._log("正在取消…")
        elif self.scanning:
            self.scan_cancel.set()
            self._log("正在取消扫描…")


def main():
    install_crash_hooks()  # 未捕获异常写日志，避免无声闪退无从排查
    ffmpeg, bundled = find_ffmpeg()
    root = tk.Tk()
    root.withdraw()  # 探测阶段先隐藏窗口

    if ffmpeg is None:
        messagebox.showerror(
            "缺少 ffmpeg",
            "未找到可用的 ffmpeg。\n\n"
            "本工具已随附 ffmpeg，但当前环境无法执行它。常见原因：\n"
            "  1) 随附的 ffmpeg 是 x86_64 版，而当前是 Apple Silicon 且未安装 Rosetta 2；\n"
            "  2) 文件损坏或被系统安全策略拦截。\n\n"
            "解决办法：\n"
            "  · Apple Silicon 且无网络：请先在有网机器上装好 Rosetta 2，或改用 arm64 版 ffmpeg；\n"
            "  · 或将可用的 ffmpeg 放到本工具目录的 bin/ 文件夹下。",
        )
        root.destroy()
        sys.exit(1)

    # 架构兼容性提示：随附的是 x86_64 二进制，在 Apple Silicon 上需 Rosetta
    try:
        machine = os.uname().machine  # macOS: arm64 / x86_64
    except Exception:
        machine = ""
    if bundled and machine == "arm64":
        # 尝试判断 x86_64 二进制是否真能在该机器运行（有 Rosetta 则 -version 已通过）
        messagebox.showinfo(
            "提示",
            "检测到当前是 Apple Silicon 芯片。\n\n"
            "本工具随附的是 x86_64 版 ffmpeg，已验证可运行（系统已装 Rosetta 2）。\n"
            "若换到未装 Rosetta 2 的 Mac，请把 arm64 版 ffmpeg 放入 bin/ 目录替换即可。",
        )

    root.deiconify()
    app = RemuxApp(root, ffmpeg)
    # macOS 下 Command+Q / 关闭窗口
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
