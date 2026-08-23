#!/usr/bin/env python3
"""Build and verify a variable-length name/dialogue expansion test image."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent  # repo root: the build runs with CWD=work/
from typing import Any

from build_ssrw_hangul_font_probe import (
    RAW_SECTOR_SIZE,
    USER_DATA_OFFSET,
    USER_DATA_SIZE,
    rebuild_mode2_form1,
    verify_mode2_form1,
)
from build_ssrw_screenshot_korean_test import (
    EXE_LBA,
    MAPPING_PATH,
    SCEDATA_LBA,
    count_wide_usage,
    encode_text,
    load_or_extend_mapping,
    ordered_hangul,
    patch_extent,
    patch_font,
    patch_menu,
    patch_scenario,
    sha256,
    verify_extent,
)
from extract_ssrw_japanese_text import (
    Codec,
    SCENARIO_TEXT_TAIL_SIGNATURE,
    srw_lz_decompress,
    true_records,
)


SCENARIO_INDEX = 1
PVD_LBA = 16
ROOT_DIRECTORY_LBA = 22
AUDIO_PREGAP_SECTORS = 150

EXPANSION_SUFFIXES = [
    "확장 대사 01입니다.\n한글 대사를 계속 더해\n다음 대사를 확인합니다",
    "확장 대사 02입니다.\n파일 크기가 커져도\n정상 실행을 확인합니다",
    "확장 대사 03입니다.\n압축 대사를 다시 만들고\n다음 장면을 확인합니다",
    "확장 대사 04입니다.\n파일 위치가 이동해도\n다음 대사를 확인합니다",
    "확장 대사 05입니다.\n문자 주소가 변해도\n정상 실행을 확인합니다",
    "확장 대사 06입니다.\n캐릭터 이름과 대사를 함께\n확장해 확인합니다",
    "확장 대사 07입니다.\n대사 파일이 커져도\n다른 위치에서 확인합니다",
    "확장 대사 08입니다.\n다음 장면과 설정이\n정상인지 확인합니다",
    "확장 대사 09입니다.\n압축을 다시 하고\n전체 대사를 비교합니다",
    "확장 대사 10입니다.\n원본을 넘은 대사도\n정상 실행을 확인합니다",
    "확장 대사 11입니다.\n여러 대사를 늘려\n주소 변화를 확인합니다",
    "확장 대사 12입니다.\n마지막 확장 문장도\n정상 실행을 확인합니다",
]

SPEAKER_EXPANSIONS = (
    ("리리「", "리리나「"),
    ("히로「", "히이로「"),
    ("히「", "히이로「"),
    ("박사「", "하마구치 박사「"),
    ("켄「", "켄이치「"),
    ("미츠「", "미츠요「"),
    ("히요「", "히요시「"),
    ("다이「", "다이지로「"),
    ("메구「", "메구미「"),
    ("메「", "메구미「"),
    ("잇페「", "잇페이「"),
)


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) // boundary * boundary


def load_encoder():
    import importlib.util

    path = Path(__file__).resolve().parent / "srw_lz_enc.py"
    spec = importlib.util.spec_from_file_location("srw_lz_enc_expansion", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load SRW LZ encoder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expanded_translations(compact: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    output = dict(compact)
    name_changes = []
    for record_id, original in list(output.items()):
        expanded = original
        applied = []
        for short, full in SPEAKER_EXPANSIONS:
            count = expanded.count(short)
            if count:
                expanded = expanded.replace(short, full)
                applied.append({"short": short[:-1], "full": full[:-1], "count": count})
        if applied:
            name_changes.append({"id": record_id, "changes": applied})
        output[record_id] = expanded
    for ordinal, suffix in enumerate(EXPANSION_SUFFIXES, start=4):
        record_id = f"SCE-001-{ordinal:04d}"
        output[record_id] += "<WAIT>" + suffix
    return output, name_changes


def rebuild_expanded_scedata(
    source: bytes,
    translations: dict[str, str],
    codec: Codec,
    hangul: dict[str, int],
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    pointers = list(struct.unpack_from("<128I", source, 0))
    start = pointers[SCENARIO_INDEX]
    end = pointers[SCENARIO_INDEX + 1]
    old_member = source[start:end]
    decompressed, old_consumed = srw_lz_decompress(old_member)

    scenario_doc = json.loads(Path("text_extracted/ssrw_scenario_dialogue.json").read_text(encoding="utf-8"))
    scenario = scenario_doc["scenarios"][SCENARIO_INDEX]
    records = scenario["records"]
    pool_start = int(scenario["text_pool_start"])
    pool_end = int(scenario["text_pool_end"])
    old_signature = decompressed.find(SCENARIO_TEXT_TAIL_SIGNATURE, pool_end)
    if old_signature < 0:
        raise ValueError("scenario tail signature was not found")

    for left, right in zip(records, records[1:]):
        left_end = int(left["source_offset"]) + len(bytes.fromhex(left["raw_hex"]))
        if left_end != int(right["source_offset"]):
            raise ValueError(f"non-contiguous records at {left['id']}")
    first_start = int(records[0]["source_offset"])
    last_end = int(records[-1]["source_offset"]) + len(bytes.fromhex(records[-1]["raw_hex"]))
    if first_start != pool_start or last_end != pool_end:
        raise ValueError("record list does not cover the complete text pool")

    new_pool = bytearray()
    applied = []
    for row in records:
        record_id = row["id"]
        old_start = int(row["source_offset"])
        old_raw = decompressed[old_start:old_start + len(bytes.fromhex(row["raw_hex"]))]
        if record_id in translations:
            encoded = encode_text(translations[record_id], codec, hangul) + b"\xFF"
            new_raw = encoded
            applied.append({
                "id": record_id,
                "old_record_bytes": len(old_raw),
                "new_record_bytes": len(new_raw),
                "growth_bytes": len(new_raw) - len(old_raw),
                "korean": translations[record_id],
            })
        else:
            new_raw = old_raw
        new_pool.extend(new_raw)

    new_signature = align(pool_start + len(new_pool), 4)
    gap = bytes(new_signature - (pool_start + len(new_pool)))
    rebuilt = bytearray(decompressed[:pool_start] + bytes(new_pool) + gap + decompressed[old_signature:])
    tail_delta = new_signature - old_signature
    old_headers = list(struct.unpack_from("<14I", decompressed, 0))
    new_headers = []
    for index, value in enumerate(old_headers):
        adjusted = value + tail_delta if value >= old_signature else value
        struct.pack_into("<I", rebuilt, index * 4, adjusted)
        new_headers.append(adjusted)
    if rebuilt.find(SCENARIO_TEXT_TAIL_SIGNATURE, pool_start) != new_signature:
        raise ValueError("rebuilt tail signature offset mismatch")

    encoder = load_encoder()
    compressed = encoder.compress(bytes(rebuilt), level=8)
    round_trip, consumed = srw_lz_decompress(compressed)
    if round_trip != bytes(rebuilt) or consumed != len(compressed):
        raise ValueError("expanded scenario LZ round trip failed")
    new_chunk = compressed + bytes(align(len(compressed), 4) - len(compressed))
    old_allocation = end - start
    if len(new_chunk) <= old_allocation:
        raise ValueError(
            f"expansion did not outgrow original member allocation: {len(new_chunk)} <= {old_allocation}"
        )

    chunks = []
    for index, member_start in enumerate(pointers):
        member_end = pointers[index + 1] if index + 1 < len(pointers) else len(source)
        chunks.append(source[member_start:member_end])
    chunks[SCENARIO_INDEX] = new_chunk
    output = bytearray(0x200)
    new_pointers = []
    for chunk in chunks:
        new_pointers.append(len(output))
        output.extend(chunk)
    struct.pack_into("<128I", output, 0, *new_pointers)

    if len(output) <= len(source):
        raise ValueError("SCEDATA file did not grow")
    for index, member_start in enumerate(new_pointers):
        member_end = new_pointers[index + 1] if index + 1 < len(new_pointers) else len(output)
        decoded, _ = srw_lz_decompress(bytes(output[member_start:member_end]))
        if index == SCENARIO_INDEX:
            expected = bytes(rebuilt)
        else:
            old_end = pointers[index + 1] if index + 1 < len(pointers) else len(source)
            expected, _ = srw_lz_decompress(source[pointers[index]:old_end])
        if decoded != expected:
            raise ValueError(f"SCEDATA member verification failed at {index}")

    ranges = true_records(bytes(rebuilt), pool_start, pool_start + len(new_pool))
    if len(ranges) != len(records):
        raise ValueError(f"record count changed: {len(ranges)} != {len(records)}")
    for row in applied:
        ordinal = int(row["id"].rsplit("-", 1)[1])
        record_start, record_end = ranges[ordinal]
        expected = encode_text(row["korean"], codec, hangul) + b"\xFF"
        if bytes(rebuilt[record_start:record_end]) != expected:
            raise ValueError(f"expanded record verification failed: {row['id']}")

    report = {
        "member_index": SCENARIO_INDEX,
        "record_count": len(records),
        "translated_variable_records": len(applied),
        "old_decompressed_bytes": len(decompressed),
        "new_decompressed_bytes": len(rebuilt),
        "decompressed_growth_bytes": len(rebuilt) - len(decompressed),
        "old_text_pool_bytes": pool_end - pool_start,
        "new_text_pool_bytes": len(new_pool),
        "text_pool_growth_bytes": len(new_pool) - (pool_end - pool_start),
        "old_tail_offset": old_signature,
        "new_tail_offset": new_signature,
        "tail_shift_bytes": tail_delta,
        "old_header_targets": old_headers,
        "new_header_targets": new_headers,
        "shifted_header_target_count": sum(a != b for a, b in zip(old_headers, new_headers)),
        "old_compressed_stream_bytes": old_consumed,
        "new_compressed_stream_bytes": len(compressed),
        "old_member_allocation_bytes": old_allocation,
        "new_member_allocation_bytes": len(new_chunk),
        "member_growth_bytes": len(new_chunk) - old_allocation,
        "old_scedata_bytes": len(source),
        "new_scedata_bytes": len(output),
        "scedata_growth_bytes": len(output) - len(source),
        "outer_pointer_count": len(new_pointers),
        "shifted_outer_pointer_count": sum(a != b for a, b in zip(pointers, new_pointers)),
    }
    return bytes(output), report, applied


def bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def make_mode2_form1_sector(lba: int, user_data: bytes) -> bytes:
    if len(user_data) > USER_DATA_SIZE:
        raise ValueError("sector user data is too large")
    absolute = lba + 150
    minute, remainder = divmod(absolute, 75 * 60)
    second, frame = divmod(remainder, 75)
    sector = bytearray(RAW_SECTOR_SIZE)
    sector[:12] = b"\x00" + b"\xFF" * 10 + b"\x00"
    sector[12:16] = bytes((bcd(minute), bcd(second), bcd(frame), 2))
    sector[16:24] = b"\x00\x00\x08\x00" * 2
    sector[USER_DATA_OFFSET:USER_DATA_OFFSET + len(user_data)] = user_data
    rebuild_mode2_form1(sector)
    verify_mode2_form1(sector)
    return bytes(sector)


def directory_records(user_data: bytes) -> list[dict[str, Any]]:
    output = []
    position = 0
    while position < len(user_data):
        length = user_data[position]
        if length == 0:
            position = align(position + 1, USER_DATA_SIZE)
            continue
        record = user_data[position:position + length]
        name_length = record[32]
        name = record[33:33 + name_length].decode("ascii", "replace")
        output.append({
            "offset": position,
            "length": length,
            "name": name,
            "extent": struct.unpack_from("<I", record, 2)[0],
            "size": struct.unpack_from("<I", record, 10)[0],
        })
        position += length
    return output


def set_both_endian_u32(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", blob, offset, value)
    struct.pack_into(">I", blob, offset + 4, value)


def patch_iso_metadata_and_append(
    track_path: Path,
    source_track: Path,
    patched_exe: bytes,
    source_exe: bytes,
    expanded_scedata: bytes,
) -> dict[str, Any]:
    old_track_sectors = source_track.stat().st_size // RAW_SECTOR_SIZE
    new_scedata_lba = old_track_sectors
    added_sectors = math.ceil(len(expanded_scedata) / USER_DATA_SIZE)
    new_track_sectors = old_track_sectors + added_sectors

    with track_path.open("r+b") as track:
        changed_exe = patch_extent(track, EXE_LBA, source_exe, patched_exe)

        track.seek(PVD_LBA * RAW_SECTOR_SIZE)
        pvd_sector = bytearray(track.read(RAW_SECTOR_SIZE))
        pvd = bytearray(pvd_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
        if pvd[:7] != b"\x01CD001\x01":
            raise ValueError("primary volume descriptor signature mismatch")
        old_volume_sectors = struct.unpack_from("<I", pvd, 80)[0]
        if struct.unpack_from(">I", pvd, 84)[0] != old_volume_sectors:
            raise ValueError("PVD endian copies differ")
        new_volume_sectors = old_volume_sectors + added_sectors
        set_both_endian_u32(pvd, 80, new_volume_sectors)
        pvd_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE] = pvd
        rebuild_mode2_form1(pvd_sector)
        track.seek(PVD_LBA * RAW_SECTOR_SIZE)
        track.write(pvd_sector)

        track.seek(ROOT_DIRECTORY_LBA * RAW_SECTOR_SIZE)
        root_sector = bytearray(track.read(RAW_SECTOR_SIZE))
        root = bytearray(root_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
        records = {row["name"]: row for row in directory_records(root)}
        for required in ("SCEDATA.BIN;1", "NULL.DA;1"):
            if required not in records:
                raise ValueError(f"missing root directory record: {required}")
        sce_record = records["SCEDATA.BIN;1"]
        null_record = records["NULL.DA;1"]
        old_scedata_lba = sce_record["extent"]
        old_scedata_size = sce_record["size"]
        old_null_lba = null_record["extent"]
        new_null_lba = new_track_sectors + AUDIO_PREGAP_SECTORS
        set_both_endian_u32(root, sce_record["offset"] + 2, new_scedata_lba)
        set_both_endian_u32(root, sce_record["offset"] + 10, len(expanded_scedata))
        set_both_endian_u32(root, null_record["offset"] + 2, new_null_lba)
        root_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE] = root
        rebuild_mode2_form1(root_sector)
        track.seek(ROOT_DIRECTORY_LBA * RAW_SECTOR_SIZE)
        track.write(root_sector)

        track.seek(0, 2)
        for sector_index in range(added_sectors):
            start = sector_index * USER_DATA_SIZE
            user = expanded_scedata[start:start + USER_DATA_SIZE]
            track.write(make_mode2_form1_sector(new_scedata_lba + sector_index, user))

    if track_path.stat().st_size != source_track.stat().st_size + added_sectors * RAW_SECTOR_SIZE:
        raise ValueError("Track 1 physical size growth mismatch")
    with track_path.open("rb") as track:
        verify_extent(track, EXE_LBA, patched_exe)
        track.seek(PVD_LBA * RAW_SECTOR_SIZE)
        verify_mode2_form1(track.read(RAW_SECTOR_SIZE))
        track.seek(ROOT_DIRECTORY_LBA * RAW_SECTOR_SIZE)
        root_sector = track.read(RAW_SECTOR_SIZE)
        verify_mode2_form1(root_sector)
        root = root_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE]
        records = {row["name"]: row for row in directory_records(root)}
        if records["SCEDATA.BIN;1"]["extent"] != new_scedata_lba:
            raise ValueError("SCEDATA relocated extent verification failed")
        if records["SCEDATA.BIN;1"]["size"] != len(expanded_scedata):
            raise ValueError("SCEDATA directory size verification failed")
        if records["NULL.DA;1"]["extent"] != new_null_lba:
            raise ValueError("audio pseudo-file relocation verification failed")
        extracted = bytearray()
        for sector_index in range(added_sectors):
            track.seek((new_scedata_lba + sector_index) * RAW_SECTOR_SIZE)
            sector = track.read(RAW_SECTOR_SIZE)
            verify_mode2_form1(sector)
            extracted.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
        if bytes(extracted[:len(expanded_scedata)]) != expanded_scedata:
            raise ValueError("relocated SCEDATA extraction verification failed")

    return {
        "old_track_sectors": old_track_sectors,
        "new_track_sectors": new_track_sectors,
        "added_track_sectors": added_sectors,
        "old_track_bytes": source_track.stat().st_size,
        "new_track_bytes": track_path.stat().st_size,
        "track_growth_bytes": track_path.stat().st_size - source_track.stat().st_size,
        "old_volume_sectors": old_volume_sectors,
        "new_volume_sectors": new_volume_sectors,
        "old_scedata_lba": old_scedata_lba,
        "new_scedata_lba": new_scedata_lba,
        "old_directory_scedata_size": old_scedata_size,
        "new_directory_scedata_size": len(expanded_scedata),
        "old_audio_pseudo_file_lba": old_null_lba,
        "new_audio_pseudo_file_lba": new_null_lba,
        "patched_exe_sectors": changed_exe,
        "verified_appended_form1_sectors": added_sectors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, default=Path("Shin Super Robot Taisen (Track 1).bin"))
    parser.add_argument("--track2", type=Path, default=Path("Shin Super Robot Taisen (Track 2).bin"))
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument("--scedata", type=Path, default=Path("extracted/SCEDATA.BIN"))
    parser.add_argument("--bdf", type=Path, default=_REPO / "font" / "Galmuri14.bdf")
    parser.add_argument("--output-dir", type=Path, default=Path("test_build_expansion_ko"))
    args = parser.parse_args()

    compact_doc = json.loads(Path("screenshot_translation_ko.json").read_text(encoding="utf-8"))
    compact = compact_doc["translations"]
    expanded, name_changes = expanded_translations(compact)
    mapping_doc = json.loads(Path("text_extracted/ssrw_japanese_font_mapping.json").read_text(encoding="utf-8"))
    codec = Codec(mapping_doc)
    usage = count_wide_usage([
        Path("text_extracted/ssrw_scenario_dialogue.json"),
        Path("text_extracted/ssrw_battle_dialogue.json"),
        Path("text_extracted/ssrw_menu_text.json"),
    ])
    existing = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))["entries"]
    required = [row["character"] for row in existing]
    for character in ordered_hangul(list(expanded.values())):
        if character not in required:
            required.append(character)
    hangul = load_or_extend_mapping(MAPPING_PATH, required, usage)
    if any(usage[index] for index in hangul.values()):
        raise ValueError("expanded mapping consumed a glyph used by original text")

    source_exe = args.exe.read_bytes()
    source_scedata = args.scedata.read_bytes()
    menu_exe, menu_applied = patch_menu(source_exe, codec, hangul)
    patched_exe, _ = patch_font(menu_exe, args.bdf, hangul)
    baseline_scedata, _, _ = patch_scenario(source_scedata, compact, codec, hangul)
    expanded_scedata, scenario_report, applied = rebuild_expanded_scedata(
        baseline_scedata, expanded, codec, hangul
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = args.output_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    (extracted_dir / args.exe.name).write_bytes(patched_exe)
    (extracted_dir / args.scedata.name).write_bytes(expanded_scedata)
    shutil.copyfile(MAPPING_PATH, args.output_dir / "hangul_mapping.json")
    (args.output_dir / "expansion_applied.json").write_text(json.dumps({
        "speaker_name_expansions": name_changes,
        "dialogue_records": applied,
        "menu_fields": menu_applied,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base = "Shin Super Robot Taisen Korean Name Dialogue Expansion Test"
    output_track = args.output_dir / f"{base} (Track 1).bin"
    shutil.copyfile(args.track, output_track)
    iso_report = patch_iso_metadata_and_append(
        output_track, args.track, patched_exe, source_exe, expanded_scedata
    )
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
        "build": "variable-length Korean speaker-name and dialogue expansion test",
        "expanded_speaker_record_count": len(name_changes),
        "expanded_dialogue_page_record_count": len(EXPANSION_SUFFIXES),
        "variable_translation_record_count": len(applied),
        "hangul_mapping_count": len(hangul),
        "all_hangul_slots_unused_by_original_text": True,
        "scenario_and_archive_growth": scenario_report,
        "iso_and_track_growth": iso_report,
        "sha256": {
            output_track.name: sha256(output_track.read_bytes()),
            output_track2.name: sha256(output_track2.read_bytes()),
            cue.name: sha256(cue.read_bytes()),
            "expanded_SCEDATA.BIN": sha256(expanded_scedata),
            "patched_SLPS_005.50": sha256(patched_exe),
        },
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
