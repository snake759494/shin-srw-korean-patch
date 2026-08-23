#!/usr/bin/env python3
"""Extract reinsertion-oriented Japanese text from Shin Super Robot Wars.

Outputs scenario dialogue from LZ-compressed SCEDATA members, FC 08 battle
messages from BTTMES, and an offset catalogue for menu strings in the PS-X
executable.  The original byte stream and every renderer control are retained.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import struct
from typing import Any, Iterable


CONTROL_ARGS = {
    0xF6: 0,
    0xF7: 0,
    0xF8: 1,
    0xF9: 1,
    0xFA: 0,
    0xFB: 2,
    0xFC: 2,
    0xFD: 2,
    0xFE: 1,
}
CONTROL_NAMES = {
    0xF6: "line_break",
    0xF7: "wait_or_page",
    0xF8: "control_f8",
    0xF9: "control_f9",
    0xFA: "control_fa",
    0xFB: "control_fb",
    0xFC: "control_fc",
    0xFD: "control_fd",
    0xFE: "control_fe",
    0xFF: "terminator",
}

MENU_ANCHORS = (
    "ターン終了", "部隊表", "全体マップ", "精神検索", "精神使用", "命令",
    "システム", "作戦目的", "セーブ", "セーブします", "よろしいですか",
    "行動終了していないユニットが", "ユニット能力", "パイロット能力",
    "武器性能", "システム設定", "ボタン設定", "サウンド", "ステレオ",
    "モノラル", "特殊操作", "音楽再生タイプの変更", "勝利条件",
    "敗北条件", "敵の全滅", "味方の全滅", "移動", "分離", "地上",
    "精神", "能力", "精神ポイント", "自分の命中率が",
)

# Every real decompressed scenario places this shared binary lookup table
# immediately after its final FF-terminated text record (with 4-7 zero bytes
# of alignment before it).  It appears exactly once in all 73 distinct
# members and provides the true text-pool end; header targets occur later.
SCENARIO_TEXT_TAIL_SIGNATURE = bytes.fromhex("74 01 A6 00 E8 00 9E 00")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def srw_lz_decompress(source: bytes, position: int = 0) -> tuple[bytes, int]:
    """Decode the SRW MSB-first LZ stream used by each SCEDATA member."""
    output = bytearray()
    bit_buffer = 0
    counter = 0x80

    def bit() -> int:
        nonlocal bit_buffer, counter, position
        counter <<= 1
        if counter & 0x100:
            counter = 1
            bit_buffer = source[position]
            position += 1
        bit_buffer = (bit_buffer << 1) & 0x1FF
        return (bit_buffer >> 8) & 1

    while True:
        if bit():
            output.append(source[position])
            position += 1
        elif bit():
            word = (source[position] << 8) | source[position + 1]
            position += 2
            displacement = (word >> 3) - 0x2000
            length = word & 7
            if length:
                length += 2
            else:
                extension = source[position]
                position += 1
                if extension == 0:
                    break
                length = extension + 1
            cursor = len(output) + displacement
            for _ in range(length):
                output.append(output[cursor])
                cursor += 1
        else:
            code = (bit() << 1) | bit()
            displacement = source[position] - 0x100
            position += 1
            cursor = len(output) + displacement
            for _ in range(code + 2):
                output.append(output[cursor])
                cursor += 1
    return bytes(output), position


class Codec:
    def __init__(self, mapping: dict[str, Any]):
        self.small = {int(row["byte"]): row for row in mapping["small_rows"]}
        self.wide = {int(row["glyph_index"]): row for row in mapping["wide_rows"]}
        inverse: dict[str, list[bytes]] = defaultdict(list)
        for index, row in self.small.items():
            if row.get("character"):
                inverse[row["character"]].append(bytes((index,)))
        for index, row in self.wide.items():
            if row.get("character"):
                inverse[row["character"]].append(
                    bytes((0xF0 + (index >> 8), index & 0xFF))
                )
        self.inverse = dict(inverse)

    def tokenize(self, raw: bytes, stop_at_terminator: bool = False) -> list[dict[str, Any]]:
        tokens: list[dict[str, Any]] = []
        position = 0
        while position < len(raw):
            opcode = raw[position]
            if opcode < 0xF0:
                row = self.small.get(opcode, {})
                character = {
                    0x00: " ", 0x05: "ν", 0x0C: "V",
                    0xE8: "『", 0xE9: "』", 0xEA: "○",
                }.get(
                    opcode, row.get("character")
                )
                tokens.append({
                    "type": "glyph", "offset": position, "length": 1,
                    "raw_hex": f"{opcode:02X}", "glyph_page": "small",
                    "glyph_index": opcode, "character": character,
                    "confidence": "verified" if character is not None else "unresolved",
                })
                position += 1
            elif opcode <= 0xF5:
                if position + 1 >= len(raw):
                    tokens.append({
                        "type": "truncated", "offset": position,
                        "raw_hex": f"{opcode:02X}",
                    })
                    break
                index = (opcode - 0xF0) * 0x100 + raw[position + 1]
                row = self.wide.get(index, {})
                tokens.append({
                    "type": "glyph", "offset": position, "length": 2,
                    "raw_hex": raw[position:position + 2].hex(" ").upper(),
                    "glyph_page": "wide", "glyph_index": index,
                    "character": row.get("character"),
                    "confidence": row.get("confidence", "unresolved"),
                })
                position += 2
            elif opcode == 0xFF:
                tokens.append({
                    "type": "terminator", "offset": position, "length": 1,
                    "raw_hex": "FF", "code": "0xFF", "name": "terminator",
                })
                position += 1
                if stop_at_terminator:
                    break
            else:
                argument_count = CONTROL_ARGS[opcode]
                end = position + 1 + argument_count
                if end > len(raw):
                    end = len(raw)
                tokens.append({
                    "type": "control", "offset": position,
                    "length": end - position,
                    "raw_hex": raw[position:end].hex(" ").upper(),
                    "code": f"0x{opcode:02X}", "name": CONTROL_NAMES[opcode],
                    "arguments_hex": raw[position + 1:end].hex(" ").upper(),
                })
                position = end
        return tokens

    @staticmethod
    def rendered(tokens: Iterable[dict[str, Any]]) -> str:
        output: list[str] = []
        for token in tokens:
            if token["type"] == "glyph":
                output.append(token.get("character") or f"<{token['raw_hex'].replace(' ', '')}>")
            elif token["type"] == "control":
                name = token["name"]
                if name == "line_break":
                    output.append("\n")
                elif name == "wait_or_page":
                    output.append("<WAIT>")
                else:
                    arguments = token.get("arguments_hex", "").replace(" ", "")
                    output.append(f"<{token['code'][2:]}{':' + arguments if arguments else ''}>")
        return "".join(output)

    @staticmethod
    def compact_segments(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        glyph_run: list[dict[str, Any]] = []

        def flush() -> None:
            if not glyph_run:
                return
            text = "".join(t.get("character") or f"<{t['raw_hex'].replace(' ', '')}>" for t in glyph_run)
            segments.append({
                "type": "text", "offset": glyph_run[0]["offset"],
                "length": sum(t["length"] for t in glyph_run),
                "raw_hex": " ".join(t["raw_hex"] for t in glyph_run),
                "text": text,
            })
            glyph_run.clear()

        for token in tokens:
            if token["type"] == "glyph":
                glyph_run.append(token)
            else:
                flush()
                segments.append(token)
        flush()
        return segments

    def encode_variants(self, text: str, limit: int = 200_000) -> list[bytes]:
        choices = [self.inverse.get(character, []) for character in text]
        if any(not choice for choice in choices):
            return []
        count = 1
        for choice in choices:
            count *= len(choice)
        if count > limit:
            return []
        return [b"".join(parts) for parts in itertools.product(*choices)]


def true_records(data: bytes, start: int, end: int) -> list[tuple[int, int]]:
    records: list[tuple[int, int]] = []
    record_start = position = start
    while position < end:
        opcode = data[position]
        if opcode < 0xF0:
            position += 1
        elif opcode <= 0xF5:
            position += 2
        elif opcode == 0xFF:
            position += 1
            records.append((record_start, position))
            record_start = position
        else:
            position += 1 + CONTROL_ARGS[opcode]
        if position > end:
            # A handful of members have one non-text alignment/header byte
            # after the final true FF and immediately before the first script
            # target.  It is pool tail, not a truncated record.
            break
    return records


def record_document(
    codec: Codec, raw: bytes, *, record_id: str, source_offset: int,
    extra: dict[str, Any], body_skip: int = 0,
) -> dict[str, Any]:
    tokens = codec.tokenize(raw)
    visible = [
        dict(token, offset=token["offset"] - body_skip)
        for token in tokens
        if token["offset"] >= body_skip
    ]
    japanese = codec.rendered(visible)
    speaker = japanese.split("「", 1)[0] if "「" in japanese else None
    warnings = [
        {
            "record_byte_offset": token["offset"],
            "raw_hex": token["raw_hex"],
            "glyph_index": token["glyph_index"],
            "confidence": token["confidence"],
        }
        for token in tokens
        if token["type"] == "glyph"
        and token["offset"] >= body_skip
        and token.get("confidence") in {"low", "unresolved"}
    ]
    return {
        "id": record_id,
        "source_offset": source_offset,
        "source_offset_hex": f"0x{source_offset:X}",
        "raw_hex": raw.hex(" ").upper(),
        "body_skip": body_skip,
        "japanese": japanese,
        "speaker": speaker,
        "segments": codec.compact_segments(visible),
        "mapping_warnings": warnings,
        "translation": "",
        **extra,
    }


def extract_scenarios(path: Path, codec: Codec) -> dict[str, Any]:
    source = path.read_bytes()
    table_bytes = struct.unpack_from("<I", source, 0)[0]
    if not table_bytes or table_bytes % 4:
        raise ValueError("invalid SCEDATA pointer table")
    member_offsets = list(struct.unpack_from(f"<{table_bytes // 4}I", source, 0))
    scenarios = []
    all_records = []
    content_first_seen: dict[str, int] = {}
    for scenario_index, compressed_start in enumerate(member_offsets):
        compressed_end = (
            member_offsets[scenario_index + 1]
            if scenario_index + 1 < len(member_offsets)
            else len(source)
        )
        member = source[compressed_start:compressed_end]
        decompressed, consumed = srw_lz_decompress(member)
        content_hash = sha256(decompressed)
        duplicate_of = content_first_seen.setdefault(content_hash, scenario_index)
        header_targets = list(struct.unpack_from("<14I", decompressed, 0))
        pool_start = 0x38
        signature_at = decompressed.find(SCENARIO_TEXT_TAIL_SIGNATURE, pool_start)
        if signature_at < 0:
            # Slot 96 is a tiny shared lookup member, not a scenario script.
            pool_end = pool_start
        else:
            pool_end = signature_at
            while pool_end > pool_start and decompressed[pool_end - 1] == 0:
                pool_end -= 1
        ranges = (
            true_records(decompressed, pool_start, pool_end)
            if duplicate_of == scenario_index else []
        )
        scenario_records = []
        prologue_anchor = codec.encode_variants("地球を汚染") if scenario_index == 0 else []
        for ordinal, (start, end) in enumerate(ranges):
            raw = decompressed[start:end]
            body_skip = 12 if ordinal == 0 and scenario_index != 0 and len(raw) > 12 else 0
            if scenario_index == 0 and ordinal == 0:
                body_skip = len(raw)
                kind = "non_text_data"
            elif scenario_index == 0 and ordinal == 1:
                hits = [raw.find(anchor) for anchor in prologue_anchor]
                hits = [hit for hit in hits if hit >= 0]
                if not hits:
                    raise ValueError("scenario 0 prologue text anchor was not found")
                body_skip = min(hits)
                kind = "prologue"
            elif ordinal == 0:
                kind = "objective_victory"
            elif ordinal == 2:
                kind = "objective_defeat"
            elif not codec.rendered(codec.tokenize(raw)):
                kind = "separator"
            elif 0x3E in raw:
                kind = "dialogue"
            else:
                kind = "text"
            record = record_document(
                codec, raw,
                record_id=f"SCE-{scenario_index:03d}-{ordinal:04d}",
                source_offset=start,
                body_skip=body_skip,
                extra={
                    "archive": "SCEDATA.BIN", "scenario_index": scenario_index,
                    "record_index": ordinal, "kind": kind,
                    "decompressed_offset": start,
                    "decompressed_offset_hex": f"0x{start:X}",
                },
            )
            scenario_records.append(record)
            all_records.append(record)
        scenarios.append({
            "scenario_index": scenario_index,
            "compressed_start": compressed_start,
            "compressed_start_hex": f"0x{compressed_start:X}",
            "compressed_end": compressed_end,
            "compressed_end_hex": f"0x{compressed_end:X}",
            "compressed_allocation_size": compressed_end - compressed_start,
            "compressed_stream_bytes_used": consumed,
            "compressed_padding_bytes": compressed_end - compressed_start - consumed,
            "decompressed_size": len(decompressed),
            "decompressed_sha256": content_hash,
            "duplicate_of_scenario": duplicate_of if duplicate_of != scenario_index else None,
            "header_targets": header_targets,
            "first_header_target": min(header_targets),
            "text_tail_signature_offset": signature_at if signature_at >= 0 else None,
            "text_pool_start": pool_start,
            "text_pool_end": pool_end,
            "record_count": len(scenario_records),
            "records": scenario_records,
        })
    return {
        "archive": str(path.resolve()), "sha256": sha256(source),
        "member_count": len(scenarios), "record_count": len(all_records),
        "compression": "SRW MSB-first LZ; each pointer selects one complete stream",
        "scenarios": scenarios,
    }


def extract_battle(path: Path, codec: Codec) -> dict[str, Any]:
    source = path.read_bytes()
    table_bytes = struct.unpack_from("<I", source, 0)[0]
    pointer_count = table_bytes // 4 if table_bytes and table_bytes % 4 == 0 else 0
    pointers = list(struct.unpack_from(f"<{pointer_count}I", source, 0)) if pointer_count else []
    records = []
    position = 0
    while True:
        start = source.find(b"\xFC\x08", position)
        if start < 0:
            break
        body_start = start + 4
        tokens = codec.tokenize(source[body_start:], stop_at_terminator=True)
        if not tokens or tokens[-1]["type"] != "terminator":
            position = start + 2
            continue
        length = tokens[-1]["offset"] + 1
        end = body_start + length
        bank_id = source[start + 3]
        message_id = source[start + 2]
        containing = [index for index, pointer in enumerate(pointers) if pointer <= start]
        table_entry = containing[-1] if containing else None
        record = record_document(
            codec, source[body_start:end],
            record_id=f"BTT-{bank_id:02X}-{message_id:02X}-{start:06X}",
            source_offset=start,
            extra={
                "archive": "BTT/BTTMES.BIN", "kind": "battle_dialogue",
                "record_header_hex": source[start:body_start].hex(" ").upper(),
                "message_id": message_id, "bank_id": bank_id,
                "outer_table_entry": table_entry,
                "body_offset": body_start,
                "body_offset_hex": f"0x{body_start:X}",
                "record_end": end,
                "record_end_hex": f"0x{end:X}",
            },
        )
        records.append(record)
        position = end
    return {
        "archive": str(path.resolve()), "sha256": sha256(source),
        "record_signature": "FC 08 <message-id> <bank-id>",
        "record_count": len(records), "records": records,
    }


def extract_menu(path: Path, codec: Codec) -> dict[str, Any]:
    source = path.read_bytes()
    occurrences = []
    seen: set[tuple[int, str]] = set()
    for label in MENU_ANCHORS:
        for encoded in codec.encode_variants(label):
            position = 0
            while True:
                position = source.find(encoded, position)
                if position < 0:
                    break
                identity = (position, label)
                if identity not in seen:
                    seen.add(identity)
                    occurrences.append({
                        "id": f"EXE-{position:06X}-{label}",
                        "archive": "SLPS_005.50", "kind": "menu_anchor",
                        "source_offset": position,
                        "source_offset_hex": f"0x{position:X}",
                        "raw_hex": encoded.hex(" ").upper(),
                        "japanese": label, "translation": "",
                    })
                position += 1
    occurrences.sort(key=lambda row: (row["source_offset"], row["japanese"]))
    return {
        "archive": str(path.resolve()), "sha256": sha256(source),
        "method": "exact encoded anchor search from captured early menus",
        "anchor_count": len(MENU_ANCHORS), "occurrence_count": len(occurrences),
        "records": occurrences,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, default=Path("text_extracted/ssrw_japanese_font_mapping.json"))
    parser.add_argument("--scedata", type=Path, default=Path("extracted/SCEDATA.BIN"))
    parser.add_argument("--bttmes", type=Path, default=Path("extracted/BTT/BTTMES.BIN"))
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument("--output-dir", type=Path, default=Path("text_extracted"))
    args = parser.parse_args()

    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    codec = Codec(mapping)
    scenario = extract_scenarios(args.scedata, codec)
    battle = extract_battle(args.bttmes, codec)
    menu = extract_menu(args.exe, codec)
    mapping_warning_counts = Counter(
        warning["raw_hex"]
        for section in (scenario, battle)
        for record in (
            [r for s in section["scenarios"] for r in s["records"]]
            if "scenarios" in section else section["records"]
        )
        for warning in record["mapping_warnings"]
    )
    combined = {
        "format": "Shin Super Robot Wars Japanese source text v1",
        "encoding": {
            "small_glyph": "00-EF: direct 8x16 glyph index",
            "wide_glyph": "F0-F5 plus trail byte: 16x16 glyph index",
            "controls": {f"0x{k:02X}": {"name": CONTROL_NAMES[k], "argument_bytes": v} for k, v in CONTROL_ARGS.items()},
            "terminator": "FF when reached as an opcode (never split a wide/control operand)",
            "font_mapping": str(args.mapping.resolve()),
        },
        "reinsertion_notes": [
            "Keep raw_hex and segments until translation is encoded.",
            "SCEDATA offsets are decompressed-member offsets; recompress and rebuild the member pointer table after editing.",
            "BTTMES offsets are direct archive offsets; preserve the FC 08 four-byte record header.",
            "translation is intentionally blank and ready for Korean text.",
        ],
        "mapping_warning_usage": dict(mapping_warning_counts.most_common()),
        "scenario": scenario,
        "battle": battle,
        "menu": menu,
    }
    scenario_work = [
        {
            "id": record["id"], "category": "scenario",
            "scenario_index": record["scenario_index"],
            "record_index": record["record_index"], "kind": record["kind"],
            "decompressed_offset_hex": record["decompressed_offset_hex"],
            "speaker": record["speaker"], "japanese": record["japanese"],
            "translation": "",
        }
        for scenario_row in scenario["scenarios"]
        for record in scenario_row["records"]
        if record["kind"] not in {"separator", "non_text_data"} and record["japanese"]
    ]
    battle_work = [
        {
            "id": record["id"], "category": "battle",
            "bank_id": record["bank_id"], "message_id": record["message_id"],
            "source_offset_hex": record["source_offset_hex"],
            "speaker": record["speaker"], "japanese": record["japanese"],
            "translation": "",
        }
        for record in battle["records"]
    ]
    grouped_menu: dict[str, list[str]] = defaultdict(list)
    for record in menu["records"]:
        grouped_menu[record["japanese"]].append(record["source_offset_hex"])
    menu_work = [
        {
            "id": f"MENU-{index:03d}", "category": "menu",
            "japanese": japanese, "occurrence_offsets_hex": offsets,
            "translation": "",
        }
        for index, (japanese, offsets) in enumerate(sorted(grouped_menu.items()), 1)
    ]
    worklist = {
        "format": "SSRW Japanese-to-Korean translation worklist v1",
        "master_file": str((args.output_dir / "ssrw_japanese_text.json").resolve()),
        "notes": [
            "Translate only the translation fields.",
            "Keep inline <WAIT> markers and line breaks unless layout is intentionally changed.",
            "Use the master file for raw bytes, controls, and reinsertion offsets.",
        ],
        "counts": {
            "scenario": len(scenario_work), "battle": len(battle_work),
            "menu_unique": len(menu_work),
        },
        "scenario": scenario_work, "battle": battle_work, "menu": menu_work,
    }
    write_json(args.output_dir / "ssrw_scenario_dialogue.json", scenario)
    write_json(args.output_dir / "ssrw_battle_dialogue.json", battle)
    write_json(args.output_dir / "ssrw_menu_text.json", menu)
    write_json(args.output_dir / "ssrw_japanese_text.json", combined)
    write_json(args.output_dir / "ssrw_translation_worklist.json", worklist)
    print(f"scenario: {scenario['record_count']} records / {scenario['member_count']} members")
    print(f"battle:   {battle['record_count']} records")
    print(f"menu:     {menu['occurrence_count']} anchor occurrences")
    print(f"mapping warnings used: {dict(mapping_warning_counts.most_common())}")
    print(f"combined: {(args.output_dir / 'ssrw_japanese_text.json').resolve()}")
    print(f"worklist: {(args.output_dir / 'ssrw_translation_worklist.json').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
