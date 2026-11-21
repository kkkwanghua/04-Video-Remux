#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯标准库解析 Mach-O 文件架构（x86_64 / arm64 / universal fat）。
用法：python3 macho_arch.py <file> [file...]
输出：每个文件的架构列表。用于本机(x86_64)无法运行 arm64 二进制时校验产物架构。
"""
import struct
import sys

# Mach-O magic
MH_MAGIC = 0xfeedface      # 32-bit LE
MH_CIGAM = 0xcefaedfe
MH_MAGIC_64 = 0xfeedfacf   # 64-bit LE
MH_CIGAM_64 = 0xcffaedfe
FAT_MAGIC = 0xcafebabe     # fat 32-bit BE
FAT_CIGAM = 0xbebafeca
FAT_MAGIC_64 = 0xcafebabf
FAT_CIGAM_64 = 0xbfbafeca

# CPU types
CPU_ARCH_ABI64 = 0x01000000
CPU_TYPE_X86 = 7
CPU_TYPE_X86_64 = CPU_TYPE_X86 | CPU_ARCH_ABI64          # 0x01000007
CPU_TYPE_ARM = 12
CPU_TYPE_ARM64 = CPU_TYPE_ARM | CPU_ARCH_ABI64           # 0x0100000c
CPU_TYPE_ARM64_32 = 12 | 0x02000000

ARCH_NAMES = {
    CPU_TYPE_X86_64: "x86_64",
    CPU_TYPE_X86: "i386",
    CPU_TYPE_ARM64: "arm64",
    CPU_TYPE_ARM64_32: "arm64_32",
    CPU_TYPE_ARM: "arm",
}


def name_of(ctype):
    # CPU subtype 256 = arm64e; 我们从 cputype 判断主架构
    base = ctype & 0xffffffff
    return ARCH_NAMES.get(base, f"unknown(0x{base:x})")


def parse(path):
    with open(path, "rb") as f:
        data = f.read(64)
    if len(data) < 8:
        return ["too-small"]
    magic_be = struct.unpack_from(">I", data, 0)[0]
    magic_le = struct.unpack_from("<I", data, 0)[0]

    # Universal binary (fat) — 大端 magic
    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        is64 = magic_be == FAT_MAGIC_64
        nfat = struct.unpack_from(">I", data, 4)[0]
        out = []
        off = 8
        entry_size = 32 if is64 else 20
        with open(path, "rb") as f:
            f.seek(0)
            full = f.read()
        for i in range(nfat):
            if off + entry_size > len(full):
                break
            ctype = struct.unpack_from(">i", full, off)[0]
            csub = struct.unpack_from(">i", full, off + 4)[0]
            nm = name_of(ctype)
            if nm == "arm64" and (csub & 0xffffffff) == 2:
                nm = "arm64e"
            out.append(nm)
            off += entry_size
        return out

    # 薄二进制
    if magic_le == MH_MAGIC_64:
        ctype = struct.unpack_from("<i", data, 4)[0]
        csub = struct.unpack_from("<i", data, 8)[0]
        nm = name_of(ctype)
        if nm == "arm64" and (csub & 0xffffffff) == 2:
            nm = "arm64e"
        return [nm]
    if magic_le == MH_MAGIC:
        ctype = struct.unpack_from("<i", data, 4)[0]
        return [name_of(ctype)]
    return ["not-macho"]


if __name__ == "__main__":
    for p in sys.argv[1:]:
        try:
            arches = parse(p)
            print(f"{','.join(arches)}\t{p}")
        except Exception as e:
            print(f"ERROR\t{p}\t{e}")
