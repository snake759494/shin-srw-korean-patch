#!/usr/bin/env python3
"""Survey SCEDATA event-script references into the dialogue text pool.

This is a static audit.  It does not start an emulator.  The scenario VM keeps
its event program after the shared lookup-table signature and uses several
binary command forms to reach records that may move when Korean text grows.
The audit deliberately starts with every signed-16-bit position, then groups
the references by the bytes immediately before the operand.  That makes a
missed command family visible instead of relying on a guessed opcode list.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import struct
import sys
import argparse


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"

sys.path.insert(0, str(ROOT / "tools"))
from extract_ssrw_japanese_text import (  # noqa: E402
    SCENARIO_TEXT_TAIL_SIGNATURE,
    srw_lz_decompress,
)


JUMP_OPCODES = (0x07, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x14, 0x1E, 0x1F)
FC_LENGTH = {
    0x00: 4, 0x01: 2, 0x02: 2, 0x03: 2, 0x04: 2, 0x07: 4, 0x08: 4,
    0x09: 2, 0x0A: 4, 0x0E: 5, 0x0F: 6, 0x10: 4, 0x11: 4, 0x12: 4,
    0x14: 6, 0x1E: 4, 0x1F: 4,
}


def btt_jump_operands(block: bytes) -> list[int]:
    """Walk one BTTMES bank's FC09/FA control graph.

    Sites are bank-relative offsets of signed s16 displacements.  This mirrors
    the builder's relocation walk, but is kept here as an independent audit so
    the release check can compare the old and new execution graphs.
    """
    if len(block) < 26:
        return []
    entries = struct.unpack_from("<13H", block, 0)
    pending = [
        value + 2 * index
        for index, value in enumerate(entries)
        if index and 0 <= value + 2 * index < len(block)
    ]
    visited: set[int] = set()
    sites: list[int] = []
    while pending:
        pc = pending.pop()
        while 0 <= pc < len(block) - 1 and pc not in visited:
            visited.add(pc)
            opcode = block[pc]
            if opcode == 0xFF:
                break
            if opcode == 0xFA:
                count = block[pc + 1]
                if not 1 <= count <= 16 or pc + 2 + 2 * count > len(block):
                    break
                for step in range(count):
                    site = pc + 2 + 2 * step
                    sites.append(site)
                    pending.append(site + struct.unpack_from("<h", block, site)[0])
                pc += 2 + 2 * count
                continue
            if opcode == 0xFC:
                sub = block[pc + 1]
                if sub in JUMP_OPCODES:
                    site = pc + 2
                    if site + 2 > len(block):
                        break
                    sites.append(site)
                    target = site + struct.unpack_from("<h", block, site)[0]
                    if sub in (0x07, 0x1E):
                        pc = target
                        continue
                    pending.append(target)
                    pc += FC_LENGTH[sub]
                    continue
                if sub in FC_LENGTH:
                    pc += FC_LENGTH[sub]
                    continue
                break
            if opcode in (0xF9, 0xFE):
                pc += 3
                continue
            if opcode in (0xF6, 0xF7):
                pc += 1
                continue
            if 0xF0 <= opcode <= 0xF5:
                pc += 2
                continue
            if opcode in (0xEC, 0xED, 0xEE, 0xEF, 0xF8, 0xFB, 0xFD):
                break
            pc += 1
    return sites


def audit_bttmes(source: bytes, rebuilt: bytes, applied_path: Path) -> dict[str, object]:
    """Compare every BTTMES index and script edge before/after expansion."""
    table_bytes = struct.unpack_from("<I", source, 0)[0]
    pointer_count = table_bytes // 4
    old_pointers = list(struct.unpack_from(f"<{pointer_count}I", source, 0))
    new_pointers = list(struct.unpack_from(f"<{pointer_count}I", rebuilt, 0))
    old_banks = sorted({value for value in old_pointers if table_bytes <= value < len(source)})
    new_banks = sorted({value for value in new_pointers if table_bytes <= value < len(rebuilt)})
    if len(old_banks) != len(new_banks):
        raise AssertionError(f"BTTMES bank count changed: {len(old_banks)} -> {len(new_banks)}")

    applied = json.loads(applied_path.read_text(encoding="utf-8"))
    changes = [row for row in applied.get("battle", [])]
    by_old = {
        int(row["old_source_offset"]): row
        for row in changes
    }

    def relocate(old_offset: int) -> int:
        delta = 0
        for row in changes:
            old_start = int(row["old_source_offset"])
            old_end = old_start + int(row["old_record_bytes"])
            if old_offset >= old_end:
                delta += int(row["growth_bytes"])
            elif old_offset >= old_start:
                return int(row["new_source_offset"]) + (old_offset - old_start)
            else:
                break
        return old_offset + delta

    def bounds(image: bytes, banks: list[int], pos: int) -> tuple[int, int]:
        start = banks[pos]
        end = banks[pos + 1] if pos + 1 < len(banks) else len(image)
        return start, end

    index_mismatches: list[dict[str, object]] = []
    for pos, old_bank in enumerate(old_banks):
        new_bank = new_banks[pos]
        old_start, old_end = bounds(source, old_banks, pos)
        new_start, new_end = bounds(rebuilt, new_banks, pos)
        old_entries = struct.unpack_from("<13H", source, old_bank)
        degenerate = any(
            value + 2 * index >= old_end - old_start
            for index, value in enumerate(old_entries)
        )
        new_entries = struct.unpack_from("<13H", rebuilt, new_bank)
        for index, old_value in enumerate(old_entries):
            old_target = old_bank + old_value + 2 * index
            # The one malformed retail bank has three genuine pointers beyond
            # its own span and ten dummy/in-bank values.  Only the former move
            # with the archive; relocating the dummy values changes the
            # bank's sentinel/index padding and can alter later dispatch.
            if degenerate and old_start <= old_target < old_end:
                # Preserve the relative index value while the bank itself
                # moves; the resulting absolute target therefore moves by
                # the bank delta too.
                expected = new_bank + old_value + 2 * index
            else:
                expected = relocate(old_target)
            new_target = new_bank + new_entries[index] + 2 * index
            if new_target != expected:
                index_mismatches.append({
                    "bank": f"0x{old_bank:X}", "entry": index,
                    "old_target": f"0x{old_target:X}",
                    "expected_target": f"0x{expected:X}",
                    "new_target": f"0x{new_target:X}",
                })
        if new_end < new_start + 26 or old_end < old_start + 26:
            raise AssertionError(f"BTTMES bank {old_bank:#x} has no 13-entry index")

    graph_mismatches: list[dict[str, object]] = []
    inspected = 0
    old_header_count = 0
    retargeted_header_count = 0
    for pos, old_bank in enumerate(old_banks):
        new_bank = new_banks[pos]
        old_start, old_end = bounds(source, old_banks, pos)
        new_start, new_end = bounds(rebuilt, new_banks, pos)
        old_block = source[old_start:old_end]
        new_block = rebuilt[new_start:new_end]
        old_headers = [
            offset - old_start
            for offset in range(old_start, old_end - 1)
            if source[offset] == 0xFC and source[offset + 1] == 0x08
        ]
        # The script area keeps its retail size, so no message starts before
        # retail's first one; an FC 08 earlier than that is a jump-table entry
        # whose value happens to be 0x08FC (bank 0x3F000 since v1.0.15).
        floor = old_headers[0] if old_headers else 0
        new_headers = [
            offset - new_start
            for offset in range(new_start + floor, new_end - 1)
            if rebuilt[offset] == 0xFC and rebuilt[offset + 1] == 0x08
        ]
        if len(old_headers) != len(new_headers):
            graph_mismatches.append({
                "bank": f"0x{old_bank:X}",
                "reason": "message_header_count",
                "old": len(old_headers), "new": len(new_headers),
            })
        header_map = dict(zip(old_headers, new_headers))
        old_sites = btt_jump_operands(old_block)
        for site in old_sites:
            inspected += 1
            if site + 2 > len(new_block):
                graph_mismatches.append({
                    "bank": f"0x{old_bank:X}", "site": f"0x{site:X}",
                    "reason": "site_out_of_new_bank",
                })
                continue
            old_target = site + struct.unpack_from("<h", old_block, site)[0]
            new_target = site + struct.unpack_from("<h", new_block, site)[0]
            if old_target in header_map:
                old_header_count += 1
                expected = header_map[old_target]
                if new_target != expected:
                    graph_mismatches.append({
                        "bank": f"0x{old_bank:X}", "site": f"0x{site:X}",
                        "reason": "message_edge",
                        "old_target": f"0x{old_target:X}",
                        "expected_target": f"0x{expected:X}",
                        "new_target": f"0x{new_target:X}",
                    })
                elif new_target != old_target:
                    retargeted_header_count += 1
            elif new_target != old_target:
                # Non-message script targets are before the movable text pool;
                # changing one is an execution-graph mutation, not translation.
                graph_mismatches.append({
                    "bank": f"0x{old_bank:X}", "site": f"0x{site:X}",
                    "reason": "non_message_edge_changed",
                    "old_target": f"0x{old_target:X}",
                    "new_target": f"0x{new_target:X}",
                })

    return {
        "pointer_count": pointer_count,
        "old_bank_count": len(old_banks), "new_bank_count": len(new_banks),
        "index_mismatch_count": len(index_mismatches),
        "index_mismatches": index_mismatches[:20],
        "script_jump_site_count": inspected,
        "message_edge_count": old_header_count,
        "retargeted_message_edge_count": retargeted_header_count,
        "graph_mismatch_count": len(graph_mismatches),
        "graph_mismatches": graph_mismatches[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--opcode",
        type=lambda value: int(value, 0),
        help="only print hits whose preceding byte has this value",
    )
    parser.add_argument(
        "--bttmes",
        action="store_true",
        help="also audit the current BTTMES rebuild against its applied map",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=WORK / "issue74_current_build",
        help="build directory containing extracted/BTT/BTTMES.BIN and translation_applied.json",
    )
    args = parser.parse_args()
    source = (WORK / "extracted" / "SCEDATA.BIN").read_bytes()
    document = json.loads(
        (WORK / "text_extracted" / "ssrw_scenario_dialogue.json").read_text(
            encoding="utf-8"
        )
    )
    table_bytes = struct.unpack_from("<I", source, 0)[0]
    offsets = struct.unpack_from(f"<{table_bytes // 4}I", source, 0)
    totals: Counter[tuple[int, str]] = Counter()
    examples: defaultdict[tuple[int, str], list[dict[str, object]]] = defaultdict(list)
    all_hits = 0
    for scenario in document["scenarios"]:
        index = int(scenario["scenario_index"])
        if scenario.get("duplicate_of_scenario") is not None:
            continue
        member = srw_lz_decompress(
            source[
                offsets[index] : offsets[index + 1]
                if index + 1 < len(offsets)
                else len(source)
            ]
        )[0]
        signature = int(scenario.get("text_tail_signature_offset") or -1)
        if signature < 0:
            continue
        records = scenario["records"]
        ranges = [
            (
                int(row["source_offset"]),
                int(row["source_offset"]) + len(bytes.fromhex(row["raw_hex"])),
                str(row["id"]),
            )
            for row in records
        ]
        starts = {start: record_id for start, _end, record_id in ranges}
        for site in range(signature + 8, len(member) - 1):
            target = site + struct.unpack_from("<h", member, site)[0]
            target_row = next(
                (
                    (record_id, target - start)
                    for start, end, record_id in ranges
                    if start <= target < end
                ),
                None,
            )
            if target_row is None:
                continue
            all_hits += 1
            opcode_offset = site - 3
            opcode = member[opcode_offset] if opcode_offset >= signature + 8 else None
            if args.opcode is not None and opcode != args.opcode:
                continue
            actor = (
                struct.unpack_from("<H", member, opcode_offset + 1)[0]
                if opcode is not None and opcode_offset + 3 <= len(member)
                else None
            )
            key = (int(opcode) if opcode is not None else -1, "start" if target_row[1] == 0 else "inside")
            totals[key] += 1
            if len(examples[key]) < 8:
                examples[key].append(
                    {
                        "scenario": index,
                        "site": f"0x{site:X}",
                        "opcode_offset": f"0x{opcode_offset:X}",
                        "opcode": f"0x{opcode:02X}" if opcode is not None else None,
                        "actor": f"0x{actor:04X}" if actor is not None else None,
                        "target": f"0x{target:X}",
                        "record": target_row[0],
                        "relative": target_row[1],
                        "context": member[max(signature + 8, site - 5) : site + 2].hex(" ").upper(),
                    }
                )

    print(f"all signed-s16 hits into a record: {all_hits}")
    for key in sorted(totals):
        print(f"opcode={key[0]:#04x} landing={key[1]} count={totals[key]}")
        for example in examples[key]:
            print("  " + json.dumps(example, ensure_ascii=True, sort_keys=True))
    print("exact record-start hits by preceding opcode:")
    print(
        json.dumps(
            {
                f"0x{opcode:02X}": count
                for (opcode, landing), count in sorted(totals.items())
                if landing == "start"
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    if args.bttmes:
        source_btt = WORK / "extracted" / "BTT" / "BTTMES.BIN"
        build_dir = args.build_dir
        if not build_dir.is_absolute():
            build_dir = ROOT / build_dir
        rebuilt_btt = build_dir / "extracted" / "BTT" / "BTTMES.BIN"
        applied = build_dir / "translation_applied.json"
        report = audit_bttmes(source_btt.read_bytes(), rebuilt_btt.read_bytes(), applied)
        print("BTTMES semantic audit:")
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        if report["index_mismatch_count"] or report["graph_mismatch_count"]:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
