#!/usr/bin/env python3
"""Static regression checks for issue #74.

The report is about a battle-dialogue layout failure followed by an incorrect
attack target.  This check intentionally never starts an emulator.  It proves
the two relevant classes of state independently: the FC09 speaker-name path
and the first-battle executable/map/BTT data path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import build_ssrw_full_translation as build  # noqa: E402
from audit_scedata_event_refs import audit_bttmes  # noqa: E402
from build_ssrw_hangul_font_probe import (  # noqa: E402
    RAW_SECTOR_SIZE,
    USER_DATA_OFFSET,
    USER_DATA_SIZE,
    parse_bdf,
    render_glyph,
    verify_mode2_form1,
)
from extract_ssrw_japanese_text import Codec, srw_lz_decompress  # noqa: E402


FIXED_BATTLE_FILES = {
    "BATTLE.LZB": (965, 26880),
    "MAP.LZB": (986, 122135),
    "BTT/UNTDATA.BIN": (26959, 5176353),
    "MAP/FACEDAT.BIN": (30416, 434528),
    "MAP/MAPDAT.BIN": (29488, 876077),
    "MAP/UNIT.B04": (30629, 32768),
    "MAP/UNIT.CLT": (30645, 2048),
    "MAP/OBJECT.B04": (30646, 32768),
    "MAP/OBJECT.CLT": (30662, 8192),
    "MAP/EVENT.DAT": (30741, 59169),
    "MAP/EVENT2.DAT": (30770, 34854),
    "MAP/REMAP.DAT": (30788, 20254),
}

EXE_REGIONS_UNCHANGED = {
    "face_table": (0x5F280, 0x3000),
    "window_layout": (0xCD4BC, 132),
    "battle_parser": (0xAB7A4, 0x1000),
}


def read_iso_file(track: Path, lba: int, size: int) -> bytes:
    """Read and validate a raw MODE2/2352 file extent."""
    result = bytearray()
    with track.open("rb") as stream:
        for index in range((size + USER_DATA_SIZE - 1) // USER_DATA_SIZE):
            stream.seek((lba + index) * RAW_SECTOR_SIZE)
            sector = stream.read(RAW_SECTOR_SIZE)
            if len(sector) != RAW_SECTOR_SIZE:
                raise AssertionError(
                    f"short MODE2 sector at LBA {lba + index} in {track}"
                )
            verify_mode2_form1(sector)
            result.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
    return bytes(result[:size])


def arena_limit(target: int) -> int | None:
    arenas = [
        spec["arena"] for spec in build.EXE_STRING_POOLS.values()
    ] + [
        build.EXE_SYS_POOL_ARENA,
        build.EXE_DEMO_POOL["arena"],
    ]
    for start, end in arenas:
        if start <= target < end:
            return end
    return None


def check_exe_pools(source: bytes, rebuilt: bytes, build_dir: Path) -> dict[str, Any]:
    """Check self-relative pool pointers and the FC09 short-name record."""
    errors: list[str] = []
    valid_records = 0
    sanitized_invalid = 0
    for label, spec in build.EXE_STRING_POOLS.items():
        table = spec["table"]
        arena_start, arena_end = spec["arena"]
        if rebuilt[table - 4:table] != source[table - 4:table]:
            errors.append(f"{label} announcing stub was overwritten")
        for index in range(spec["entries"]):
            slot = table + index * 4
            source_target = slot + struct.unpack_from("<I", source, slot)[0]
            source_raw = build.read_encoded_string(source, source_target, arena_end)
            if source_raw is None or not arena_start <= source_target < arena_end:
                rebuilt_target = slot + struct.unpack_from("<I", rebuilt, slot)[0]
                limit = arena_limit(rebuilt_target)
                rebuilt_raw = build.read_encoded_string(rebuilt, rebuilt_target, limit)
                if rebuilt_raw != b"\xFF":
                    errors.append(
                        f"{label}[{index}] invalid source pointer was not isolated "
                        f"as an empty record: target=0x{rebuilt_target:X}"
                    )
                sanitized_invalid += 1
                continue

            rebuilt_target = slot + struct.unpack_from("<I", rebuilt, slot)[0]
            limit = arena_limit(rebuilt_target)
            rebuilt_raw = build.read_encoded_string(rebuilt, rebuilt_target, limit)
            if rebuilt_raw is None:
                errors.append(
                    f"{label}[{index}] rebuilt target 0x{rebuilt_target:X} "
                    "is not a terminated record"
                )
            else:
                valid_records += 1

    mapping = {
        row["character"]: int(row["glyph_index"])
        for row in json.loads(
            (build_dir / "hangul_mapping.json").read_text(encoding="utf-8")
        )["entries"]
    }
    codec = Codec(
        json.loads(
            (ROOT / "data" / "ssrw_japanese_font_mapping.json").read_text(
                encoding="utf-8"
            )
        )
    )
    pilot_table = build.EXE_STRING_POOLS["PILOTNAME"]["table"]
    pilot_slot = pilot_table + 4
    pilot_target = pilot_slot + struct.unpack_from("<I", rebuilt, pilot_slot)[0]
    pilot_raw = build.read_encoded_string(rebuilt, pilot_target, arena_limit(pilot_target))
    expected = build.encode_pilot_name_text("아무로", codec, mapping)[0] + b"\xFF"
    if pilot_raw != expected:
        errors.append(
            "PILOTNAME[1] is not the expected 아무로 record: "
            f"got {pilot_raw.hex(' ') if pilot_raw else '<invalid>'}, "
            f"expected {expected.hex(' ')}"
        )
    else:
        # The battle name line is a full-width window - layout record 0 of the
        # table at RAM 0x800FCBBC carries flags bit 2 - so a lone byte is drawn
        # as wide glyph 0x600|byte and advances 3, exactly like a wide pair.
        # Measuring it with the body-text rule of 2 per byte is what made the
        # old compact code page look as though it saved width.  Compare code
        # units against the Japanese name instead: three cells stay three cells.
        retail_target = pilot_slot + struct.unpack_from("<I", source, pilot_slot)[0]
        retail_raw = build.read_encoded_string(
            source, retail_target, arena_limit(retail_target)
        )
        korean_units = build.encoded_display_advance(pilot_raw, full_width=True)
        retail_units = build.encoded_display_advance(retail_raw, full_width=True)
        if retail_raw is None or korean_units != retail_units:
            errors.append(
                "PILOTNAME[1] no longer matches the retail name width: "
                f"{korean_units} vs {retail_units} units"
            )

    bdf = parse_bdf(ROOT / "font" / "Galmuri14.bdf")
    small_font_errors = []
    # Only the two cells the fixed labels use are repainted now; names no
    # longer touch the half-width bank at all.
    for character, index in build.SMALL_LABEL_GLYPHS.items():
        expected_glyph = render_glyph(bdf[ord(character)], 8, 8, 16)
        offset = build.SMALL_FONT_OFFSET + index * build.SMALL_GLYPH_BYTES
        if rebuilt[offset:offset + build.SMALL_GLYPH_BYTES] != expected_glyph:
            small_font_errors.append(character)
    if small_font_errors:
        errors.append(
            "compact label glyphs differ from the recorded 8x16 render: "
            + ", ".join(small_font_errors)
        )
    repainted = [
        b for b in range(256)
        if rebuilt[build.SMALL_FONT_OFFSET + b * 16:build.SMALL_FONT_OFFSET + b * 16 + 16]
        != source[build.SMALL_FONT_OFFSET + b * 16:build.SMALL_FONT_OFFSET + b * 16 + 16]
    ]
    if set(repainted) != set(build.SMALL_LABEL_GLYPHS.values()):
        errors.append(
            "half-width bank repaints %s, expected only %s"
            % ([hex(b) for b in repainted],
               [hex(b) for b in sorted(build.SMALL_LABEL_GLYPHS.values())])
        )

    return {
        "errors": errors,
        "valid_records": valid_records,
        "sanitized_invalid_entries": sanitized_invalid,
        "pilotname_entry_1": pilot_raw.hex(" ") if pilot_raw else None,
        "pilotname_expected": expected.hex(" "),
        "compact_name_advance": (
            build.encoded_display_advance(pilot_raw) if pilot_raw else None
        ),
    }


def check_battle_path(
    retail_track: Path,
    built_track: Path,
    source_exe: bytes,
    rebuilt_exe: bytes,
    source_btt: bytes,
    rebuilt_btt: bytes,
    build_dir: Path,
) -> dict[str, Any]:
    """Check first-battle data and every BTT record edge without executing it."""
    errors: list[str] = []
    unchanged_files: list[str] = []
    for name, (lba, size) in FIXED_BATTLE_FILES.items():
        retail = read_iso_file(retail_track, lba, size)
        rebuilt = read_iso_file(built_track, lba, size)
        if retail != rebuilt:
            errors.append(f"first-battle file changed unexpectedly: {name}")
        else:
            unchanged_files.append(name)

    for name, (offset, size) in EXE_REGIONS_UNCHANGED.items():
        if rebuilt_exe[offset:offset + size] != source_exe[offset:offset + size]:
            errors.append(f"executable battle region changed unexpectedly: {name}")

    if rebuilt_exe[build.EXE_SCEDATA_TABLE_OFFSET:
                   build.EXE_SCEDATA_TABLE_OFFSET + build.EXE_SCEDATA_TABLE_BYTES] != (
        (build_dir / "extracted" / "SCEDATA.BIN").read_bytes()[:build.EXE_SCEDATA_TABLE_BYTES]
    ):
        errors.append("SCEDATA executable mirror does not match the rebuilt archive")
    if rebuilt_exe[build.EXE_BTTMES_TABLE_OFFSET:
                   build.EXE_BTTMES_TABLE_OFFSET + build.EXE_BTTMES_TABLE_BYTES] != rebuilt_btt[:build.EXE_BTTMES_TABLE_BYTES]:
        errors.append("BTTMES executable mirror does not match the rebuilt archive")

    first_offset = 0x93E
    if source_btt[first_offset:first_offset + 4] != bytes.fromhex("FC 08 01 01"):
        errors.append("retail first BTT record is not the expected FC08 bank/message")
    if rebuilt_btt[first_offset:first_offset + 4] != source_btt[first_offset:first_offset + 4]:
        errors.append("first BTT FC08 header changed")

    applied = build_dir / "translation_applied.json"
    semantic = audit_bttmes(source_btt, rebuilt_btt, applied)
    if semantic["index_mismatch_count"] or semantic["graph_mismatch_count"]:
        errors.append("BTT index or script graph mismatch")

    return {
        "errors": errors,
        "unchanged_first_battle_files": unchanged_files,
        "first_btt_header": rebuilt_btt[first_offset:first_offset + 4].hex(" "),
        "btt_semantic_audit": semantic,
    }


def check_scedata(build_dir: Path, rebuilt_exe: bytes) -> dict[str, Any]:
    path = build_dir / "extracted" / "SCEDATA.BIN"
    data = path.read_bytes()
    verification = build.verify_scedata_against_exe(rebuilt_exe, data)
    offsets = struct.unpack_from("<128I", data, 0)
    start = offsets[1]
    end = offsets[2]
    member, consumed = srw_lz_decompress(data[start:end])
    errors = []
    if not 0 < consumed <= end - start:
        errors.append("scenario 1 compressed member consumes outside its allocation")
    if len(member) < 4:
        errors.append("scenario 1 compressed member is empty")
    return {
        "errors": errors,
        "scenario_1_decompressed_bytes": len(member),
        "scenario_1_compressed_bytes": consumed,
        "runtime_verification": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retail-track", type=Path,
                        default=ROOT.parent / "Shin Super Robot Taisen (Track 1).bin")
    parser.add_argument("--build-dir", type=Path,
                        default=ROOT / "work" / "korean_translation_full_fixed")
    args = parser.parse_args()
    build_dir = args.build_dir if args.build_dir.is_absolute() else ROOT / args.build_dir
    retail_track = args.retail_track.resolve()
    built_track = build_dir / "Shin Super Robot Taisen Korean Full Translation (Track 1).bin"
    source_exe = (ROOT / "work" / "extracted" / "SLPS_005.50").read_bytes()
    rebuilt_exe = (build_dir / "extracted" / "SLPS_005.50").read_bytes()
    source_btt = (ROOT / "work" / "extracted" / "BTT" / "BTTMES.BIN").read_bytes()
    rebuilt_btt = (build_dir / "extracted" / "BTT" / "BTTMES.BIN").read_bytes()

    pool_report = check_exe_pools(source_exe, rebuilt_exe, build_dir)
    battle_report = check_battle_path(
        retail_track, built_track, source_exe, rebuilt_exe,
        source_btt, rebuilt_btt, build_dir,
    )
    scedata_report = check_scedata(build_dir, rebuilt_exe)
    errors = pool_report["errors"] + battle_report["errors"] + scedata_report["errors"]
    print(json.dumps({
        "build_dir": str(build_dir),
        "pool": {key: value for key, value in pool_report.items() if key != "errors"},
        "battle": {key: value for key, value in battle_report.items() if key != "errors"},
        "scedata": {key: value for key, value in scedata_report.items() if key != "errors"},
        "errors": errors,
    }, ensure_ascii=True, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
