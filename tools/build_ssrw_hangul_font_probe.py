#!/usr/bin/env python3
"""Build SSRW Hangul font probes and patch a MODE2/2352 track.

The SSRW executable has a 12,256-byte bitmap region at file offset 0x7E458.
The title's live font table is 766 sequential 8x16 cells, one glyph per
16-byte entry.  ``--mode full16`` is only a diagnostic contact-sheet view that
places two neighboring 8x16 entries side by side; it must not be used for the
in-game replacement because those neighboring entries are separate glyphs.
The executable size and all ISO sectors remain unchanged; only the EXE's font
bytes and the EDC/ECC of affected MODE2/Form1 sectors are rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo


RAW_SECTOR_SIZE = 2352
USER_DATA_OFFSET = 0x18
USER_DATA_SIZE = 0x800
EDC_OFFSET = 0x818
ECC_P_OFFSET = 0x81C
ECC_Q_OFFSET = 0x8C8

FONT_OFFSET = 0x7E458
FONT_GLYPH_WIDTH = 16
FONT_GLYPH_HEIGHT = 16
FONT_BYTES_PER_GLYPH = 32
FONT_GLYPH_COUNT = 383
FONT_HALF_GLYPH_COUNT = FONT_GLYPH_COUNT * 2
HALF_GLYPH_WIDTH = 8
HALF_GLYPH_HEIGHT = 16
HALF_BYTES_PER_GLYPH = 16
HALF_GLYPH_COUNT = 766
KSX1001_HANGUL_COUNT = 2350


@dataclass(frozen=True)
class BdfGlyph:
    encoding: int
    width: int
    height: int
    x_offset: int
    y_offset: int
    bitmap: tuple[int, ...]
    bitmap_bits: int


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_bdf(path: Path) -> dict[int, BdfGlyph]:
    lines = path.read_text(encoding="utf-8").splitlines()
    glyphs: dict[int, BdfGlyph] = {}
    position = 0
    while position < len(lines):
        if not lines[position].startswith("STARTCHAR "):
            position += 1
            continue
        position += 1
        encoding: int | None = None
        bbx: tuple[int, int, int, int] | None = None
        bitmap_rows: list[str] = []
        while position < len(lines) and lines[position] != "ENDCHAR":
            line = lines[position]
            if line.startswith("ENCODING "):
                encoding = int(line.split()[1])
            elif line.startswith("BBX "):
                _, width, height, x_offset, y_offset = line.split()
                bbx = (int(width), int(height), int(x_offset), int(y_offset))
            elif line == "BITMAP":
                position += 1
                while position < len(lines) and lines[position] != "ENDCHAR":
                    bitmap_rows.append(lines[position])
                    position += 1
                break
            position += 1
        if encoding is not None and encoding >= 0 and bbx is not None:
            width, height, x_offset, y_offset = bbx
            row_bytes = (width + 7) // 8
            if len(bitmap_rows) != height:
                raise ValueError(
                    f"U+{encoding:04X}: {len(bitmap_rows)} rows, expected {height}"
                )
            expected_hex = row_bytes * 2
            if any(len(row) != expected_hex for row in bitmap_rows):
                raise ValueError(f"U+{encoding:04X}: malformed BDF bitmap row")
            glyphs[encoding] = BdfGlyph(
                encoding=encoding,
                width=width,
                height=height,
                x_offset=x_offset,
                y_offset=y_offset,
                bitmap=tuple(int(row, 16) for row in bitmap_rows),
                bitmap_bits=row_bytes * 8,
            )
        position += 1
    return glyphs


def ks_x_1001_hangul() -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for lead in range(0xB0, 0xC9):
        for trail in range(0xA1, 0xFF):
            code = (lead << 8) | trail
            result.append((code, bytes((lead, trail)).decode("euc_kr")))
    if len(result) != KSX1001_HANGUL_COUNT:
        raise AssertionError(f"built {len(result)} Hangul codes")
    if result[0][1] != "가" or result[-1][1] != "힝":
        raise AssertionError("unexpected KS X 1001 Hangul boundaries")
    return result


def render_glyph(
    glyph: BdfGlyph,
    ink_width: int = 11,
    cell_width: int = FONT_GLYPH_WIDTH,
    cell_height: int = FONT_GLYPH_HEIGHT,
) -> bytes:
    if not 1 <= ink_width <= cell_width:
        raise ValueError(f"ink width must be between 1 and {cell_width}")
    canvas = [[0 for _ in range(cell_width)] for _ in range(cell_height)]
    # Galmuri14 is 14 pixels high.  Put its baseline on row 14, leaving a
    # one-pixel top/bottom margin in the game's 16-row cell.
    baseline_row = cell_height - 2
    top = baseline_row - (glyph.y_offset + glyph.height - 1)
    # The Complete Box reference build uses a 1-pixel left inset and keeps
    # the remaining space on the right.  SSRW's original full-width Han
    # cells use the same visual advance convention; centering an 11-pixel
    # Hangul bitmap would move it one pixel too far right.
    left = 1 if cell_width == FONT_GLYPH_WIDTH else (cell_width - ink_width) // 2
    clipped = 0
    for source_y, row_bits in enumerate(glyph.bitmap):
        target_y = top + source_y
        for source_x in range(glyph.width):
            mask = 1 << (glyph.bitmap_bits - 1 - source_x)
            if not row_bits & mask:
                continue
            if glyph.width <= ink_width:
                scaled_x = source_x + (ink_width - glyph.width) // 2
            else:
                scaled_x = (source_x * ink_width) // glyph.width
            target_x = left + scaled_x
            if 0 <= target_x < cell_width and 0 <= target_y < cell_height:
                canvas[target_y][target_x] = 1
            else:
                clipped += 1
    if clipped:
        raise ValueError(f"U+{glyph.encoding:04X}: clipped {clipped} pixels")
    output = bytearray()
    row_bytes = (cell_width + 7) // 8
    for row in canvas:
        word = 0
        for x, pixel in enumerate(row):
            if pixel:
                word |= 1 << (row_bytes * 8 - 1 - x)
        output.extend(word.to_bytes(row_bytes, "big"))
    return bytes(output)


def render_sheet(
    font: bytes,
    glyph_count: int,
    scale: int = 4,
    labels: bool = True,
    cell_width: int = FONT_GLYPH_WIDTH,
    cell_height: int = FONT_GLYPH_HEIGHT,
    bytes_per_glyph: int = FONT_BYTES_PER_GLYPH,
) -> Image.Image:
    columns = 32
    rows = math.ceil(glyph_count / columns)
    label_height = 8 if labels else 0
    image_cell_width = cell_width * scale
    image_cell_height = (cell_height + label_height) * scale
    image = Image.new("RGB", (columns * image_cell_width, rows * image_cell_height), "white")
    draw = ImageDraw.Draw(image)
    label_font = ImageFont.load_default() if labels else None
    row_bytes = (cell_width + 7) // 8
    for index in range(glyph_count):
        base = index * bytes_per_glyph
        left = (index % columns) * image_cell_width
        top = (index // columns) * image_cell_height
        for y in range(cell_height):
            bits = int.from_bytes(font[base + y * row_bytes : base + (y + 1) * row_bytes], "big")
            for x in range(cell_width):
                if bits & (1 << (row_bytes * 8 - 1 - x)):
                    x0 = left + x * scale
                    y0 = top + y * scale
                    draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill="black")
        if labels:
            draw.text(
                (left + 1, top + cell_height * scale + 1),
                f"{index:03X}",
                fill=(200, 0, 0),
                font=label_font,
            )
    return image


def write_mapping(
    path: Path,
    hangul: list[tuple[int, str]],
    glyph_count: int,
    mode: str,
    bytes_per_glyph: int,
) -> None:
    if mode == "full16":
        rows = [
            "full_glyph_index\teuc_kr\tcharacter\tleft_half_slot\tright_half_slot\tfont_offset"
        ]
        for index, (euc_kr, character) in enumerate(hangul[:glyph_count]):
            half_slot = index * 2
            rows.append(
                f"0x{index:03X}\t0x{euc_kr:04X}\t{character}\t"
                f"0x{half_slot:03X}\t0x{half_slot + 1:03X}\t"
                f"0x{index * bytes_per_glyph:04X}"
            )
    else:
        rows = ["glyph_index\teuc_kr\tcharacter\tfont_slot\tfont_offset"]
        for index, (euc_kr, character) in enumerate(hangul[:glyph_count]):
            rows.append(
                f"0x{index:03X}\t0x{euc_kr:04X}\t{character}\t"
                f"0x{index:03X}\t0x{index * bytes_per_glyph:04X}"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def make_edc_table() -> list[int]:
    table: list[int] = []
    for value in range(256):
        current = value
        for _ in range(8):
            current = (current >> 1) ^ (0xD8018001 if current & 1 else 0)
        table.append(current & 0xFFFFFFFF)
    return table


def make_ecc_tables() -> tuple[list[int], list[int]]:
    forward = [0] * 256
    backward = [0] * 256
    for value in range(256):
        doubled = value << 1
        if doubled & 0x100:
            doubled ^= 0x11D
        forward[value] = doubled
        backward[value ^ doubled] = value
    return forward, backward


EDC_TABLE = make_edc_table()
ECC_FORWARD, ECC_BACKWARD = make_ecc_tables()


def compute_edc(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ EDC_TABLE[(value ^ byte) & 0xFF]
    return value


def compute_ecc(
    address: bytes,
    data: bytes,
    major_count: int,
    minor_count: int,
    major_mult: int,
    minor_inc: int,
) -> bytes:
    size = major_count * minor_count
    output = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            byte = address[index] if index < 4 else data[index - 4]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= byte
            ecc_b ^= byte
            ecc_a = ECC_FORWARD[ecc_a]
        ecc_a = ECC_BACKWARD[ECC_FORWARD[ecc_a] ^ ecc_b]
        output[major] = ecc_a
        output[major + major_count] = ecc_a ^ ecc_b
    return bytes(output)


def rebuild_mode2_form1(sector: bytearray) -> None:
    if len(sector) != RAW_SECTOR_SIZE or sector[0x0F] != 2:
        raise ValueError("expected a MODE2 raw sector")
    if sector[0x12] & 0x20:
        raise ValueError("the EXE extent contains a MODE2 Form 2 sector")
    if sector[0x10:0x14] != sector[0x14:0x18]:
        raise ValueError("MODE2 subheader copies differ")
    edc = compute_edc(sector[0x10:EDC_OFFSET])
    sector[EDC_OFFSET:ECC_P_OFFSET] = edc.to_bytes(4, "little")
    address = bytes(4)
    sector[ECC_P_OFFSET:ECC_Q_OFFSET] = compute_ecc(address, sector[0x10:ECC_P_OFFSET], 86, 24, 2, 86)
    sector[ECC_Q_OFFSET:RAW_SECTOR_SIZE] = compute_ecc(address, sector[0x10:ECC_Q_OFFSET], 52, 43, 86, 88)


def verify_mode2_form1(sector: bytes) -> None:
    if compute_edc(sector[0x10:EDC_OFFSET]) != int.from_bytes(sector[EDC_OFFSET:ECC_P_OFFSET], "little"):
        raise ValueError("EDC verification failed")
    address = bytes(4)
    expected_p = compute_ecc(address, sector[0x10:ECC_P_OFFSET], 86, 24, 2, 86)
    expected_q = compute_ecc(address, sector[0x10:ECC_Q_OFFSET], 52, 43, 86, 88)
    if sector[ECC_P_OFFSET:ECC_Q_OFFSET] != expected_p or sector[ECC_Q_OFFSET:] != expected_q:
        raise ValueError("ECC verification failed")


def patch_track(track_path: Path, output_path: Path, source_exe: bytes, patched_exe: bytes, exe_lba: int) -> int:
    shutil.copyfile(track_path, output_path)
    changed = 0
    sector_count = len(source_exe) // USER_DATA_SIZE
    with output_path.open("r+b") as track:
        for sector_index in range(sector_count):
            position = (exe_lba + sector_index) * RAW_SECTOR_SIZE
            track.seek(position)
            sector = bytearray(track.read(RAW_SECTOR_SIZE))
            if len(sector) != RAW_SECTOR_SIZE:
                raise ValueError(f"short raw sector at LBA {exe_lba + sector_index}")
            original = sector[USER_DATA_OFFSET : USER_DATA_OFFSET + USER_DATA_SIZE]
            expected = source_exe[sector_index * USER_DATA_SIZE : (sector_index + 1) * USER_DATA_SIZE]
            if original != expected:
                raise ValueError(f"source EXE mismatch at sector {sector_index}")
            replacement = patched_exe[sector_index * USER_DATA_SIZE : (sector_index + 1) * USER_DATA_SIZE]
            if replacement == original:
                continue
            sector[USER_DATA_OFFSET : USER_DATA_OFFSET + USER_DATA_SIZE] = replacement
            rebuild_mode2_form1(sector)
            track.seek(position)
            track.write(sector)
            changed += 1
    return changed


def verify_patched_track(track_path: Path, patched_exe: bytes, exe_lba: int) -> None:
    with track_path.open("rb") as track:
        sector_count = len(patched_exe) // USER_DATA_SIZE
        for sector_index in range(sector_count):
            track.seek((exe_lba + sector_index) * RAW_SECTOR_SIZE)
            sector = track.read(RAW_SECTOR_SIZE)
            actual = sector[USER_DATA_OFFSET : USER_DATA_OFFSET + USER_DATA_SIZE]
            expected = patched_exe[sector_index * USER_DATA_SIZE : (sector_index + 1) * USER_DATA_SIZE]
            if actual != expected:
                raise ValueError(f"patched EXE mismatch at sector {sector_index}")
            verify_mode2_form1(sector)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, required=True, help="source Track 1 BIN")
    parser.add_argument("--track2", type=Path, required=True, help="source Track 2 BIN")
    parser.add_argument("--source-exe", type=Path, required=True, help="extracted SLPS_005.50")
    parser.add_argument("--bdf", type=Path, required=True, help="Galmuri14 BDF")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exe-lba", type=int, default=24)
    parser.add_argument("--font-offset", type=lambda value: int(value, 0), default=FONT_OFFSET)
    parser.add_argument(
        "--mode",
        choices=("full16", "half8"),
        default="half8",
        help="treat the bitmap table as 383 16x16 cells or 766 8x16 cells",
    )
    parser.add_argument(
        "--ink-width",
        type=int,
        default=None,
        help="horizontal ink width; defaults to 11 for full16 and 7 for half8",
    )
    args = parser.parse_args()

    if args.mode == "full16":
        cell_width = FONT_GLYPH_WIDTH
        cell_height = FONT_GLYPH_HEIGHT
        bytes_per_glyph = FONT_BYTES_PER_GLYPH
        glyph_count = FONT_GLYPH_COUNT
        default_ink_width = 11
        output_stem = "ssrw_hangul_first_383_16x16"
        storage_description = "Each 16x16 glyph occupies two consecutive 8x16 table halves"
    else:
        cell_width = HALF_GLYPH_WIDTH
        cell_height = HALF_GLYPH_HEIGHT
        bytes_per_glyph = HALF_BYTES_PER_GLYPH
        glyph_count = HALF_GLYPH_COUNT
        default_ink_width = 7
        output_stem = "ssrw_hangul_first_766_8x16"
        storage_description = "Each glyph occupies one consecutive 8x16 table slot"
    ink_width = default_ink_width if args.ink_width is None else args.ink_width
    font_size = glyph_count * bytes_per_glyph

    if args.track.stat().st_size % RAW_SECTOR_SIZE:
        raise ValueError("source Track 1 is not a whole number of raw sectors")
    source_exe = args.source_exe.read_bytes()
    if len(source_exe) % USER_DATA_SIZE:
        raise ValueError("source EXE is not a whole number of ISO sectors")
    if not 0 <= args.font_offset <= len(source_exe) - font_size:
        raise ValueError("font range does not fit in source EXE")

    bdf = parse_bdf(args.bdf)
    hangul = ks_x_1001_hangul()
    selected = hangul[:glyph_count]
    missing = [character for _, character in selected if ord(character) not in bdf]
    if missing:
        raise ValueError(f"BDF is missing {len(missing)} selected Hangul glyphs")
    hangul_font = b"".join(
        render_glyph(
            bdf[ord(character)],
            ink_width,
            cell_width=cell_width,
            cell_height=cell_height,
        )
        for _, character in selected
    )
    if len(hangul_font) != font_size:
        raise AssertionError("unexpected Hangul font size")

    original_font = source_exe[args.font_offset : args.font_offset + len(hangul_font)]
    patched_exe = bytearray(source_exe)
    patched_exe[args.font_offset : args.font_offset + len(hangul_font)] = hangul_font
    patched_exe = bytes(patched_exe)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font_dir = args.output_dir / "font"
    font_dir.mkdir(parents=True, exist_ok=True)
    (font_dir / f"{output_stem}.bin").write_bytes(hangul_font)
    write_mapping(
        font_dir / f"{output_stem}_mapping.tsv",
        hangul,
        glyph_count,
        args.mode,
        bytes_per_glyph,
    )
    metadata = PngInfo()
    metadata.add_text("Encoding", f"KS X 1001 Hangul, first {glyph_count} of {KSX1001_HANGUL_COUNT}")
    metadata.add_text(
        "Glyph format",
        f"{glyph_count} glyphs, {cell_width}x{cell_height}, 1bpp, "
        f"{bytes_per_glyph} bytes/glyph, row-major MSB-first",
    )
    metadata.add_text("Storage", storage_description)
    metadata.add_text("Ink width", str(ink_width))
    metadata.add_text("Glyph range", f"0x000-0x{glyph_count - 1:03X}")
    sheet = render_sheet(
        hangul_font,
        glyph_count,
        labels=True,
        cell_width=cell_width,
        cell_height=cell_height,
        bytes_per_glyph=bytes_per_glyph,
    )
    sheet.save(font_dir / f"{output_stem}.png", pnginfo=metadata, optimize=True)

    patched_exe_path = args.output_dir / "extracted" / args.source_exe.name
    patched_exe_path.parent.mkdir(parents=True, exist_ok=True)
    patched_exe_path.write_bytes(patched_exe)

    output_track = args.output_dir / "Shin Super Robot Taisen Hangul Font Probe (Track 1).bin"
    changed = patch_track(args.track, output_track, source_exe, patched_exe, args.exe_lba)
    verify_patched_track(output_track, patched_exe, args.exe_lba)

    output_track2 = args.output_dir / "Shin Super Robot Taisen Hangul Font Probe (Track 2).bin"
    shutil.copyfile(args.track2, output_track2)
    cue = args.output_dir / "Shin Super Robot Taisen Hangul Font Probe.cue"
    cue.write_text(
        f'FILE "{output_track.name}" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n"
        f'FILE "{output_track2.name}" BINARY\n'
        "  TRACK 02 AUDIO\n"
        "    INDEX 00 00:00:00\n"
        "    INDEX 01 00:02:00\n",
        encoding="ascii",
    )

    report = {
        "source_track": str(args.track),
        "source_track_sha256": sha256_file(args.track),
        "patched_track": str(output_track),
        "patched_track_sha256": sha256_file(output_track),
        "source_exe_sha256": sha256(source_exe),
        "patched_exe_sha256": sha256(patched_exe),
        "source_font_sha256": sha256(original_font),
        "hangul_font_sha256": sha256(hangul_font),
        "font_offset_hex": f"0x{args.font_offset:X}",
        "font_size_bytes": len(hangul_font),
        "mode": args.mode,
        "glyph_count": glyph_count,
        "slot_count": glyph_count,
        "cell_width": cell_width,
        "cell_height": cell_height,
        "bytes_per_glyph": bytes_per_glyph,
        "glyph_format": storage_description,
        "hangul_sequence": (
            f"KS X 1001 Hangul first {len(selected)} of {len(hangul)}, "
            f"EUC-KR 0x{selected[0][0]:04X}-0x{selected[-1][0]:04X}, "
            f"{selected[0][1]}-{selected[-1][1]}"
        ),
        "ink_width": ink_width,
        "exe_lba": args.exe_lba,
        "exe_sector_count": len(source_exe) // USER_DATA_SIZE,
        "changed_exe_sectors": changed,
        "verification": "patched EXE payload and MODE2/Form1 EDC/ECC verified",
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Output cue: {cue.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
