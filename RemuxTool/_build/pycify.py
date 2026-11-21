#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 app 内 Python stdlib 编译为 legacy .pyc 并删除 .py，实现 app 内零源码。"""
import os
import compileall
import sys

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "视频重封装工具.app")
STDLIB = os.path.join(APP_PATH, "Contents", "Resources", "runtime", "lib", "python3.13")


def main():
    if not os.path.isdir(STDLIB):
        print("STDLIB_NOT_FOUND", STDLIB)
        sys.exit(1)
    print("编译 stdlib 为 legacy pyc ...")
    ok = compileall.compile_dir(STDLIB, quiet=1, force=True, legacy=True)
    if not ok:
        print("COMPILE_FAILED")
        sys.exit(1)
    cnt = 0
    for dp, _, fn in os.walk(STDLIB):
        for f in fn:
            if f.endswith(".py"):
                os.remove(os.path.join(dp, f))
                cnt += 1
    print(f"PYCIFY_OK 已移除 {cnt} 个 .py 文件")


if __name__ == "__main__":
    main()
