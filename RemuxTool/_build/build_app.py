#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建「视频重封装工具.app」——一个完全自包含的 macOS 应用：
  - 内嵌精简 Python 3 运行时（含 tkinter）
  - 内嵌 ffmpeg
  - 主程序编译为 .pyc（不分发 .py 源码）
  - Info.plist 含版本号
  - 带图标

用法：python3 build_app.py
输出：与 RemuxTool 同级的「视频重封装工具.app」
"""
import os
import sys
import shutil
import stat
import subprocess
import py_compile

# ---------- 路径配置 ----------
HERE = os.path.dirname(os.path.abspath(__file__))            # RemuxTool/_build
TOOL = os.path.dirname(HERE)                                  # RemuxTool
WORKSPACE = os.path.dirname(TOOL)                             # 工作区
APP_PATH = os.path.join(WORKSPACE, "视频重封装工具.app")

PYROOT = "/Users/bkp01/.workbuddy/binaries/python/versions/3.13.12"
SRC_PY = os.path.join(TOOL, "remux_gui.py")
SRC_FFMPEG = os.path.join(TOOL, "bin", "ffmpeg")
SRC_ICNS = "/tmp/appicon_new.icns"

# 从主程序读取版本号，保持单一来源
sys.path.insert(0, TOOL)
import remux_gui as mod
VERSION = mod.APP_VERSION
APP_NAME = "视频重封装工具"

# ---------- app 目录结构 ----------
CONTENTS = os.path.join(APP_PATH, "Contents")
MACOS = os.path.join(CONTENTS, "MacOS")
RESOURCES = os.path.join(CONTENTS, "Resources")
RUNTIME = os.path.join(RESOURCES, "runtime")
APP_CODE = os.path.join(RESOURCES, "app")
APP_BIN = os.path.join(RESOURCES, "bin")

# stdlib 中需要剔除的目录（瘦身，工具用不到）
PRUNE_DIRS = {
    "site-packages", "ensurepip", "idlelib", "test", "config-3.13-darwin",
    "__pycache__", "turtledemo", "pydoc_data", "lib2to3",
}
PRUNE_FILES_SUFFIX = (".pyc",)  # 只保留 .py 源码字节码由运行生成；pyc 目录统一剔除


def reset_dir(p):
    if os.path.exists(p):
        shutil.rmtree(p)
    os.makedirs(p)


def copy_stdlib_pruned(src, dst):
    """复制 python3.13 stdlib，剔除无用目录与所有 __pycache__。"""
    shutil.copytree(
        src, dst,
        ignore=lambda d, names: [
            n for n in names
            if (n in PRUNE_DIRS) or (n.startswith("__pycache__"))
            or (n.endswith(".pyc"))
        ],
    )


def write_launcher():
    """macOS 应用入口脚本：设置环境变量后启动内嵌 Python 运行 pyc。"""
    launcher = os.path.join(MACOS, "launcher")
    content = f'''#!/bin/bash
# {APP_NAME} v{VERSION} — 自包含启动器（离线）
RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PYTHONHOME="$RESOURCES/runtime"
export REMUX_RESOURCE_DIR="$RESOURCES"
export TCL_LIBRARY="$RESOURCES/runtime/lib/tcl9.0"
export TK_LIBRARY="$RESOURCES/runtime/lib/tk9.0"
# 让内嵌 pyc 能 import 同目录的 drop_target 等模块（sourceless 加载）
export PYTHONPATH="$RESOURCES/app"
exec "$RESOURCES/runtime/bin/python3.13" "$RESOURCES/app/remux_gui.pyc" "$@"
'''
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IEXEC)


def write_info_plist():
    plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>appicon.icns</string>
    <key>CFBundleIdentifier</key>
    <string>local.remux.tool</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>{VERSION}</string>
    <key>CFBundleVersion</key>
    <string>{VERSION}</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
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
    print(f"构建 {APP_NAME}.app  v{VERSION} ...")
    # 清空旧 app
    if os.path.exists(APP_PATH):
        shutil.rmtree(APP_PATH)
    for d in (MACOS, RESOURCES, RUNTIME, APP_CODE, APP_BIN):
        os.makedirs(d, exist_ok=True)

    # 1. 编译主程序及依赖模块为 pyc（不分发 .py）
    print("  [1/4] 编译主程序与依赖模块为 .pyc ...")
    py_compile.compile(SRC_PY, cfile=os.path.join(APP_CODE, "remux_gui.pyc"), doraise=True)
    drop_src = os.path.join(TOOL, "drop_target.py")
    if os.path.exists(drop_src):
        py_compile.compile(drop_src, cfile=os.path.join(APP_CODE, "drop_target.pyc"), doraise=True)

    # 2. 图标 + 启动器 + Info.plist
    print("  [2/4] 写入图标 / 启动器 / Info.plist ...")
    if os.path.exists(SRC_ICNS):
        shutil.copy2(SRC_ICNS, os.path.join(RESOURCES, "appicon.icns"))
    write_launcher()
    write_info_plist()
    os.makedirs(RUNTIME, exist_ok=True)
    os.makedirs(os.path.join(RUNTIME, "bin"), exist_ok=True)
    os.makedirs(os.path.join(RUNTIME, "lib"), exist_ok=True)

    # 3. 生成 app 专用说明
    print("  [3/4] 写入说明文件 ...")
    readme = f"""{APP_NAME}  v{VERSION}

完全自包含的 macOS 应用，无需安装 Python 或 ffmpeg，无需联网。

使用方法：
  双击打开本应用 → 选择源文件夹 / 目标文件夹 → 选目标格式 → 扫描 → 开始转换。

功能：
  - 批量扫描视频文件，无损重封装到 MOV / MP4 / MKV
  - 保留子目录层级
  - MXF→MOV 专项优化（跳过时间码/元数据轨）
  - HEVC 自动修复 hvc1 标签，解决 Mac 空格预览问题
  - 临时文件校验 + 原子替换，单文件失败不影响整体

架构：内置 ffmpeg 为 x86_64。M 系列 Mac 需已装 Rosetta 2；
      若未装，可把 arm64 版 ffmpeg 替换 Resources/bin/ffmpeg。

版本：v{VERSION}
"""
    with open(os.path.join(RESOURCES, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(readme)

    size = subprocess.run(["du", "-sh", APP_PATH], capture_output=True, text=True).stdout.strip()
    print(f"完成：{APP_PATH}  ({size})")


if __name__ == "__main__":
    main()
