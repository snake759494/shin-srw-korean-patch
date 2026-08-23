#!/usr/bin/env python3
"""Recover SSRW's custom text encoding from its embedded bitmap fonts.

The 8x16 page is in a known kana/Latin order.  The 1,536 16x16 glyphs are
matched against the reviewed Super Robot Wars Complete Box font and are also
read with the installed Japanese Windows OCR engine.  All evidence is kept in
the output so uncertain glyphs remain reviewable.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from PIL import Image


SMALL_OFFSET = 0x72438
SMALL_COUNT = 0x100
SMALL_BYTES = 16
WIDE_OFFSET = 0x73438
WIDE_COUNT = 0x600
WIDE_BYTES = 32

# Context-verified readings from repeated, unambiguous phrases in SCEDATA and
# BTTMES (for example 兜甲児, 驚く, 獣士, 初めて, 資質, 被害, 監視).
CONTEXT_OVERRIDES = {
    0x01E: "斉",
    0x0FD: "技",
    0x0AE: "驚",
    0x164: "算",
    0x17C: "亀",
    0x1AE: "郎",
    0x224: "位",
    0x229: "振",
    0x27D: "縮",
    0x297: "初",
    0x2B9: "静",
    0x2D3: "到",
    0x2D6: "久",
    0x2FB: "多",
    0x33E: "監",
    0x355: "獣",
    0x358: "諸",
    0x359: "興",
    0x36F: "慮",
    0x391: "被",
    0x397: "如",
    0x3A4: "遂",
    0x3A7: "児",
    0x3F6: "勢",
    0x40B: "政",
    0x42D: "質",
    0x43F: "働",
    0x48C: "斉",
    0x48F: "囲",
    0x49F: "鎧",
    0x4A9: "慕",
    0x4D5: "伊",
    0x52C: "覧",
    0x569: "阪",
    0x58D: "暇",
    0x5A0: "湯",
    0x5D2: "狩",
    0x5EB: "悠",
    0x463: "唯",
    0x33A: "衛",
}

SMALL_CONTEXT_OVERRIDES = {
    0x00: " ",
    0x05: "ν",
    0x0C: "V",
    0xE8: "『",
    0xE9: "』",
    0xEA: "○",
}


def load_cb_ocr_module(path: Path):
    spec = importlib.util.spec_from_file_location("srwcb_ocr", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ssrw_wide_to_row_major(source: bytes) -> bytes:
    return b"".join(bytes((source[y], source[16 + y])) for y in range(16))


def decode_wide_glyphs(font: bytes) -> tuple[list[bytes], list[Image.Image]]:
    raw: list[bytes] = []
    images: list[Image.Image] = []
    for index in range(WIDE_COUNT):
        split = font[index * WIDE_BYTES : (index + 1) * WIDE_BYTES]
        row_major = ssrw_wide_to_row_major(split)
        raw.append(row_major)
        image = Image.new("L", (16, 16), 255)
        pixels = image.load()
        for y in range(16):
            word = int.from_bytes(row_major[y * 2 : y * 2 + 2], "big")
            for x in range(16):
                if word & (0x8000 >> x):
                    pixels[x, y] = 0
        images.append(image)
    return raw, images


def encoded_wide(index: int) -> str:
    return bytes((0xF0 + (index >> 8), index & 0xFF)).hex(" ").upper()


def cp_string(value: str | None) -> str:
    return "" if value is None else " ".join(f"U+{ord(c):04X}" for c in value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument(
        "--cb-exe",
        type=Path,
        default=Path("EX.WAR")  # from the sibling Complete Box disc; not in this repo,
    )
    parser.add_argument(
        "--cb-map",
        type=Path,
        default=Path(
            "srwcb_embedded_font_mapping_reviewed.json"  # sibling project
        ),
    )
    parser.add_argument(
        "--cb-ocr-script",
        type=Path,
        default=Path(
            "ocr_embedded_font.py"  # unpublished OCR helper
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("text_extracted"))
    parser.add_argument("--skip-ocr", action="store_true")
    args = parser.parse_args()

    cb_ocr = load_cb_ocr_module(args.cb_ocr_script.resolve())
    exe = args.exe.read_bytes()
    small = exe[SMALL_OFFSET : SMALL_OFFSET + SMALL_COUNT * SMALL_BYTES]
    wide_split = exe[WIDE_OFFSET : WIDE_OFFSET + WIDE_COUNT * WIDE_BYTES]
    if len(small) != SMALL_COUNT * SMALL_BYTES or len(wide_split) != WIDE_COUNT * WIDE_BYTES:
        raise ValueError("SSRW executable font data is truncated")
    wide_raw, wide_images = decode_wide_glyphs(wide_split)

    cb_exe = args.cb_exe.read_bytes()
    cb_font = cb_exe[cb_ocr.DEFAULT_FONT_OFFSET : cb_ocr.DEFAULT_FONT_OFFSET + cb_ocr.FONT_BYTES]
    if len(cb_font) != cb_ocr.FONT_BYTES:
        raise ValueError("Complete Box font is truncated")
    cb_doc = json.loads(args.cb_map.read_text(encoding="utf-8"))
    cb_rows = {int(row["glyph_index"]): row for row in cb_doc["rows"]}

    exact_lookup: dict[bytes, list[tuple[int, str]]] = defaultdict(list)
    reference: list[tuple[int, str, int]] = []
    for index in range(cb_ocr.GLYPH_COUNT):
        row = cb_rows.get(index)
        character = row.get("character") if row else None
        if not character:
            continue
        glyph = cb_font[index * WIDE_BYTES : (index + 1) * WIDE_BYTES]
        exact_lookup[glyph].append((index, character))
        reference.append((index, character, int.from_bytes(glyph, "big")))

    nearest: dict[int, dict[str, Any]] = {}
    for index, glyph in enumerate(wide_raw):
        value = int.from_bytes(glyph, "big")
        best_distance = 257
        best: list[tuple[int, str]] = []
        for cb_index, character, cb_value in reference:
            distance = (value ^ cb_value).bit_count()
            if distance < best_distance:
                best_distance = distance
                best = [(cb_index, character)]
            elif distance == best_distance:
                best.append((cb_index, character))
        nearest[index] = {
            "distance": best_distance,
            "candidates": [
                {"cb_glyph_index": i, "character": c} for i, c in best[:12]
            ],
            "candidate_count": len(best),
        }
        if index % 128 == 127:
            print(f"bitmap matching: {index + 1}/{WIDE_COUNT}", flush=True)

    observations: dict[int, dict[str, str | None]] = {
        index: {variant.name: None for variant in cb_ocr.VARIANTS}
        for index in range(WIDE_COUNT)
    }
    grid_root = args.output_dir / "ocr_grids"
    helper = args.cb_ocr_script.with_name("windows_ocr_json.ps1").resolve()
    if not args.skip_ocr:
        batch_starts = list(range(0, WIDE_COUNT, cb_ocr.GRID_COUNT))
        for variant in cb_ocr.VARIANTS:
            for batch_number, start in enumerate(batch_starts, 1):
                image_path = grid_root / variant.name / f"batch_{start:03X}.png"
                cb_ocr.render_grid(wide_images, start, variant, image_path)
                lines = cb_ocr.run_ocr(helper, image_path.resolve())
                assigned = cb_ocr.assign_words(lines, start, variant)
                for index, value in assigned.items():
                    if index < WIDE_COUNT:
                        observations[index][variant.name] = value
                print(
                    f"OCR {variant.name} {batch_number:02d}/{len(batch_starts):02d} "
                    f"0x{start:03X}: {len(assigned):02d}",
                    flush=True,
                )

    wide_rows: list[dict[str, Any]] = []
    for index, glyph in enumerate(wide_raw):
        exact = exact_lookup.get(glyph, [])
        exact_chars = sorted({character for _, character in exact})
        obs = observations[index]
        counts = Counter(value for value in obs.values() if value)
        ranked_ocr = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ocr_value = ranked_ocr[0][0] if ranked_ocr else None
        ocr_votes = ranked_ocr[0][1] if ranked_ocr else 0
        near = nearest[index]
        near_chars = sorted({item["character"] for item in near["candidates"]})

        character: str | None = None
        source = "unresolved"
        confidence = "unresolved"
        if len(exact_chars) == 1:
            character, source, confidence = exact_chars[0], "complete_box_exact", "verified"
        elif len(near_chars) == 1 and ocr_value == near_chars[0] and near["distance"] <= 16:
            character, source, confidence = near_chars[0], "bitmap_nearest+ocr", "high"
        elif len(near_chars) == 1 and near["distance"] <= 2:
            character, source, confidence = near_chars[0], "complete_box_nearest", "high"
        elif len(near_chars) == 1 and near["distance"] <= 6:
            character, source, confidence = near_chars[0], "complete_box_nearest", "medium"
        elif ocr_votes >= 2:
            character, source, confidence = ocr_value, "windows_ocr", "medium"
        elif len(near_chars) == 1 and near["distance"] <= 12:
            character, source, confidence = near_chars[0], "complete_box_nearest", "low"
        elif ocr_value:
            character, source, confidence = ocr_value, "windows_ocr", "low"

        if index in CONTEXT_OVERRIDES:
            character = CONTEXT_OVERRIDES[index]
            source = "scenario_context_review"
            confidence = "verified"

        wide_rows.append(
            {
                "glyph_index": index,
                "glyph_index_hex": f"0x{index:03X}",
                "message_bytes": encoded_wide(index),
                "character": character,
                "unicode": cp_string(character),
                "source": source,
                "confidence": confidence,
                "complete_box_exact": [
                    {"cb_glyph_index": i, "character": c} for i, c in exact
                ],
                "nearest": near,
                "ocr_observations": obs,
                "ocr_alternatives": [
                    {"character": value, "votes": votes} for value, votes in ranked_ocr
                ],
                "foreground_pixels": sum(byte.bit_count() for byte in glyph),
                "glyph_sha256": hashlib.sha256(glyph).hexdigest(),
            }
        )

    low_map = cb_ocr.build_low_manual_map()
    small_rows = []
    for index in range(SMALL_COUNT):
        glyph = small[index * SMALL_BYTES : (index + 1) * SMALL_BYTES]
        character = SMALL_CONTEXT_OVERRIDES.get(index, low_map.get(index))
        small_rows.append(
            {
                "byte": index,
                "byte_hex": f"0x{index:02X}",
                "character": character,
                "unicode": cp_string(character),
                "source": "known_low_order" if index in low_map else "unmapped_or_control",
                "foreground_pixels": sum(byte.bit_count() for byte in glyph),
                "glyph_sha256": hashlib.sha256(glyph).hexdigest(),
            }
        )

    metadata = {
        "format": "SSRW custom font Unicode mapping v1",
        "source_executable": str(args.exe.resolve()),
        "source_executable_sha256": hashlib.sha256(exe).hexdigest(),
        "font_layout": {
            "small": "256 glyphs, 8x16, direct byte 00-EF (F0-FF are text controls/prefixes)",
            "wide": "1536 glyphs, 16x16, split halves; F0-F5 plus trail byte",
        },
        "complete_box_mapping": str(args.cb_map.resolve()),
        "warning": "Low-confidence OCR/nearest matches require contextual review.",
        "small_rows": small_rows,
        "wide_rows": wide_rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "ssrw_japanese_font_mapping.json"
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    tsv_path = args.output_dir / "ssrw_japanese_font_mapping.tsv"
    lines = ["glyph_index\tmessage_bytes\tcharacter\tconfidence\tsource\tnearest_distance\tocr"]
    for row in wide_rows:
        ocr = ";".join(f"{item['character']}:{item['votes']}" for item in row["ocr_alternatives"])
        fields = (
            row["glyph_index_hex"], row["message_bytes"], row["character"] or "",
            row["confidence"], row["source"], str(row["nearest"]["distance"]), ocr,
        )
        lines.append("\t".join(value.replace("\t", "\\t").replace("\n", "\\n") for value in fields))
    tsv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"mapping JSON: {json_path.resolve()}")
    print(f"mapping TSV:  {tsv_path.resolve()}")
    print("confidence:", dict(Counter(row["confidence"] for row in wide_rows)))
    print("source:", dict(Counter(row["source"] for row in wide_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
