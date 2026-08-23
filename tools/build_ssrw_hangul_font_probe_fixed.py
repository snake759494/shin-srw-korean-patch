#!/usr/bin/env python3
"""Build the corrected SSRW 16x16 Hangul font probe.

The renderer at 0x800D8BBC indexes the wide table as ``base + index * 32``.
The table begins at EXE file offset 0x73438 and stores each glyph as sixteen
left-half bytes followed by sixteen right-half bytes.  Message bytes F0-F5
select indices 0x000-0x5FF.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL.PngImagePlugin import PngInfo

from build_ssrw_hangul_font_probe import (
    USER_DATA_SIZE,
    ks_x_1001_hangul,
    parse_bdf,
    patch_track,
    render_glyph,
    sha256,
    sha256_file,
    verify_patched_track,
)
from extract_ssrw_true_fonts import draw_sheet


FONT_OFFSET = 0x73438
FONT_TABLE_GLYPH_COUNT = 1536
FONT_BYTES_PER_GLYPH = 32


def row_major_to_split_halves(glyph: bytes) -> bytes:
    if len(glyph) != FONT_BYTES_PER_GLYPH:
        raise ValueError("expected one 32-byte 16x16 glyph")
    return bytes(glyph[y * 2] for y in range(16)) + bytes(
        glyph[y * 2 + 1] for y in range(16)
    )


def split_halves_to_row_major(glyph: bytes) -> bytes:
    if len(glyph) != FONT_BYTES_PER_GLYPH:
        raise ValueError("expected one 32-byte 16x16 glyph")
    output = bytearray()
    for y in range(16):
        output.extend((glyph[y], glyph[16 + y]))
    return bytes(output)


def shift_row_major(glyph: bytes, x_shift: int) -> bytes:
    """Move a row-major glyph horizontally without changing its cell size."""
    if not -15 <= x_shift <= 15:
        raise ValueError("x shift must be between -15 and 15")
    output = bytearray()
    for y in range(16):
        word = int.from_bytes(glyph[y * 2 : y * 2 + 2], "big")
        if x_shift < 0:
            shifted = (word << -x_shift) & 0xFFFF
        else:
            shifted = word >> x_shift
        output.extend(shifted.to_bytes(2, "big"))
    return bytes(output)


def message_bytes(index: int) -> bytes:
    if not 0 <= index < 0x600:
        raise ValueError(index)
    return bytes((0xF0 + (index >> 8), index & 0xFF))


def write_mapping(path: Path, hangul: list[tuple[int, str]], glyph_count: int) -> None:
    rows = ["glyph_index\tmessage_bytes\teuc_kr\tcharacter\texe_font_offset"]
    for index, (euc_kr, character) in enumerate(hangul[:glyph_count]):
        encoded = message_bytes(index).hex(" ").upper()
        rows.append(
            f"0x{index:03X}\t{encoded}\t0x{euc_kr:04X}\t{character}\t"
            f"0x{FONT_OFFSET + index * FONT_BYTES_PER_GLYPH:X}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def count_wide_references(blob: bytes, glyph_count: int) -> dict[str, int]:
    by_page = {f"F{page:X}": 0 for page in range(6)}
    within_probe = 0
    total = 0
    position = 0
    while position + 1 < len(blob):
        lead = blob[position]
        if 0xF0 <= lead <= 0xF5:
            index = ((lead - 0xF0) << 8) | blob[position + 1]
            by_page[f"F{lead - 0xF0:X}"] += 1
            total += 1
            if index < glyph_count:
                within_probe += 1
            position += 2
        else:
            position += 1
    return {"total": total, f"within_first_{glyph_count}": within_probe, **by_page}


def count_exact_unique_glyph_matches(blob: bytes, font: bytes, stride: int) -> int:
    glyphs = {
        font[offset : offset + stride]
        for offset in range(0, len(font), stride)
        if any(font[offset : offset + stride])
    }
    return sum(glyph in blob for glyph in glyphs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, required=True)
    parser.add_argument("--track2", type=Path, required=True)
    parser.add_argument("--source-exe", type=Path, required=True)
    parser.add_argument("--bdf", type=Path, required=True)
    parser.add_argument("--bttmes", type=Path, default=Path("extracted/BTT/BTTMES.BIN"))
    parser.add_argument("--scedata", type=Path, default=Path("extracted/SCEDATA.BIN"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exe-lba", type=int, default=24)
    parser.add_argument(
        "--ink-width",
        type=int,
        default=11,
        help="original SSRW wide glyphs predominantly occupy columns 0-10",
    )
    parser.add_argument("--glyph-count", type=int, default=FONT_TABLE_GLYPH_COUNT)
    parser.add_argument(
        "--x-shift",
        type=int,
        default=-1,
        help="horizontal pixel shift after rendering; -1 avoids the observed right-edge crop",
    )
    args = parser.parse_args()

    if not 1 <= args.glyph_count <= FONT_TABLE_GLYPH_COUNT:
        raise ValueError(f"glyph count must be 1-{FONT_TABLE_GLYPH_COUNT}")
    font_size = args.glyph_count * FONT_BYTES_PER_GLYPH

    source_exe = args.source_exe.read_bytes()
    if len(source_exe) % USER_DATA_SIZE:
        raise ValueError("source EXE is not a whole number of ISO sectors")
    if FONT_OFFSET + font_size > len(source_exe):
        raise ValueError("wide font range does not fit in source EXE")

    bdf = parse_bdf(args.bdf)
    hangul = ks_x_1001_hangul()
    selected = hangul[: args.glyph_count]
    missing = [character for _, character in selected if ord(character) not in bdf]
    if missing:
        raise ValueError(f"BDF is missing {len(missing)} selected Hangul glyphs")

    glyphs = []
    for _, character in selected:
        row_major = render_glyph(bdf[ord(character)], args.ink_width, 16, 16)
        row_major = shift_row_major(row_major, args.x_shift)
        glyphs.append(row_major_to_split_halves(row_major))
    hangul_font = b"".join(glyphs)
    if len(hangul_font) != font_size:
        raise AssertionError("unexpected corrected font size")
    for glyph in glyphs:
        if row_major_to_split_halves(split_halves_to_row_major(glyph)) != glyph:
            raise AssertionError("split-half round trip failed")

    original_font = source_exe[FONT_OFFSET : FONT_OFFSET + font_size]
    patched_exe = bytearray(source_exe)
    patched_exe[FONT_OFFSET : FONT_OFFSET + font_size] = hangul_font
    patched_exe = bytes(patched_exe)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font_dir = args.output_dir / "font"
    font_dir.mkdir(parents=True, exist_ok=True)
    stem = f"first_{args.glyph_count}_16x16"
    (font_dir / f"ssrw_original_{stem}_split_halves.bin").write_bytes(original_font)
    (font_dir / f"ssrw_hangul_{stem}_split_halves.bin").write_bytes(hangul_font)
    write_mapping(font_dir / f"ssrw_hangul_first_{args.glyph_count}_mapping.tsv", hangul, args.glyph_count)

    metadata = PngInfo()
    metadata.add_text("Font base", "EXE file offset 0x73438; runtime pointer initialized at 0x800BEEC8")
    metadata.add_text("Renderer", "0x800D8BBC: base + glyph_index * 32")
    last_message = message_bytes(args.glyph_count - 1).hex(" ").upper()
    metadata.add_text("Encoding", f"F0-F5 plus trail byte; F0 00 through {last_message}")
    metadata.add_text("Storage", "16 left-column bytes followed by 16 right-column bytes")
    metadata.add_text("Ink width", str(args.ink_width))
    metadata.add_text("X shift", str(args.x_shift))
    original_sheet = draw_sheet(original_font, args.glyph_count, "split-halves", scale=4)
    original_sheet.save(font_dir / f"ssrw_original_first_{args.glyph_count}_16x16.png", optimize=True)
    hangul_sheet = draw_sheet(hangul_font, args.glyph_count, "split-halves", scale=4)
    hangul_sheet.save(
        font_dir / f"ssrw_hangul_first_{args.glyph_count}_16x16.png",
        pnginfo=metadata,
        optimize=True,
    )

    patched_exe_path = args.output_dir / "extracted" / args.source_exe.name
    patched_exe_path.parent.mkdir(parents=True, exist_ok=True)
    patched_exe_path.write_bytes(patched_exe)

    base_name = f"Shin Super Robot Taisen Correct All {args.glyph_count} 16x16 Hangul Probe"
    output_track = args.output_dir / f"{base_name} (Track 1).bin"
    changed_sectors = patch_track(args.track, output_track, source_exe, patched_exe, args.exe_lba)
    verify_patched_track(output_track, patched_exe, args.exe_lba)
    output_track2 = args.output_dir / f"{base_name} (Track 2).bin"
    shutil.copyfile(args.track2, output_track2)
    cue_path = args.output_dir / f"{base_name}.cue"
    cue_path.write_text(
        f'FILE "{output_track.name}" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n"
        f'FILE "{output_track2.name}" BINARY\n'
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:00:00\n",
        encoding="ascii",
    )

    archives: dict[str, dict[str, int]] = {}
    full_wide_font = source_exe[FONT_OFFSET : FONT_OFFSET + 1536 * FONT_BYTES_PER_GLYPH]
    small_font = source_exe[0x72438 : 0x72438 + 256 * 16]
    for name, path in (("BTTMES.BIN", args.bttmes), ("SCEDATA.BIN", args.scedata)):
        if path.exists():
            archive_blob = path.read_bytes()
            archives[name] = {
                **count_wide_references(archive_blob, args.glyph_count),
                "exact_16x16_bitmap_matches": count_exact_unique_glyph_matches(
                    archive_blob, full_wide_font, FONT_BYTES_PER_GLYPH
                ),
                "exact_8x16_bitmap_matches": count_exact_unique_glyph_matches(
                    archive_blob, small_font, 16
                ),
            }
    report = {
        "font_discovery": {
            "small_font": "0x72438, 256 x 16-byte 8x16 glyphs",
            "wide_font": "0x73438, 1536 x 32-byte 16x16 glyphs",
            "wide_storage": "left 8 columns x 16 rows, then right 8 columns x 16 rows",
            "renderer": "0x800D8BBC",
            "pointer_initializer": "0x800BEEC8",
            "original_ink_columns": "0-10 predominantly; column 11 rare; columns 12-15 unused",
        },
        "source_exe_sha256": sha256(source_exe),
        "patched_exe_sha256": sha256(patched_exe),
        "source_font_sha256": sha256(original_font),
        "hangul_font_sha256": sha256(hangul_font),
        "font_offset_hex": f"0x{FONT_OFFSET:X}",
        "font_size_bytes": font_size,
        "glyph_count": args.glyph_count,
        "glyph_cell": "16x16",
        "bytes_per_glyph": FONT_BYTES_PER_GLYPH,
        "message_range": f"F0 00-{last_message}",
        "hangul_range": f"{selected[0][1]}-{selected[-1][1]}",
        "ink_width": args.ink_width,
        "x_shift": args.x_shift,
        "changed_exe_sectors": changed_sectors,
        "archive_wide_glyph_references": archives,
        "source_track_sha256": sha256_file(args.track),
        "patched_track_sha256": sha256_file(output_track),
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
