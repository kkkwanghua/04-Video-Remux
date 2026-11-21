#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app 图标（PNG -> iconset），纯标准库。"""
import zlib, struct, os, subprocess


def make_png(path, size):
    W = H = size
    s = W / 512.0
    bg = (38, 50, 72, 255)
    accent = (90, 173, 255, 255)
    accent2 = (255, 196, 84, 255)
    white = (240, 245, 255, 255)
    px = [[bg for _ in range(W)] for _ in range(H)]

    def rect(x0, y0, x1, y1, c):
        x0, y0, x1, y1 = int(x0*s), int(y0*s), int(x1*s), int(y1*s)
        for y in range(max(0, y0), min(H, y1)):
            for x in range(max(0, x0), min(W, x1)):
                px[y][x] = c

    # 胶片外框
    rect(96, 150, 416, 362, accent)
    rect(112, 166, 400, 346, bg)
    for i in range(5):
        x = 130 + i * 56
        rect(x, 168, x + 30, 186, accent)
        rect(x, 326, x + 30, 344, accent)
    # 播放三角
    for y in range(210, 306):
        t = (y - 210) / (306 - 210)
        half = int(60 * (1 - t))
        for x in range(248 - half, 248 + half + 1):
            if 0 <= x < W:
                px[y][x] = white
    # 箭头
    rect(330, 240, 410, 262, accent2)
    for y in range(240, 276):
        w = 80 - (y - 240) * 2
        if w <= 0:
            break
        x0 = 330 + (80 - w) // 2
        for x in range(x0, x0 + w):
            if 0 <= x < W and y < H:
                px[y][x] = accent2

    def in_round(x, y):
        r = 90 * s
        for cx, cy in [(r, r), (W-r, r), (r, H-r), (W-r, H-r)]:
            if (x < cx and y < cy) or (x > W-cx and y < cy) or \
               (x < cx and y > H-cy) or (x > W-cx and y > H-cy):
                dx, dy = x - cx, y - cy
                if dx*dx + dy*dy > r*r:
                    return False
        return True

    raw = bytearray()
    for y in range(H):
        raw.append(0)
        for x in range(W):
            c = px[y][x]
            raw += bytes(c) if in_round(x, y) else bytes((0, 0, 0, 0))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t+d) & 0xffffffff)

    hdr = struct.pack('>IIBBBBB', W, H, 8, 6, 0, 0, 0)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr) +
            chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(data)


def main():
    iconset = "/tmp/remux_iconset.iconset"
    os.makedirs(iconset, exist_ok=True)
    base = "/tmp/remux_icon_base.png"
    make_png(base, 1024)
    for sz in [16, 32, 64, 128, 256, 512]:
        subprocess.run(["sips", "-z", str(sz), str(sz), base,
                        "--out", f"{iconset}/icon_{sz}x{sz}.png"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sz2 = sz * 2
        subprocess.run(["sips", "-z", str(sz2), str(sz2), base,
                        "--out", f"{iconset}/icon_{sz}x{sz}@2x.png"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("ICONSET_OK", iconset)


if __name__ == "__main__":
    main()
