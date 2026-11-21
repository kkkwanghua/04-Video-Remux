#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把指定 stdlib 目录的 .py 编译为 legacy .pyc 并删除 .py（app 内零源码）。
用法：python3 pycify_arm64.py <stdlib路径>
"""
import os
import sys
import compileall


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法: pycify_arm64.py <stdlib路径>")
    stdlib = sys.argv[1]
    if not os.path.isdir(stdlib):
        print("STDLIB_NOT_FOUND", stdlib)
        sys.exit(1)
    print("编译 stdlib 为 legacy pyc ...")
    ok = compileall.compile_dir(stdlib, quiet=1, force=True, legacy=True)
    if not ok:
        print("COMPILE_FAILED")
        sys.exit(1)
    cnt = 0
    for dp, _, fn in os.walk(stdlib):
        for f in fn:
            if f.endswith(".py"):
                os.remove(os.path.join(dp, f))
                cnt += 1
    print(f"PYCIFY_OK 已移除 {cnt} 个 .py 文件")


if __name__ == "__main__":
    main()
