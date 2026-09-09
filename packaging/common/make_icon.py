#!/usr/bin/env python3
"""Generate the Lernapp icon without any third-party library (pure zlib PNG writer).

    make_icon.py --png out.png [--size 1024]
    make_icon.py --ico out.ico            # PNG-in-ICO (256/48/32/16 px), Windows Vista+

The design is deliberately simple: a blue rounded square, a white speech bubble and a bold
white "L". The macOS build turns the PNG into an .icns with sips + iconutil, Linux uses the
PNG for the .desktop entry, Windows uses the .ico for the shortcuts / Inno Setup.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path: Path, size: int, pixel) -> None:  # noqa: ANN001 - callable (x, y) -> (r, g, b, a)
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        for x in range(size):
            raw.extend(pixel(x, y))
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)


def _in_rounded_rect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def make_pixel(size: int):  # noqa: ANN202
    s = size
    margin = s * 0.06
    radius = s * 0.22

    def bg(x: float, y: float) -> tuple[int, int, int]:
        t = (x + y) / (2 * s)
        return (int(31 + (59 - 31) * t), int(95 + (130 - 95) * t), int(191 + (246 - 191) * t))

    # speech bubble: rounded rect + tail
    bx0, by0, bx1, by1 = s * 0.20, s * 0.22, s * 0.80, s * 0.68
    br = s * 0.12
    # bold "L" inside the bubble
    lx0, ly0 = s * 0.36, s * 0.31
    stem_w, base_h = s * 0.09, s * 0.08
    lx1, ly1 = s * 0.62, s * 0.59

    def pixel(px: int, py: int) -> tuple[int, int, int, int]:
        x, y = px + 0.5, py + 0.5
        if not _in_rounded_rect(x, y, margin, margin, s - margin, s - margin, radius):
            return (0, 0, 0, 0)
        # subtle border shade
        if not _in_rounded_rect(x, y, margin + s * 0.012, margin + s * 0.012, s - margin - s * 0.012,
                                s - margin - s * 0.012, radius - s * 0.012):
            r, g, b = bg(x, y)
            return (max(r - 30, 0), max(g - 30, 0), max(b - 30, 0), 255)
        in_bubble = _in_rounded_rect(x, y, bx0, by0, bx1, by1, br)
        # tail: triangle below the bubble, bottom-left
        tx0, ty = s * 0.30, by1 - s * 0.02
        if not in_bubble and ty <= y <= s * 0.80:
            k = (y - ty) / (s * 0.80 - ty)
            if tx0 + k * s * 0.08 <= x <= tx0 + s * 0.16 - k * s * 0.02:
                in_bubble = True
        if in_bubble:
            in_l = (lx0 <= x <= lx0 + stem_w and ly0 <= y <= ly1) or (lx0 <= x <= lx1 and ly1 - base_h <= y <= ly1)
            if in_l:
                r, g, b = bg(x, y)
                return (r, g, b, 255)
            return (255, 255, 255, 255)
        r, g, b = bg(x, y)
        return (r, g, b, 255)

    return pixel


def png_bytes(size: int) -> bytes:
    tmp = Path(f".make_icon_{size}.png.tmp")
    write_png(tmp, size, make_pixel(size))
    data = tmp.read_bytes()
    tmp.unlink()
    return data


def write_ico(path: Path, sizes: tuple[int, ...] = (256, 48, 32, 16)) -> None:
    images = [(sz, png_bytes(sz)) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    body = b""
    for sz, data in images:
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    path.write_bytes(header + entries + body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--png", type=Path, help="write a PNG here")
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--ico", type=Path, help="write a Windows .ico here")
    args = ap.parse_args(argv)
    if not args.png and not args.ico:
        ap.error("--png and/or --ico required")
    if args.png:
        args.png.parent.mkdir(parents=True, exist_ok=True)
        write_png(args.png, args.size, make_pixel(args.size))
        print(f"wrote {args.png} ({args.size}x{args.size})")
    if args.ico:
        args.ico.parent.mkdir(parents=True, exist_ok=True)
        write_ico(args.ico)
        print(f"wrote {args.ico}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
