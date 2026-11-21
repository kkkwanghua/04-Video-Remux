#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 app 的第二阶段：
  前置：build_app.py 已生成 Info.plist / launcher / pyc / 说明 / 图标
  本阶段：用 rsync 复制精简 Python 运行时 + ffmpeg（绕开沙盒 shim），最后校验。
"""
import os
import sys
import subprocess
import stat

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(TOOL)
APP_PATH = os.path.join(WORKSPACE, "视频重封装工具.app")
CONTENTS = os.path.join(APP_PATH, "Contents")
RESOURCES = os.path.join(CONTENTS, "Resources")
RUNTIME = os.path.join(RESOURCES, "runtime")
APP_BIN = os.path.join(RESOURCES, "bin")

PYROOT = "/Users/bkp01/.workbuddy/binaries/python/versions/3.13.12"
SRC_FFMPEG = os.path.join(TOOL, "bin", "ffmpeg")

sys.path.insert(0, TOOL)
import remux_gui as mod
VERSION = mod.APP_VERSION


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        print("CMD FAIL:", " ".join(cmd))
        print(r.stderr[-1000:])
        raise SystemExit(1)
    return r


def main():
    os.makedirs(RUNTIME, exist_ok=True)
    os.makedirs(os.path.join(RUNTIME, "bin"), exist_ok=True)
    os.makedirs(os.path.join(RUNTIME, "lib"), exist_ok=True)
    os.makedirs(APP_BIN, exist_ok=True)

    # 0. 复制 python 可执行文件和核心 dylib
    print("[0/4] 复制 python 可执行文件与 dylib ...")
    run(["cp", os.path.join(PYROOT, "bin", "python3.13"),
         os.path.join(RUNTIME, "bin", "python3.13")])
    run(["chmod", "+x", os.path.join(RUNTIME, "bin", "python3.13")])
    for dll in ["libpython3.13.dylib", "libtcl9.0.dylib", "libtcl9tk9.0.dylib"]:
        run(["cp", os.path.join(PYROOT, "lib", dll), os.path.join(RUNTIME, "lib", dll)])

    # 1. rsync 复制精简 stdlib（剔除 site-packages / 缓存 / pyc）
    print("[1/4] rsync 复制 Python stdlib（精简）...")
    run([
        "rsync", "-a", "--delete",
        "--exclude", "site-packages",
        "--exclude", "ensurepip",
        "--exclude", "idlelib",
        "--exclude", "test",
        "--exclude", "config-3.13-darwin",
        "--exclude", "turtledemo",
        "--exclude", "pydoc_data",
        "--exclude", "lib2to3",
        "--exclude", "__pycache__",
        "--exclude", "*.pyc",
        f"{PYROOT}/lib/python3.13/",
        f"{RUNTIME}/lib/python3.13/",
    ])
    # 2. 复制 tcl/tk 资源目录
    print("[2/4] 复制 tcl/tk 资源...")
    for d in ["tcl9.0", "tk9.0", "tcl9", "itcl4.3.5", "thread3.0.4"]:
        src = os.path.join(PYROOT, "lib", d)
        if os.path.isdir(src):
            run(["rsync", "-a", f"{src}/", f"{RUNTIME}/lib/{d}/"])

    # 3. 复制内置 ffmpeg
    print("[3/4] 复制内置 ffmpeg ...")
    run(["cp", SRC_FFMPEG, os.path.join(APP_BIN, "ffmpeg")])
    run(["chmod", "+x", os.path.join(APP_BIN, "ffmpeg")])

    size = run(["du", "-sh", APP_PATH]).stdout.strip()
    print(f"STAGE2_OK  v{VERSION}  {APP_PATH}  {size}")


if __name__ == "__main__":
    main()
