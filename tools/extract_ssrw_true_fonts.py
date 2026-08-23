#!/usr/bin/env python3
"""Extract the font tables proven by SSRW's renderer code."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SMALL_OFFSET = 0x72438
SMALL_COUNT = 256
SMALL_BYTES = 16
WIDE_OFFSET = 0x73438
WIDE_COUNT = 6 * 256
WIDE_BYTES = 32


def draw_sheet(font: bytes, count: int, wide_layout: str, scale: int = 3) -> Image.Image:
    columns = 32
    rows = math.ceil(count / columns)
    label_h = 8
    width = 16 if wide_layout != "small" else 8
    cell_w = width * scale
    cell_h = (16 + label_h) * scale
    image = Image.new("RGB", (columns * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(image)
    label_font = ImageFont.load_default()
    stride = WIDE_BYTES if wide_layout != "small" else SMALL_BYTES
    for index in range(count):
        glyph = font[index * stride : (index + 1) * stride]
        left = index % columns * cell_w
        top = index // columns * cell_h
        for y in range(16):
            if wide_layout == "small":
                word = glyph[y]
            elif wide_layout == "row-major":
                word = int.from_bytes(glyph[y * 2 : y * 2 + 2], "big")
            elif wide_layout == "split-halves":
                word = (glyph[y] << 8) | glyph[16 + y]
            else:
                raise ValueError(wide_layout)
            for x in range(width):
                bit = width - 1 - x
                if word & (1 << bit):
                    x0 = left + x * scale
                    y0 = top + y * scale
                    draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill="black")
        draw.text((left + 1, top + 16 * scale + 1), f"{index:03X}", fill=(190, 0, 0), font=label_font)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument("--output-dir", type=Path, default=Path("true_font_extracted"))
    args = parser.parse_args()
    exe = args.exe.read_bytes()
    small = exe[SMALL_OFFSET : SMALL_OFFSET + SMALL_COUNT * SMALL_BYTES]
    wide = exe[WIDE_OFFSET : WIDE_OFFSET + WIDE_COUNT * WIDE_BYTES]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "ssrw_small_256_8x16.bin").write_bytes(small)
    (args.output_dir / "ssrw_wide_1536_16x16.bin").write_bytes(wide)
    draw_sheet(small, SMALL_COUNT, "small").save(args.output_dir / "ssrw_small_256_8x16.png")
    draw_sheet(wide, WIDE_COUNT, "split-halves").save(
        args.output_dir / "ssrw_wide_1536_16x16_split_halves.png"
    )
    draw_sheet(wide, WIDE_COUNT, "row-major").save(
        args.output_dir / "ssrw_wide_1536_16x16_row_major_control.png"
    )
    print(f"small: offset={SMALL_OFFSET:#x}, bytes={len(small)}")
    print(f"wide:  offset={WIDE_OFFSET:#x}, bytes={len(wide)}")


if __name__ == "__main__":
    main()
