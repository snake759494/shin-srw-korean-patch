#!/usr/bin/env python3
"""Build a fixed-layout Korean test image for the captured opening scene and menus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import shutil
import struct
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent  # repo root: the build runs with CWD=work/
from typing import Any

from PIL.PngImagePlugin import PngInfo

from build_ssrw_hangul_font_probe import (
    RAW_SECTOR_SIZE,
    USER_DATA_OFFSET,
    USER_DATA_SIZE,
    parse_bdf,
    rebuild_mode2_form1,
    render_glyph,
    verify_mode2_form1,
)
from build_ssrw_hangul_font_probe_fixed import (
    FONT_BYTES_PER_GLYPH,
    FONT_OFFSET,
    row_major_to_split_halves,
    shift_row_major,
)
from extract_ssrw_japanese_text import Codec, srw_lz_decompress
from extract_ssrw_true_fonts import draw_sheet


FONT_GLYPH_COUNT = 1536
SCENARIO_INDEX = 1
EXE_LBA = 24
SCEDATA_LBA = 651
MAPPING_PATH = Path("korean_patch/hangul_mapping.json")

MENU_TRANSLATIONS = {
    "地上": "지상",
    "モノラル": "모노",
    "ステレオ": "입체",
    "分離": "분리",
    "よろしいですか": "확인?",
    "システム": "설정",
    "部隊表": "부대",
    "移動": "이동",
    "自分の命中率が": "명중률",
    "精神": "정신",
    "精神ポイント": "정신력",
    "能力": "능력",
    "全体マップ": "전체맵",
    "精神検索": "정신검색",
    "命令": "명령",
    "作戦目的": "작전목적",
    "精神使用": "정신사용",
    "音楽再生タイプの変更": "음악재생방식",
    "行動終了していないユニットが": "행동안끝난유닛",
    "ユニット能力": "유닛능력",
    "パイロット能力": "조종능력",
    "武器性能": "무기성능",
    "システム設定": "환경설정",
    "サウンド": "음향",
    "特殊操作": "특수조작",
    "ボタン設定": "키설정",
    "勝利条件": "승리조건",
    "敗北条件": "패배조건",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def message_bytes(index: int) -> bytes:
    if not 0 <= index < FONT_GLYPH_COUNT:
        raise ValueError(index)
    return bytes((0xF0 + (index >> 8), index & 0xFF))


def is_hangul(character: str) -> bool:
    return "가" <= character <= "힣"


def walk_raw_hex(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("raw_hex"), str) and "id" in value:
            try:
                yield bytes.fromhex(value["raw_hex"])
            except ValueError:
                pass
        else:
            for child in value.values():
                yield from walk_raw_hex(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_raw_hex(child)


def count_wide_usage(paths: list[Path]) -> Counter[int]:
    usage: Counter[int] = Counter()
    control_args = {
        0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0,
        0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1,
    }
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        for raw in walk_raw_hex(document):
            position = 0
            while position < len(raw):
                opcode = raw[position]
                if 0xF0 <= opcode <= 0xF5 and position + 1 < len(raw):
                    usage[((opcode - 0xF0) << 8) | raw[position + 1]] += 1
                    position += 2
                elif opcode in control_args:
                    position += 1 + control_args[opcode]
                else:
                    position += 1
    return usage


def ordered_hangul(strings: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for text in strings:
        for character in text.replace("<WAIT>", ""):
            if is_hangul(character) and character not in seen:
                seen.add(character)
                output.append(character)
    return output


def load_or_extend_mapping(
    path: Path, required: list[str], usage: Counter[int]
) -> dict[str, int]:
    existing_rows: list[dict[str, Any]] = []
    if path.exists():
        existing_rows = json.loads(path.read_text(encoding="utf-8"))["entries"]
    mapping = {row["character"]: int(row["glyph_index"]) for row in existing_rows}
    occupied = set(mapping.values())
    candidates = sorted(
        (index for index in range(FONT_GLYPH_COUNT) if index not in occupied),
        key=lambda index: (usage[index], index),
    )
    for character in required:
        if character in mapping:
            continue
        if not candidates:
            raise ValueError("wide font table has no slots left")
        mapping[character] = candidates.pop(0)
    ordered_rows = sorted(mapping.items(), key=lambda item: required.index(item[0]) if item[0] in required else -1)
    entries = []
    for sequence, (character, index) in enumerate(ordered_rows):
        entries.append({
            "sequence": sequence,
            "character": character,
            "glyph_index": index,
            "message_bytes": message_bytes(index).hex(" ").upper(),
            "original_text_usage_count": usage[index],
            "font_offset": FONT_OFFSET + index * FONT_BYTES_PER_GLYPH,
            "font_offset_hex": f"0x{FONT_OFFSET + index * FONT_BYTES_PER_GLYPH:X}",
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format": "SSRW F0-F5 wide glyph mapping",
        "policy": "append Hangul in first-appearance order; prefer unused original text slots",
        "entry_count": len(entries),
        "entries": entries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mapping


def encode_text(text: str, codec: Codec, hangul: dict[str, int]) -> bytes:
    output = bytearray()
    position = 0
    while position < len(text):
        if text[position] == "<":
            match = re.match(r"<([0-9A-Fa-f]{2})(?::([0-9A-Fa-f]*))?>", text[position:])
            if match:
                opcode = int(match.group(1), 16)
                arguments = bytes.fromhex(match.group(2) or "")
                expected = {
                    0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0,
                    0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1,
                }.get(opcode)
                if expected is None or len(arguments) != expected:
                    raise ValueError(f"invalid renderer control {match.group(0)!r}")
                output.append(opcode)
                output.extend(arguments)
                position += len(match.group(0))
                continue
        if text.startswith("<WAIT>", position):
            output.append(0xF7)
            position += len("<WAIT>")
            continue
        character = text[position]
        if character == "\n":
            output.append(0xF6)
        elif is_hangul(character):
            output.extend(message_bytes(hangul[character]))
        else:
            variants = codec.inverse.get(character, [])
            if not variants:
                raise ValueError(f"no original encoding for {character!r} in {text!r}")
            output.extend(min(variants, key=lambda value: (len(value), value)))
        position += 1
    return bytes(output)


def patch_scenario(
    source: bytes, translations: dict[str, str], codec: Codec, hangul: dict[str, int]
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    offsets = list(struct.unpack_from("<128I", source, 0))
    start = offsets[SCENARIO_INDEX]
    end = offsets[SCENARIO_INDEX + 1]
    decompressed, consumed = srw_lz_decompress(source[start:end])
    scenario_doc = json.loads(Path("text_extracted/ssrw_scenario_dialogue.json").read_text(encoding="utf-8"))
    records = {row["id"]: row for row in scenario_doc["scenarios"][SCENARIO_INDEX]["records"]}
    patched = bytearray(decompressed)
    applied = []
    for record_id, korean in translations.items():
        row = records[record_id]
        original = bytes.fromhex(row["raw_hex"])
        encoded = encode_text(korean, codec, hangul)
        available = len(original) - 1
        if len(encoded) > available:
            raise ValueError(f"{record_id} exceeds fixed record by {len(encoded) - available} bytes")
        replacement = encoded + bytes(available - len(encoded)) + b"\xFF"
        record_start = int(row["source_offset"])
        if decompressed[record_start:record_start + len(original)] != original:
            raise ValueError(f"source record mismatch: {record_id}")
        patched[record_start:record_start + len(original)] = replacement
        applied.append({
            "id": record_id,
            "japanese": row["japanese"],
            "korean": korean,
            "fixed_record_bytes": len(original),
            "encoded_bytes": len(encoded) + 1,
            "padding_bytes": available - len(encoded),
        })

    encoder_path = Path(__file__).resolve().parent / "srw_lz_enc.py"
    spec = importlib.util.spec_from_file_location("srw_lz_enc", encoder_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load SRW LZ encoder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    compressed = module.compress(bytes(patched), level=8)
    decoded, new_consumed = srw_lz_decompress(compressed)
    if decoded != bytes(patched) or new_consumed != len(compressed):
        raise ValueError("SCEDATA recompression round trip failed")
    allocation = end - start
    if len(compressed) > allocation:
        raise ValueError(f"compressed scenario exceeds allocation by {len(compressed) - allocation} bytes")
    output = bytearray(source)
    output[start:end] = compressed + bytes(allocation - len(compressed))
    report = {
        "scenario_index": SCENARIO_INDEX,
        "member_start": start,
        "member_end": end,
        "allocation_bytes": allocation,
        "original_stream_bytes": consumed,
        "patched_stream_bytes": len(compressed),
        "remaining_padding_bytes": allocation - len(compressed),
        "decompressed_size_unchanged": len(decompressed),
    }
    return bytes(output), applied, report


def patch_menu(
    source: bytes, codec: Codec, hangul: dict[str, int]
) -> tuple[bytes, list[dict[str, Any]]]:
    document = json.loads(Path("text_extracted/ssrw_menu_text.json").read_text(encoding="utf-8"))
    candidates = [row for row in document["records"] if row["japanese"] in MENU_TRANSLATIONS]
    candidates.sort(key=lambda row: (-len(bytes.fromhex(row["raw_hex"])), row["source_offset"]))
    output = bytearray(source)
    occupied: list[tuple[int, int]] = []
    applied = []
    for row in candidates:
        start = int(row["source_offset"])
        original = bytes.fromhex(row["raw_hex"])
        end = start + len(original)
        if any(start < used_end and used_start < end for used_start, used_end in occupied):
            continue
        if source[start:end] != original:
            raise ValueError(f"menu source mismatch at 0x{start:X}")
        korean = MENU_TRANSLATIONS[row["japanese"]]
        encoded = encode_text(korean, codec, hangul)
        if len(encoded) > len(original):
            raise ValueError(f"menu translation too long: {row['japanese']} -> {korean}")
        output[start:end] = encoded + bytes(len(original) - len(encoded))
        occupied.append((start, end))
        applied.append({
            "offset": start,
            "offset_hex": f"0x{start:X}",
            "japanese": row["japanese"],
            "korean": korean,
            "field_bytes": len(original),
            "encoded_bytes": len(encoded),
        })
    return bytes(output), applied


def patch_font(source: bytes, bdf_path: Path, mapping: dict[str, int]) -> tuple[bytes, list[bytes]]:
    bdf = parse_bdf(bdf_path)
    output = bytearray(source)
    preview: list[bytes] = []
    for character, index in mapping.items():
        glyph = bdf.get(ord(character))
        if glyph is None:
            raise ValueError(f"BDF is missing {character}")
        row_major = render_glyph(glyph, 11, 16, 16)
        row_major = shift_row_major(row_major, -1)
        stored = row_major_to_split_halves(row_major)
        offset = FONT_OFFSET + index * FONT_BYTES_PER_GLYPH
        output[offset:offset + FONT_BYTES_PER_GLYPH] = stored
        preview.append(stored)
    return bytes(output), preview


def patch_extent(track, lba: int, source: bytes, patched: bytes) -> int:
    if len(source) != len(patched):
        raise ValueError("extent size changed")
    changed = 0
    for sector_index in range(math.ceil(len(source) / USER_DATA_SIZE)):
        file_start = sector_index * USER_DATA_SIZE
        file_end = min(file_start + USER_DATA_SIZE, len(source))
        size = file_end - file_start
        position = (lba + sector_index) * RAW_SECTOR_SIZE
        track.seek(position)
        sector = bytearray(track.read(RAW_SECTOR_SIZE))
        if len(sector) != RAW_SECTOR_SIZE:
            raise ValueError(f"short raw sector at LBA {lba + sector_index}")
        actual = sector[USER_DATA_OFFSET:USER_DATA_OFFSET + size]
        if actual != source[file_start:file_end]:
            raise ValueError(f"source mismatch at LBA {lba + sector_index}")
        replacement = patched[file_start:file_end]
        if replacement == actual:
            continue
        sector[USER_DATA_OFFSET:USER_DATA_OFFSET + size] = replacement
        rebuild_mode2_form1(sector)
        track.seek(position)
        track.write(sector)
        changed += 1
    return changed


def verify_extent(track, lba: int, patched: bytes) -> None:
    for sector_index in range(math.ceil(len(patched) / USER_DATA_SIZE)):
        file_start = sector_index * USER_DATA_SIZE
        file_end = min(file_start + USER_DATA_SIZE, len(patched))
        size = file_end - file_start
        track.seek((lba + sector_index) * RAW_SECTOR_SIZE)
        sector = track.read(RAW_SECTOR_SIZE)
        if sector[USER_DATA_OFFSET:USER_DATA_OFFSET + size] != patched[file_start:file_end]:
            raise ValueError(f"verification mismatch at LBA {lba + sector_index}")
        verify_mode2_form1(sector)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, default=Path("Shin Super Robot Taisen (Track 1).bin"))
    parser.add_argument("--track2", type=Path, default=Path("Shin Super Robot Taisen (Track 2).bin"))
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument("--scedata", type=Path, default=Path("extracted/SCEDATA.BIN"))
    parser.add_argument("--bdf", type=Path, default=_REPO / "font" / "Galmuri14.bdf")
    parser.add_argument("--output-dir", type=Path, default=Path("test_build_screenshot_ko"))
    args = parser.parse_args()

    translation_doc = json.loads(Path("screenshot_translation_ko.json").read_text(encoding="utf-8"))
    translations = translation_doc["translations"]
    expected_ids = [f"SCE-001-{index:04d}" for index in range(4, 93)]
    if list(translations) != expected_ids:
        raise ValueError("translation IDs must be the complete ordered screenshot range 4-92")

    font_doc = json.loads(Path("text_extracted/ssrw_japanese_font_mapping.json").read_text(encoding="utf-8"))
    codec = Codec(font_doc)
    usage = count_wide_usage([
        Path("text_extracted/ssrw_scenario_dialogue.json"),
        Path("text_extracted/ssrw_battle_dialogue.json"),
        Path("text_extracted/ssrw_menu_text.json"),
    ])
    required = ordered_hangul(list(translations.values()) + list(MENU_TRANSLATIONS.values()))
    hangul_mapping = load_or_extend_mapping(MAPPING_PATH, required, usage)
    if any(usage[index] for index in hangul_mapping.values()):
        used_count = sum(1 for index in hangul_mapping.values() if usage[index])
        raise ValueError(f"not enough unused glyph slots; {used_count} assigned slots are in use")

    source_exe = args.exe.read_bytes()
    source_scedata = args.scedata.read_bytes()
    menu_exe, menu_applied = patch_menu(source_exe, codec, hangul_mapping)
    patched_exe, preview_glyphs = patch_font(menu_exe, args.bdf, hangul_mapping)
    patched_scedata, scenario_applied, compression_report = patch_scenario(
        source_scedata, translations, codec, hangul_mapping
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = args.output_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    (extracted_dir / args.exe.name).write_bytes(patched_exe)
    (extracted_dir / args.scedata.name).write_bytes(patched_scedata)
    shutil.copyfile(MAPPING_PATH, args.output_dir / "hangul_mapping.json")
    (args.output_dir / "translation_applied.json").write_text(json.dumps({
        "scenario": scenario_applied,
        "menus": menu_applied,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    font_dir = args.output_dir / "font"
    font_dir.mkdir(exist_ok=True)
    metadata = PngInfo()
    metadata.add_text("Cell", "16x16 split halves; ink width 11; x shift -1")
    metadata.add_text("Order", "first appearance in translated screenshot records, then menus")
    sheet = draw_sheet(b"".join(preview_glyphs), len(preview_glyphs), "split-halves", scale=4)
    sheet.save(font_dir / "hangul_mapping_preview.png", pnginfo=metadata, optimize=True)

    base = "Shin Super Robot Taisen Screenshot Korean Test"
    output_track = args.output_dir / f"{base} (Track 1).bin"
    shutil.copyfile(args.track, output_track)
    with output_track.open("r+b") as track:
        changed_exe = patch_extent(track, EXE_LBA, source_exe, patched_exe)
        changed_scedata = patch_extent(track, SCEDATA_LBA, source_scedata, patched_scedata)
    with output_track.open("rb") as track:
        verify_extent(track, EXE_LBA, patched_exe)
        verify_extent(track, SCEDATA_LBA, patched_scedata)

    output_track2 = args.output_dir / f"{base} (Track 2).bin"
    shutil.copyfile(args.track2, output_track2)
    cue = args.output_dir / f"{base}.cue"
    cue.write_text(
        f'FILE "{output_track.name}" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n"
        f'FILE "{output_track2.name}" BINARY\n'
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:00:00\n",
        encoding="ascii",
    )

    report = {
        "build": "screenshot Korean fixed-layout test",
        "scenario_records": len(scenario_applied),
        "menu_fields": len(menu_applied),
        "hangul_glyphs": len(hangul_mapping),
        "all_assigned_slots_previously_unused": True,
        "font": {"offset_hex": f"0x{FONT_OFFSET:X}", "cell": "16x16", "ink_width": 11, "x_shift": -1},
        "scedata": compression_report,
        "changed_sectors": {"SLPS_005.50": changed_exe, "SCEDATA.BIN": changed_scedata},
        "sha256": {
            output_track.name: sha256(output_track.read_bytes()),
            output_track2.name: sha256(output_track2.read_bytes()),
            cue.name: sha256(cue.read_bytes()),
            "patched_SLPS_005.50": sha256(patched_exe),
            "patched_SCEDATA.BIN": sha256(patched_scedata),
        },
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
