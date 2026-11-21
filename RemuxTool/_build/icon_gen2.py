#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仿苹果风格 app 图标：squircle + 渐变 + 阴影，纯标准库 + 2x 超采样。"""
import zlib
import struct
import os
import math
import subprocess

OUT = 1024          # 输出尺寸
SS = 2              # 超采样倍数
N = OUT * SS        # 渲染画布


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def squircle(fx, fy, half=0.47, radius=0.115):
    """圆角矩形判定：返回 True 表示在形状内。
    half: 半边长（fx,fy 相对中心 0.5 的归一化距离，满幅为 0.5）
    radius: 圆角半径（同样归一化），0.115/0.47 ≈ 24% 圆角率，接近 macOS 规范。"""
    ax = abs(fx - 0.5)
    ay = abs(fy - 0.5)
    straight = half - radius
    dx = max(ax - straight, 0.0)
    dy = max(ay - straight, 0.0)
    return dx * dx + dy * dy <= radius * radius


def symbol(fx, fy):
    """中心白色符号：影片框 + 播放三角 + 环形转换箭头。返回 alpha(0~1) 或 0。"""
    # --- 圆角影片框 ---
    x0, x1, y0, y1 = 0.30, 0.62, 0.35, 0.65
    r = 0.035
    if x0 - r <= fx <= x1 + r and y0 - r <= fy <= y1 + r:
        # 圆角判断
        cx = clamp(fx, x0 + r, x1 - r)
        cy = clamp(fy, y0 + r, y1 - r)
        ddx, ddy = fx - cx, fy - cy
        if ddx * ddx + ddy * ddy <= r * r and x0 <= fx <= x1 and y0 <= fy <= y1:
            # 胶片孔（上下边缘）
            hole = False
            if 0.363 <= fy <= 0.395 or 0.605 <= fy <= 0.637:
                for i in range(4):
                    hx = 0.325 + i * 0.075
                    if hx <= fx <= hx + 0.045:
                        hole = True
            if hole:
                return 0.0
            # 播放三角（纯白，突出）
            if 0.425 <= fy <= 0.575:
                t = (fy - 0.425) / 0.15
                cx_t = 0.435
                half_w = 0.075 * (1 - t)
                if cx_t <= fx <= cx_t + half_w * 2:
                    return 1.0
            return 0.55  # 胶片框半透明，衬托白色三角
    # --- 环形转换箭头（右侧）---
    ax, ay = 0.73, 0.5
    dx, dy = fx - ax, fy - ay
    d = math.sqrt(dx * dx + dy * dy)
    if 0.075 <= d <= 0.105:
        ang = math.atan2(dy, dx)
        # 两段弧，中间留缺口
        if (-2.7 < ang < -0.45) or (0.45 < ang < 2.7):
            return 0.98
    # 箭头尖（右下方三角）
    if 0.805 <= fx <= 0.855 and 0.44 <= fy <= 0.56:
        return 0.98
    return 0.0


def render():
    top = (78, 156, 255)
    bottom = (92, 90, 235)
    edge = (46, 108, 210)

    # 在低分辨率 OUT 上直接渲染，每个像素做 SS×SS 子采样并平均
    pixels = []
    for py in range(OUT):
        row = []
        for px in range(OUT):
            ar = ag = ab = aa = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    fx = (px * SS + sx + 0.5) / N
                    fy = (py * SS + sy + 0.5) / N
                    if not squircle(fx, fy):
                        continue
                    t = clamp(fy)
                    r = lerp(top[0], bottom[0], t)
                    g = lerp(top[1], bottom[1], t)
                    b = lerp(top[2], bottom[2], t)
                    # 边缘立体暗化：靠近形状边界处变暗
                    ax, ay = abs(fx - 0.5), abs(fy - 0.5)
                    near = max(ax, ay) / 0.47
                    et = clamp((near - 0.88) / 0.12) * 0.5
                    r = lerp(r, edge[0], et)
                    g = lerp(g, edge[1], et)
                    b = lerp(b, edge[2], et)
                    # 顶部高光
                    hi = clamp(1.0 - fy * 2.0) * 0.16
                    r = min(255, r + 255 * hi)
                    g = min(255, g + 255 * hi)
                    b = min(255, b + 255 * hi)
                    # 符号
                    sa = symbol(fx, fy)
                    if sa > 0:
                        r = lerp(r, 255, sa)
                        g = lerp(g, 255, sa)
                        b = lerp(b, 255, sa)
                    ar += r; ag += g; ab += b; aa += 255
            sub = SS * SS
            if aa > 0:
                cov = aa / 255.0 / sub
                rr = ar / sub / cov
                gg = ag / sub / cov
                bb = ab / sub / cov
                row.append((int(clamp(rr, 0, 255)), int(clamp(gg, 0, 255)),
                            int(clamp(bb, 0, 255)), int(clamp(cov * 255, 0, 255))))
            else:
                row.append((0, 0, 0, 0))
        pixels.append(row)
    return pixels


def write_png(path, pixels):
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    hdr = struct.pack(">IIBBBBB", OUT, OUT, 8, 6, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", hdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


def main():
    out = "/tmp/appicon_new.iconset"
    os.makedirs(out, exist_ok=True)
    base = "/tmp/appicon_base.png"
    write_png(base, render())
    for sz in [16, 32, 64, 128, 256, 512]:
        subprocess.run(["sips", "-z", str(sz), str(sz), base, "--out", f"{out}/icon_{sz}x{sz}.png"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sips", "-z", str(sz*2), str(sz*2), base, "--out", f"{out}/icon_{sz}x{sz}@2x.png"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["iconutil", "-c", "icns", out, "-o", "/tmp/appicon_new.icns"], check=True)
    print("ICNS_OK /tmp/appicon_new.icns")


if __name__ == "__main__":
    main()
