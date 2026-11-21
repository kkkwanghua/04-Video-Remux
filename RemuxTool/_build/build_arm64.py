#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 arm64 (Apple Silicon) 版「视频重封装工具.app」——完全自包含、离线。

与 x86 版(copy Python 3.13.12)不同，本脚本：
  - Python 运行时来自 python-build-standalone 的 aarch64-apple-darwin 3.13.x 包
  - ffmpeg 来自 imageio-ffmpeg 的 macOS arm64 wheel
  - 自动探测 Tcl/Tk 的真实目录名与 dylib 名（不写死 tcl9.0）
  - pyc 用本机 x86 Python 编译（3.13 系列 magic 一致，arm64 可直接加载）
  - 所有二进制用 macho_arch.py 校验为 arm64 后才打包

用法：
  python3 build_arm64.py <解压后的python根目录> <arm64_ffmpeg路径> <icns路径>

输出：工作区根目录的「视频重封装工具-arm64.app」
"""
import os
import sys
import stat
import glob
import shutil
import py_compile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(TOOL)

APP_NAME = "视频重封装工具"
APP_PATH = os.path.join(WORKSPACE, "视频重封装工具-arm64.app")
CONTENTS = os.path.join(APP_PATH, "Contents")
MACOS = os.path.join(CONTENTS, "MacOS")
RESOURCES = os.path.join(CONTENTS, "Resources")
RUNTIME = os.path.join(RESOURCES, "runtime")
APP_CODE = os.path.join(RESOURCES, "app")
APP_BIN = os.path.join(RESOURCES, "bin")
MACHO_TOOL = os.path.join(HERE, "macho_arch.py")

# 从主程序读版本号（单一来源）
sys.path.insert(0, TOOL)
import remux_gui as mod
VERSION = mod.APP_VERSION

# stdlib 精简剔除
PRUNE_NAMES = {
    "site-packages", "ensurepip", "idlelib", "test", "turtledemo",
    "pydoc_data", "lib2to3", "__pycache__",
}


def run(cmd, check=True):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and r.returncode != 0:
        print("CMD FAIL:", " ".join(str(c) for c in cmd))
        print(r.stdout[-800:])
        print(r.stderr[-800:])
        raise SystemExit(1)
    return r


def arch_of(path):
    """返回 Mach-O 架构字符串（arm64/x86_64/…）。"""
    r = run([sys.executable, MACHO_TOOL, path], check=False)
    if r.returncode != 0:
        return "ERR"
    return r.stdout.strip().split("\t")[0]


def find_python_root(extracted):
    """python-build-standalone 解压后通常是 <extracted>/python/。"""
    candidates = [extracted,
                  os.path.join(extracted, "python"),
                  os.path.join(extracted, "cpython")]
    for c in candidates:
        if os.path.isfile(os.path.join(c, "bin", "python3.13")):
            return c
    raise SystemExit(f"未找到 python3.13 可执行文件于 {candidates}")


def detect_tk_dirs(pyroot):
    """探测 tcl/tk 资源目录名与 dylib 名。"""
    libdir = os.path.join(pyroot, "lib")
    # tcl/tk 资源目录：形如 tcl8.6 / tk8.6 / tcl9.0 / tk9.0
    # 注意 tcl9 可能只是含 9.0/ 子目录的空壳，真正的 init.tcl 在 tcl9.0
    # 因此只保留含 init.tcl 的目录作为 TCL_LIBRARY
    all_tcl = sorted({os.path.basename(p) for p in glob.glob(os.path.join(libdir, "tcl[0-9]*"))
                      if os.path.isdir(p)})
    tcl_dirs = [d for d in all_tcl if os.path.isfile(os.path.join(libdir, d, "init.tcl"))]
    if not tcl_dirs:
        tcl_dirs = all_tcl  # 兜底
    tk_dirs = sorted({os.path.basename(p) for p in glob.glob(os.path.join(libdir, "tk[0-9]*"))
                      if os.path.isdir(p) and os.path.isfile(os.path.join(p, "tk.tcl"))})
    # dylib
    dylibs = [os.path.basename(p) for p in glob.glob(os.path.join(libdir, "*.dylib"))]
    # 选出 tkinter 依赖的 tcl/tk dylib（排除 libpython）
    tk_dylibs = [d for d in dylibs if d.startswith("libtcl") or d.startswith("libtk")]
    return tcl_dirs, tk_dirs, tk_dylibs


def write_launcher(tcl_dir, tk_dir):
    content = f'''#!/bin/bash
# {APP_NAME} v{VERSION} (Apple Silicon / arm64) — 自包含启动器（离线）
RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PYTHONHOME="$RESOURCES/runtime"
export REMUX_RESOURCE_DIR="$RESOURCES"
export TCL_LIBRARY="$RESOURCES/runtime/lib/{tcl_dir}"
export TK_LIBRARY="$RESOURCES/runtime/lib/{tk_dir}"
export PYTHONPATH="$RESOURCES/app"
exec "$RESOURCES/runtime/bin/python3.13" "$RESOURCES/app/remux_gui.pyc" "$@"
'''
    launcher = os.path.join(MACOS, "launcher")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IEXEC)


def write_info_plist():
    plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME} (arm64)</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME} (arm64)</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>appicon.icns</string>
    <key>CFBundleIdentifier</key>
    <string>local.remux.tool.arm64</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>{VERSION}</string>
    <key>CFBundleVersion</key>
    <string>{VERSION}</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
'''
    with open(os.path.join(CONTENTS, "Info.plist"), "w", encoding="utf-8") as f:
        f.write(plist)


def main():
    if len(sys.argv) < 4:
        raise SystemExit("用法: build_arm64.py <python解压根> <arm64_ffmpeg> <icns>")
    extracted, ffmpeg_src, icns_src = sys.argv[1], sys.argv[2], sys.argv[3]

    print(f"构建 {APP_NAME}-arm64.app  v{VERSION} (Apple Silicon)...")

    pyroot = find_python_root(extracted)
    print(f"  Python 根: {pyroot}")

    # 架构校验：Python 与 ffmpeg 必须是 arm64
    pybin = os.path.join(pyroot, "bin", "python3.13")
    print(f"  校验架构 python3.13: {arch_of(pybin)}")
    print(f"  校验架构 ffmpeg:     {arch_of(ffmpeg_src)}")
    assert arch_of(pybin) == "arm64", "python3.13 不是 arm64！"
    assert arch_of(ffmpeg_src) == "arm64", "ffmpeg 不是 arm64！"

    tcl_dirs, tk_dirs, tk_dylibs = detect_tk_dirs(pyroot)
    print(f"  探测 Tcl 目录: {tcl_dirs}")
    print(f"  探测 Tk  目录: {tk_dirs}")
    print(f"  探测 Tcl/Tk dylib: {tk_dylibs}")
    assert tcl_dirs and tk_dirs, "未探测到 tcl/tk 目录"
    tcl_dir, tk_dir = tcl_dirs[0], tk_dirs[0]

    # 清空旧 app
    if os.path.exists(APP_PATH):
        shutil.rmtree(APP_PATH)
    for d in (MACOS, RESOURCES, RUNTIME, APP_CODE, APP_BIN,
              os.path.join(RUNTIME, "bin"), os.path.join(RUNTIME, "lib")):
        os.makedirs(d, exist_ok=True)

    # 1. 编译主程序与依赖模块为 pyc（本机 x86 Python 编译，3.13 magic 通用）
    print("  [1/6] 编译主程序与 drop_target 为 .pyc ...")
    py_compile.compile(os.path.join(TOOL, "remux_gui.py"),
                       cfile=os.path.join(APP_CODE, "remux_gui.pyc"), doraise=True)
    drop_src = os.path.join(TOOL, "drop_target.py")
    if os.path.exists(drop_src):
        py_compile.compile(drop_src, cfile=os.path.join(APP_CODE, "drop_target.pyc"), doraise=True)

    # 2. 复制 Python 可执行文件与 dylib
    print("  [2/6] 复制 Python 可执行文件与 dylib ...")
    run(["cp", pybin, os.path.join(RUNTIME, "bin", "python3.13")])
    run(["chmod", "+x", os.path.join(RUNTIME, "bin", "python3.13")])
    # libpython
    libpython = os.path.join(pyroot, "lib", "libpython3.13.dylib")
    assert os.path.exists(libpython), "缺少 libpython3.13.dylib"
    run(["cp", libpython, os.path.join(RUNTIME, "lib", "libpython3.13.dylib")])
    # tcl/tk dylib
    for dylib in tk_dylibs:
        run(["cp", os.path.join(pyroot, "lib", dylib), os.path.join(RUNTIME, "lib", dylib)])

    # 3. rsync 复制精简 stdlib
    print("  [3/6] rsync 复制精简 stdlib ...")
    rsync_cmd = ["rsync", "-a"]
    for n in sorted(PRUNE_NAMES):
        rsync_cmd += ["--exclude", n]
    rsync_cmd += ["--exclude", "*.pyc",
                  os.path.join(pyroot, "lib", "python3.13") + "/",
                  os.path.join(RUNTIME, "lib", "python3.13") + "/"]
    run(rsync_cmd)

    # 4. 复制 tcl/tk 资源目录
    print("  [4/6] 复制 tcl/tk 资源目录 ...")
    all_tk_dirs = set(tcl_dirs) | set(tk_dirs)
    # 也带上 tcl9/tcl8 等无版本后缀目录（如存在）
    for p in glob.glob(os.path.join(pyroot, "lib", "tcl[0-9]*")) + \
             glob.glob(os.path.join(pyroot, "lib", "itcl*")) + \
             glob.glob(os.path.join(pyroot, "lib", "thread*")):
        if os.path.isdir(p):
            all_tk_dirs.add(os.path.basename(p))
    for d in sorted(all_tk_dirs):
        src = os.path.join(pyroot, "lib", d)
        if os.path.isdir(src):
            run(["rsync", "-a", src + "/", os.path.join(RUNTIME, "lib", d) + "/"])

    # 5. 复制 ffmpeg + 图标
    print("  [5/6] 复制 ffmpeg 与图标 ...")
    run(["cp", ffmpeg_src, os.path.join(APP_BIN, "ffmpeg")])
    run(["chmod", "+x", os.path.join(APP_BIN, "ffmpeg")])
    if os.path.exists(icns_src):
        run(["cp", icns_src, os.path.join(RESOURCES, "appicon.icns")])

    write_launcher(tcl_dir, tk_dir)
    write_info_plist()

    # 6. pyc 化 stdlib（零源码）—— 用本机 Python 编译 arm64 包里的 stdlib 源码
    print("  [6/6] stdlib pyc 化（零 .py）...")
    pycify = os.path.join(HERE, "pycify_arm64.py")
    run([sys.executable, pycify, os.path.join(RUNTIME, "lib", "python3.13")])

    # 说明文件
    readme = f"""{APP_NAME}  v{VERSION}  (Apple Silicon / arm64)

完全自包含的 macOS 应用，原生 arm64，无需安装 Python 或 ffmpeg，无需联网。

使用方法：
  双击打开 → 选择源文件夹 / 目标文件夹 → 选目标格式 → 扫描 → 开始转换。

功能：
  - 批量扫描视频文件，无损重封装到 MOV / MP4 / MKV
  - 保留子目录层级
  - MXF→MOV 专项优化（跳过时间码/元数据轨）
  - HEVC 自动修复 hvc1 标签，解决 Mac 空格预览问题
  - 路径栏支持直接拖放文件夹
  - 临时文件校验 + 原子替换，单文件失败不影响整体

架构：原生 Apple Silicon (arm64)，内置 arm64 Python 3.13 与 arm64 ffmpeg。
版本：v{VERSION}
"""
    with open(os.path.join(RESOURCES, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(readme)

    size = run(["du", "-sh", APP_PATH]).stdout.strip()
    print(f"BUILD_ARM64_OK  v{VERSION}  {APP_PATH}  {size}")


if __name__ == "__main__":
    main()
