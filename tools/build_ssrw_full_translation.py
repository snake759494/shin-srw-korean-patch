#!/usr/bin/env python3
"""Build and verify the complete Korean SSRW translation image."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
from typing import Any

from build_ssrw_hangul_font_probe import (
    RAW_SECTOR_SIZE,
    USER_DATA_OFFSET,
    USER_DATA_SIZE,
    rebuild_mode2_form1,
    verify_mode2_form1,
)
from build_ssrw_hangul_font_probe_fixed import (
    FONT_BYTES_PER_GLYPH,
    FONT_OFFSET,
    row_major_to_split_halves,
    shift_row_major,
)
from build_ssrw_screenshot_korean_test import (
    MENU_TRANSLATIONS as COMPACT_MENU_TRANSLATIONS,
    count_wide_usage,
    encode_text,
    load_or_extend_mapping,
    ordered_hangul,
    parse_bdf,
    render_glyph,
    sha256,
)
from extract_ssrw_japanese_text import (
    CONTROL_ARGS,
    Codec,
    SCENARIO_TEXT_TAIL_SIGNATURE,
    srw_lz_decompress,
    true_records,
)
from build_ssrw_expansion_test import load_encoder

import build_ssrw_title_graphics as title_graphics


_REPO = Path(__file__).resolve().parent.parent  # repo root: the build runs with CWD=work/

SCENARIO_INDEX_COUNT = 128
PVD_LBA = 16
ROOT_DIRECTORY_LBA = 22
EXE_LBA = 24
EXE_TEXT_BASE = 0x80030000
AUDIO_PREGAP_SECTORS = 150

# 0xED is an unused entry in the executable's 8x16 half-width table.  The
# confirmation labels "아뇨" need three message bytes when encoded as one
# half-width glyph plus one wide glyph, which fits the retail three-byte
# label field (four bytes including the SYSMSG terminator) without moving the
# following SYSMSG record.
SMALL_FONT_OFFSET = 0x72438
SMALL_GLYPH_BYTES = 16
SMALL_LABEL_AH_INDEX = 0xED

# FC09 (the battle-dialogue speaker-name command) returns PILOTNAME, and the
# original Japanese short names are rendered with the executable's 8x16
# half-width cells.  Encoding every Korean syllable as an F0-F5 glyph makes a
# three-syllable name 50% wider than its Japanese counterpart.  In the first
# battle that places "아무로" over the portrait and, for longer names, lets the
# name line reach the window limit before FC09's saved continuation is read.
#
# These compact cells are unused by every non-name text payload in the current
# translation.  Keep the allocation explicit and stable: it is a separate
# code page used only by PILOTNAME, while the same syllables remain available
# in the normal wide table for dialogue and menus.  0xED is already the
# compact cell used by the fixed "아뇨" label and is therefore shared by names.
SMALL_BATTLE_NAME_GLYPHS = {
    "무": 0x42,
    "로": 0x49,
    "이": 0x4B,
    "스": 0x51,
    "리": 0x53,
    "사": 0x67,
    "라": 0x72,
    "카": 0x78,
    "지": 0x7B,
    "병": 0x85,
    "마": 0x94,
    "시": 0x95,
    "미": 0x9D,
    "나": 0xA0,
    "하": 0xA2,
    "장": 0xC4,
    "레": 0xD5,
    "트": 0xDB,
    "오": 0xE0,
    "드": 0xEC,
    "아": SMALL_LABEL_AH_INDEX,
}

# The 0x6C..0x80 range is the compact speaker-name line in the retail battle
# layout (the body line is reset separately by F6).  Keep this as a static
# audit bound; it is not used to truncate or rewrite a name.
BATTLE_NAME_LINE_START = 0x6C
BATTLE_NAME_LINE_END = 0x80

# BTTMES.BIN is appended at the end of the data track, so its last record ends
# on the last data sector.  The battle-message loader streams with CdlReadN and
# the drive prefetches lba+1; with nothing after BTTMES that prefetch lands in
# the CD-DA track, which cannot be delivered in 2048-byte data mode.  The
# request stepper at 0x80032E84 then sees CdReadSync == -1 and retries forever.
# Keep a run of real data sectors behind the archive so read-ahead stays on
# track 1.
BTTMES_READAHEAD_GUARD_SECTORS = 16
# The executable does not read the archive index tables out of the data files.
# It carries verbatim copies of them inside itself, reached through the archive
# directory struct at RAM 0x80072540 (file 0x42D40): slot +0x110 selects the
# SCEDATA member table and slot +0x128 the BTTMES record table.  Repacking a
# data file without rewriting its mirror makes every member load from a retail
# offset, so the scenario VM decompresses garbage, reads a wild handler id and
# jumps into an overlay that was never loaded.  That is the new-game freeze.
EXE_SCEDATA_TABLE_OFFSET = 0x46014
EXE_SCEDATA_TABLE_BYTES = 0x200
EXE_BTTMES_TABLE_OFFSET = 0x47114
EXE_BTTMES_TABLE_BYTES = 0x400

# The scenario VM decompresses one SCEDATA member into a fixed buffer at RAM
# 0x801E0000.  The next live allocation begins at 0x801E4000 and the game's
# decompressor has no bounds check, so a longer member silently corrupts it.
SCEDATA_MEMBER_BUFFER_BYTES = 0x4000
# Hard limit above is a hardware fact; build to a slightly lower target so a
# future wording change cannot silently land on the boundary.
SCEDATA_MEMBER_SAFETY_MARGIN = 192

# Post-pool event commands that carry a signed s16 displacement to a dialogue
# record.  See relocate_post_pool_event_references for the measurement.
#
# 0x86 and 0x87 belong here too.  They were originally dismissed because the
# survey that found the family filtered on the 0x0800..0x09FF actor id the
# 0x80..0x85 commands carry, and these two use a completely different id range,
# so every one of them was counted as "never resolves into the text pool".  They
# do: 202 of 203 in-pool 0x86 operands and all 34 in-pool 0x87 operands land
# exactly on a record start.  Leaving them stale is what made a line open in the
# middle of itself - the speaker name gone, the portrait still showing whoever
# spoke last - once Korean records grew past their retail lengths.
DIALOGUE_DISPATCH_OPCODES = (0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87)

# Verified actor/effect id windows per command family, used to tell a real
# dispatch from an opcode-shaped byte inside an opaque script payload.
DISPATCH_ACTOR_RANGES = {
    0x80: (0x0800, 0x09FF), 0x81: (0x0800, 0x09FF), 0x82: (0x0800, 0x09FF),
    0x83: (0x0800, 0x09FF), 0x84: (0x0800, 0x09FF), 0x85: (0x0800, 0x09FF),
    0x86: (0x0001, 0x01FF), 0x87: (0x0001, 0x01FF),
}


# Player-visible strings in the executable are reached through self-relative u32
# tables: the target of entry i is (table + 4 * i) + value.  Each table is
# announced by a stub word four bytes ahead holding the RAM address of entry 0,
# which is how they were all found (40 stubs, 21 of them string tables).  No
# copy of these tables exists anywhere else on the disc, and the executable is
# written back at an unchanged size, so repacking them is confined to the EXE.
#
# arena = (first byte the pool may use, first byte it may NOT use).  The upper
# bound is the next stub-announced structure.  Retail fills every arena with no
# gaps, so the only slack comes from sharing identical strings between entries,
# which retail never does: 19.3% of the retail bytes are duplicate text.
EXE_STRING_POOLS = {
    "PILOTNAME": {"table": 0x6A938, "entries": 512, "arena": (0x6B138, 0x6B81C)},
    "PILOTFULL": {"table": 0x6B820, "entries": 512, "arena": (0x6C020, 0x6CA44)},
    "UNITNAME": {"table": 0x6CA48, "entries": 263, "arena": (0x6CE64, 0x6D6C0)},
    "WEAPON": {"table": 0x6D6C4, "entries": 1024, "arena": (0x6E6C4, 0x70294)},
    "SERIES": {"table": 0x86FCC, "entries": 13, "arena": (0x87000, 0x8705C)},
    "BGM": {"table": 0x87060, "entries": 49, "arena": (0x87124, 0x8735C)},
}

# The SYS pool is shared by fifteen tables that all point into one contiguous
# span, so it is packed as a single arena.
# All 22 stub-announced tables that feed the SYS arena.  The first pass only
# listed 15 of them; the seven marked below were missed, and because the packer
# still refills the arena their strings were overwritten while their pointers
# kept pointing at the old bytes.  That is what turned the counter-attack menu
# (反撃開始 / 武器 / 回避 / 防御 / 反撃不能) and the sound options
# (モノラル / ステレオ / ON / OFF) into garbled glyphs on screen.
EXE_SYS_POOL_TABLES = (
    (0x702BC, 16),
    (0x7030C, 5), (0x70324, 5), (0x7033C, 5), (0x70354, 2), (0x70360, 3),  # missed
    (0x70370, 16), (0x703B4, 64), (0x704E0, 64), (0x705E4, 64),
    (0x706E8, 104), (0x7088C, 35), (0x7091C, 27), (0x7098C, 9), (0x709B4, 19),
    (0x70A04, 160), (0x70C88, 83), (0x70DD8, 23), (0x70E38, 11), (0x70E68, 12),
    (0x70E9C, 2), (0x70EA8, 2),                                            # missed
)
EXE_SYS_POOL_ARENA = (0x70EB0, 0x72434)

# The demo-select table has no announcing stub word, so it needs the stub check
# waived.  Its entry 0 is a window script full of control codes, which the
# decoder renders with <...> markers so no translation ever matches it and it is
# copied through untouched.
EXE_DEMO_POOL = {"table": 0x86E80, "entries": 9, "arena": (0x86F40, 0x86FC8)}

# Pools whose text is also read sequentially - a window continues past the
# terminator into the next string - so retail's exact layout must survive.  The
# label pools are table-only and may be packed freely.
#
# SYS is one of them.  Sharing and reordering inside its arena is what left the
# battle map's terrain panel completely blank: the panel reads on from one entry
# to the next, and only two of its tables had been marked adjacent.  Packing the
# whole arena in retail address order reproduces every adjacency at once and
# still fits - 5,361 bytes of Korean in a 5,508-byte arena.
ORDER_PRESERVING_POOLS = frozenset({"DEMO", "SYS"})

# Pools that additionally carry records no table entry addresses, reached only by
# a window reading on past the previous terminator.  DEMO's arena is one window
# script and has never needed them; SYS holds nine, and dropping them is what
# left the battle map's terrain panel and the spirit-command descriptions blank.
CONTINUATION_POOLS = frozenset({"SYS"})

# The battle map's terrain panel pushes its two "<defence> 30%<up>" lines into
# the window engine's 512 byte argument ring (0x80115EF0) as a length byte
# followed by the bytes themselves, and the panel's window script consumes them
# with `f8 00`, whose handler copies exactly as many bytes as that length says.
# The length is not measured at run time - it is the immediate of an
# `ori a0, zero, 6` in each of the two branches, and retail gets away with it
# because both branches really are six bytes:
#
#   no change   変化なし                          = 6
#   up / down   two digits + '%' + アップ/ダウン   = 2 + 1 + 3 = 6
#
# Korean needs 8 and 7.  The first `f8 00` then copies only six, and the second
# one reads a leftover glyph byte as its own length - 0xF4 for 변화없음, so 244
# bytes of the ring get executed as script.  That is what blanked every glyph in
# the panel, the two literal labels included, while the frame and the terrain
# icon (drawn before the first splice) survived.  Restoring retail's SYS bytes
# put the lengths back at six, which is why that looked like an arena bug.
#
# No wording can fix this: branch A is len(변화없음), always even, and branch B
# is 3 + len(상승), always odd, so one constant cannot serve both.  The two
# immediates are rewritten instead - two bytes, no text touched.
TERRAIN_MOD_TABLE = 0x70360          # SYS table holding 변화없음 / 상승 / 저하
TERRAIN_PANEL_LENGTH_SITES = {
    # file offset of the `ori a0, zero, imm`: (table entries it may emit, extra
    # ring bytes pushed alongside them)
    0x0B6304: ((0,), 0),      # RAM 0x800E5B04, the no-change branch
    0x0B631C: ((1, 2), 3),    # RAM 0x800E5B1C, plus two digits and '%'
}

# The description box on the spirit-command screen and on the counter-attack
# option screen prints its second line by continuing past the first record's
# terminator, then blanks that line only when the pointer it reached equals the
# next table entry.  Just these two tables are read that way - verified against
# every instruction in the executable that walks past a terminator - so only
# their records need retail's physical order.  Everything else is reached by
# index alone and may still be shared and moved freely.
# Every SYS table, not just the two that were measured first.  Windows in this
# game print a second line by reading on past a record's terminator and stop only
# when the pointer they reach equals the next table entry, so those records have
# to stay physically adjacent in retail's order.  Measuring the retail arena
# shows eleven more tables laid out end to end exactly like the two already
# listed - 0x70360 among them, which holds the 상승/저하 the terrain window
# appends to 防御/命中 - and scattering them left the battle map's terrain panel
# completely blank.  Declaring all of them costs nothing but packing freedom.
ADJACENT_TABLES = frozenset({
    0x70324, 0x704E0,                                    # measured first
    0x7030C, 0x7033C, 0x70354, 0x70360, 0x7098C,         # every entry of these
    0x709B4, 0x70DD8, 0x70E38, 0x70E68, 0x70E9C, 0x70EA8,  # is end to end in retail
    0x70A04,   # terrain names: the map's terrain panel reads on from one entry
})

# Wide glyph slots whose low byte collides with a value the renderer reads on
# its own.  A wide glyph is 0xF0..0xF5 followed by any second byte, but the
# passes that walk a message without tracking that pairing see the second byte
# by itself:
#
#   0xF0..0xF5  the terminator test looks at the byte in front of a 0xFF and
#               reads 0xF0..0xF5 as "second half of a wide glyph, keep going",
#               so a record ending here runs on into the record behind it
#   0xF6..0xFE  renderer controls - newline, <WAIT>, and the argument-taking
#               forms, which also swallow the two bytes that follow
#   0xFF        the terminator itself
#
# Scenario 3 showed both halves of this.  In SCE-003-0046 the syllable 함 sat on
# 0xF7, so the message paged in the middle of the glyph and the next page opened
# on a stray ろ; in SCE-003-0051 카 sat on 0xFB, so every "사야카" line fired a
# bogus two-argument control and left the previous speaker's portrait on screen
# while the text itself read correctly.  Reserving all sixteen values costs 96 of
# the 1536 wide slots and the script needs 1156, so nothing has to be reworded.
CONTROL_AMBIGUOUS_SLOTS = frozenset(
    index for index in range(0x600) if (index & 0xFF) >= 0xF0
)

# F4 70 (the original glyph is 肉) is emitted by the weapon-status renderer
# as an inline marker after certain weapon names.  It is not present in the
# text-pool records, so usage counting cannot discover it.  Repainting the
# slot with the first Hangul syllable allocated there makes the marker appear
# as an extra "아" in the V2 Gundam and Borot weapon lists.  Keep this slot
# byte-for-byte Japanese just as the control-ambiguous slots above are kept.
INLINE_UI_GLYPH_SLOTS = frozenset({0x470})

NEWLINE = chr(10)

BASE_MAPPING = Path("korean_patch/hangul_mapping.json")
OPENING_TRANSLATION = Path("screenshot_translation_ko.json")


# These records carry a non-dialogue prefix before their visible text.  The
# opening-event/objective reader expects the original record allocation, so
# the translated text is padded inside that allocation instead of moving the
# following event data.  The fallback strings are only used when the reviewed
# Korean wording does not fit the original fixed text area.
FIXED_SCENARIO_FALLBACKS = {
    "敵の全滅": "적 전멸",
    "味方の全滅": "아군 전멸",
    "カミオンの脱出": "탈출",
    "2機以上のシャトルの脱出": "셔틀 2기 탈출",
    "秘密基地へ侵入": "비밀기지 침입",
    "現在の目的...爆薬のセット": "폭약 설치",
    "1人以上のエリア通過": "1명 통과",
}


# The first 89 visible opening records use the already tested translations in
# screenshot_translation_ko.json.  These remaining opening records are kept
# deliberately concise so every string fits its original slot.  Scenario 1's
# event program contains more address forms than the generic extractor can
# safely relocate, so none of its record boundaries may move.
SCENARIO_ONE_FIXED_OVERRIDES = {
    "SCE-001-0000": "적 전멸",
    "SCE-001-0002": "아군 전멸",
    "SCE-001-0093": "잇페이「또 나왔군!」",
    "SCE-001-0094": "켄이치「덤벼라!\n원수는 모두 없앤다!」",
    "SCE-001-0095": "하마구치 박사「기다려, 켄이치!\n저건 아군 대공마룡이다.\n함께 싸워라」",
    "SCE-001-0096": "켄이치「아군?좋아」",
    "SCE-001-0097": "하이넬「지구에 저런 로봇이?\n장갈! 보고엔 없었다」",
    "SCE-001-0099": "하이넬「하하하! 좋다!\n제법 재미있어졌군.\n장갈, 다음 수도 준비했겠지?」",
    "SCE-001-0100": "장갈「맡겨 주십시오!」",
    "SCE-001-0101": "히요시「엄마, 들려?\n우리 이겼어」",
    "SCE-001-0102": "다이지로「엄마가 아니었으면\n우린 모두 죽었을 거요.\n흑...」",
    "SCE-001-0103": "메구미「편히 쉬세요」",
    "SCE-001-0104": "잇페이「애도 아니면서\n엄마만 찾다니 꼴사납군」",
    "SCE-001-0106": "켄이치「그만!」",
    "SCE-001-0107": "잇페이「해보자고.\n날 두들겨 패고 싶잖아」",
    "SCE-001-0108": "켄이치「그래.\n다이지로, 히요시, 손대지 마」",
    "SCE-001-0109": "히요시「옛!」",
    "SCE-001-0110": "메구미「둘 다 그만!\n지금 싸울 때가 아니잖아」",
    "SCE-001-0111": "하마구치「메구미 말이 맞다.\n미츠요 박사의 뜻을 생각해라」",
    "SCE-001-0112": "히요시「윽」",
    "SCE-001-0113": "하마구치「알겠나?\n기지에서 대공마룡대가 기다린다.\n함께 가자」",
    "SCE-001-0114": "장갈「바이잔가를 쓰러뜨리다니...\n철수!」",
    "SCE-001-0115": "장갈「좋아, 철수!」",
    "SCE-001-0116": "리리나「쟤!?」",
    "SCE-001-0117": "수녀「조용히.\n새 친구를 소개할게요」",
    "SCE-001-0118": "히이로「히이로 유이」",
    "SCE-001-0119": "리리나(분명 그애야)",
    "SCE-001-0120": "수녀「히이로는 리리나 옆에 앉아요.<WAIT>\n모르는 건 리리나에게 물어요.\n수업을 시작할게요」",
    "SCE-001-0121": "리리나「잘 부탁」",
    "SCE-001-0122": "히이로「...」",
}


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) // boundary * boundary


def message_bytes(index: int) -> bytes:
    return bytes((0xF0 + (index >> 8), index & 0xFF))


def load_speaker_fixes(path: Path) -> dict[str, str]:
    """Scenario-1 lines whose speaker name had been truncated to fit its slot.

    The opening scenario keeps every record at its retail byte size, and the
    first pass shortened the speaker tags (히이로 -> 히, 켄이치 -> 켄, 리리나 ->
    리리 ...) to make the Korean fit.  These replacements restore the full name
    and pay for it out of the line body instead.
    """
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return dict(document.get("entries", {}))


def load_translation(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    for section in ("scenario", "battle", "menu"):
        if not isinstance(document.get(section), dict):
            raise ValueError(f"translation section is missing: {section}")
    return document


def build_full_mapping(
    mapping_path: Path,
    required_texts: list[str],
    usage: Counter[int],
    reserved: frozenset[int] = frozenset(),
) -> tuple[dict[str, int], dict[str, Any]]:
    """Assign a wide glyph slot to every Hangul syllable the build needs.

    `reserved` holds slots still emitted by text this build leaves in Japanese.
    Handing one of those to Hangul repaints the glyph, so the untranslated text
    renders as a random syllable.  That is what turned the controller buttons
    (△ = F5 A7, × = F5 01, □ = F5 D1, ○ = F4 00) into 문/츠/릭/복 on the
    button-config screen: those four are wide glyphs that are never translated.
    """
    if not BASE_MAPPING.exists():
        raise FileNotFoundError(BASE_MAPPING)
    base_entries = json.loads(BASE_MAPPING.read_text(encoding="utf-8"))["entries"]
    mapping = {row["character"]: int(row["glyph_index"]) for row in base_entries}
    occupied = set(mapping.values())

    def free_slot() -> int:
        candidates = [
            index for index in range(0x600)
            if index not in occupied
            and index not in reserved
            and index not in CONTROL_AMBIGUOUS_SLOTS
        ]
        candidates.sort(key=lambda index: (usage[index], index))
        if not candidates:
            raise ValueError(
                f"wide font table has no free glyph slot "
                f"({len(occupied)} used, {len(reserved)} reserved of 1536)"
            )
        return candidates[0]

    # The reviewed base mapping predates the reserve, so move any of its entries
    # that landed on a slot the untranslated text still needs.
    evicted = 0
    for character, index in list(mapping.items()):
        if index in reserved or index in CONTROL_AMBIGUOUS_SLOTS:
            occupied.discard(index)
            replacement = free_slot()
            mapping[character] = replacement
            occupied.add(replacement)
            evicted += 1

    required = ordered_hangul(required_texts)
    for character in required:
        if character in mapping:
            continue
        slot = free_slot()
        mapping[character] = slot
        occupied.add(slot)

    rows = []
    ordered_characters = list(dict.fromkeys(
        [row["character"] for row in base_entries] + required
    ))
    for sequence, character in enumerate(ordered_characters):
        index = mapping[character]
        rows.append({
            "sequence": sequence,
            "character": character,
            "glyph_index": index,
            "message_bytes": message_bytes(index).hex(" ").upper(),
            "original_text_usage_count": usage[index],
            "font_offset": FONT_OFFSET + index * FONT_BYTES_PER_GLYPH,
            "font_offset_hex": f"0x{FONT_OFFSET + index * FONT_BYTES_PER_GLYPH:X}",
        })
    document = {
        "format": "SSRW full Korean wide glyph mapping",
        "policy": "retain the reviewed opening mapping, then allocate remaining Hangul by first appearance and lowest original usage",
        "entry_count": len(rows),
        "entries": rows,
    }
    mapping_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reassigned = [row for row in rows if row["original_text_usage_count"]]
    return mapping, {
        "entry_count": len(rows),
        "reserved_slot_count": len(reserved),
        "base_entries_evicted_from_reserved_slots": evicted,
        "required_hangul_count": len(required),
        "reassigned_original_slot_count": len(reassigned),
        "reassigned_original_usage_total": sum(row["original_text_usage_count"] for row in reassigned),
        "all_required_glyphs_have_bdf_slots": True,
    }


def patch_full_font(source: bytes, bdf_path: Path, mapping: dict[str, int]) -> bytes:
    bdf = parse_bdf(bdf_path)
    output = bytearray(source)
    for character, index in mapping.items():
        glyph = bdf.get(ord(character))
        if glyph is None:
            raise ValueError(f"BDF is missing {character!r}")
        row_major = render_glyph(glyph, 11, 16, 16)
        stored = row_major_to_split_halves(shift_row_major(row_major, -1))
        offset = FONT_OFFSET + index * FONT_BYTES_PER_GLYPH
        output[offset:offset + FONT_BYTES_PER_GLYPH] = stored
    return bytes(output)


def patch_small_label_font(source: bytes, bdf_path: Path) -> tuple[bytes, dict[str, Any]]:
    """Install the compact half-width glyphs used by labels and pilot names."""
    bdf = parse_bdf(bdf_path)
    output = bytearray(source)
    glyphs = dict(SMALL_BATTLE_NAME_GLYPHS)
    if len(set(glyphs.values())) != len(glyphs):
        raise ValueError("compact label/name glyph slots overlap")
    if any(not 0 <= index < 0xF0 for index in glyphs.values()):
        raise ValueError("compact label/name glyph slot is not a half-width byte")

    applied = []
    for character, index in sorted(glyphs.items(), key=lambda item: item[1]):
        glyph = bdf.get(ord(character))
        if glyph is None:
            raise ValueError(f"BDF is missing {character!r}")
        rendered = render_glyph(glyph, 8, 8, 16)
        if len(rendered) != SMALL_GLYPH_BYTES:
            raise ValueError("unexpected 8x16 small-glyph size")
        offset = SMALL_FONT_OFFSET + index * SMALL_GLYPH_BYTES
        if offset + SMALL_GLYPH_BYTES > len(source):
            raise ValueError("small label/name glyph range does not fit in source EXE")
        output[offset:offset + SMALL_GLYPH_BYTES] = rendered
        applied.append({
            "character": character,
            "message_byte": f"0x{index:02X}",
            "font_offset_hex": f"0x{offset:X}",
            "font_bytes": SMALL_GLYPH_BYTES,
            "cell": "8x16",
        })
    return bytes(output), {
        "glyph_count": len(applied),
        "glyphs": applied,
        "shared_label_character": "아",
        "cell": "8x16",
    }


def encode_pilot_name_text(
    korean: str, codec: Codec, mapping: dict[str, int]
) -> tuple[bytes, int]:
    """Encode a PILOTNAME value with the retail compact-name code page.

    The normal encoder deliberately emits every Hangul syllable as a wide
    F0-F5 pair.  FC09 names are the one exception: a syllable with an assigned
    compact cell is emitted as one byte so the battle name line keeps the
    Japanese advance.  Any syllable outside the 20-cell subset safely falls
    back to the normal wide font; it is never silently replaced by another
    character.
    """
    output = bytearray()
    compact_glyphs = 0
    position = 0
    while position < len(korean):
        character = korean[position]
        if character == "<":
            # PILOTNAME currently contains no control markup, but delegating a
            # future tagged value to the canonical encoder prevents this
            # compact loop from treating an opcode argument as text.
            encoded = encode_text(korean[position:], codec, mapping)
            output.extend(encoded)
            break
        if character in SMALL_BATTLE_NAME_GLYPHS:
            output.append(SMALL_BATTLE_NAME_GLYPHS[character])
            compact_glyphs += 1
        else:
            output.extend(encode_text(character, codec, mapping))
        position += 1
    return bytes(output), compact_glyphs


def encoded_display_advance(raw: bytes) -> int:
    """Return the renderer's x advance for one encoded string."""
    advance = 0
    position = 0
    while position < len(raw):
        byte = raw[position]
        if byte == 0xFF:
            break
        if 0xF0 <= byte <= 0xF5:
            if position + 1 >= len(raw):
                raise ValueError("truncated wide glyph while measuring text")
            advance += 3
            position += 2
        elif byte >= 0xF6:
            position += 1 + CONTROL_ARGS.get(byte, 0)
        else:
            advance += 2
            position += 1
    return advance


def encode_all(texts: list[str], codec: Codec, mapping: dict[str, int]) -> dict[str, int]:
    sizes = {"record_count": 0, "encoded_bytes": 0, "hangul_characters": 0}
    for text in texts:
        encoded = encode_text(text, codec, mapping)
        sizes["record_count"] += 1
        sizes["encoded_bytes"] += len(encoded)
        sizes["hangul_characters"] += sum("가" <= char <= "힣" for char in text)
    return sizes


def rebuild_scenario_record(
    old_raw: bytes,
    row: dict[str, Any],
    korean: str,
    codec: Codec,
    mapping: dict[str, int],
) -> tuple[bytes, str, bool]:
    """Encode one scenario record and preserve special fixed-size records."""
    encoded = encode_text(korean, codec, mapping)
    body_skip = int(row.get("body_skip", 0))
    if body_skip <= 0:
        return old_raw[:body_skip] + encoded + b"\xFF", korean, False

    if not old_raw or old_raw[-1] != 0xFF:
        raise ValueError(f"fixed scenario record is missing FF terminator: {row['id']}")
    available = len(old_raw) - body_skip - 1
    actual_korean = korean
    fallback_used = False
    if len(encoded) > available:
        fallback = FIXED_SCENARIO_FALLBACKS.get(row.get("japanese", ""))
        if fallback is None:
            raise ValueError(
                f"fixed scenario record exceeds its allocation: {row['id']} "
                f"({len(encoded)} > {available} text bytes)"
            )
        encoded = encode_text(fallback, codec, mapping)
        actual_korean = fallback
        fallback_used = True
        if len(encoded) > available:
            raise ValueError(
                f"fixed scenario fallback exceeds its allocation: {row['id']} "
                f"({len(encoded)} > {available} text bytes)"
            )

    padded_text = encoded + bytes(available - len(encoded))
    return old_raw[:body_skip] + padded_text + b"\xFF", actual_korean, fallback_used


def fit_scenario_zero_translations(
    records: list[dict[str, Any]],
    translations: dict[str, str],
    codec: Codec,
    mapping: dict[str, int],
    capacity: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Fit the opening cutscene into its immutable retail text-pool extent.

    The opening loader walks a retail-sized pool before entering its event
    program.  Moving the program or targeting records beyond the original
    decompressed member hangs before the first rendered message.  Preserve the
    first 35 records verbatim in wording/layout; compact only later records,
    preferring whitespace removal and then omission of the on-screen speaker
    label.  Dialogue bodies and renderer controls are never truncated.
    """
    effective = dict(translations)

    def normalise(text: str) -> str:
        lines = [re.sub(r" +", " ", line.strip()) for line in text.split("\n")]
        return "\n".join(lines)

    def encoded_record_size(row: dict[str, Any], text: str | None) -> int:
        old_raw = bytes.fromhex(row["raw_hex"])
        if text is None:
            return len(old_raw)
        if int(row.get("body_skip", 0)) > 0:
            return len(old_raw)
        return len(encode_text(text, codec, mapping)) + 1

    for row in records:
        record_id = row["id"]
        if (
            int(row.get("record_index", 0)) >= 35
            and record_id in effective
            and int(row.get("body_skip", 0)) <= 0
        ):
            effective[record_id] = normalise(effective[record_id])

    total = sum(
        encoded_record_size(row, effective.get(row["id"]))
        for row in records
    )
    before_bytes = total
    candidates: list[tuple[int, str, str]] = []
    for row in records:
        if int(row.get("record_index", 0)) < 35:
            continue
        record_id = row["id"]
        text = effective.get(record_id)
        if text is None or int(row.get("body_skip", 0)) > 0:
            continue
        compact = text.replace(" ", "")
        for opening in ("「", "("):
            marker = compact.find(opening)
            if 0 < marker <= 20:
                compact = compact[marker:]
                break
        saving = encoded_record_size(row, text) - encoded_record_size(row, compact)
        if saving > 0:
            candidates.append((saving, record_id, compact))

    compacted: list[dict[str, Any]] = []
    for saving, record_id, compact in sorted(candidates, reverse=True):
        if total <= capacity:
            break
        effective[record_id] = compact
        total -= saving
        compacted.append({"id": record_id, "saved_bytes": saving})

    if total > capacity:
        raise ValueError(
            f"scenario 0 cannot fit immutable retail pool without truncation: "
            f"{total} > {capacity}"
        )
    return effective, {
        "capacity_bytes": capacity,
        "before_compaction_bytes": before_bytes,
        "after_compaction_bytes": total,
        "padding_bytes": capacity - total,
        "compacted_record_count": len(compacted),
        "saved_bytes": before_bytes - total,
        "preserved_initial_record_count": 35,
        "dialogue_bodies_truncated": False,
        "compacted_records": compacted,
    }


def fit_member_translations(
    records: list[dict[str, Any]],
    translations: dict[str, str],
    codec: Codec,
    mapping: dict[str, int],
    capacity: int,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Compact an ordinary member only as far as the 16 KiB VM buffer demands.

    Korean is denser in glyphs but wider in bytes than the retail Japanese, so a
    handful of the largest scenarios decompress past 0x801E4000.  Shorten those
    by removing redundant spacing, longest saving first, and stop the moment the
    member fits.  Every other member keeps its wording untouched.
    """

    def encoded_record_size(row: dict[str, Any], text: str | None) -> int:
        old_raw = bytes.fromhex(row["raw_hex"])
        if text is None:
            return len(old_raw)
        if int(row.get("body_skip", 0)) > 0:
            return len(old_raw)
        return len(encode_text(text, codec, mapping)) + 1

    total = sum(
        encoded_record_size(row, translations.get(row["id"]))
        for row in records
    )
    if total <= capacity:
        return translations, None

    effective = dict(translations)
    before_bytes = total
    candidates: list[tuple[int, str, str, str]] = []
    for row in records:
        record_id = row["id"]
        text = effective.get(record_id)
        if text is None or int(row.get("body_skip", 0)) > 0:
            continue
        current = encoded_record_size(row, text)
        tidy = NEWLINE.join(
            re.sub(r" +", " ", line.strip()) for line in text.split(NEWLINE)
        )
        squeezed = tidy.replace(" ", "")
        for variant, kind in ((tidy, "whitespace"), (squeezed, "spaceless")):
            saving = current - encoded_record_size(row, variant)
            if saving > 0:
                candidates.append((saving, record_id, variant, kind))

    applied: list[dict[str, Any]] = []
    used: set[str] = set()
    for saving, record_id, variant, kind in sorted(candidates, reverse=True):
        if total <= capacity:
            break
        if record_id in used:
            continue
        used.add(record_id)
        total -= saving
        effective[record_id] = variant
        applied.append({"id": record_id, "saved_bytes": saving, "kind": kind})

    if total > capacity:
        raise ValueError(
            f"member text pool cannot fit the 16 KiB scenario buffer: "
            f"{total} > {capacity}"
        )
    return effective, {
        "capacity_bytes": capacity,
        "before_compaction_bytes": before_bytes,
        "after_compaction_bytes": total,
        "compacted_record_count": len(applied),
        "saved_bytes": before_bytes - total,
        "dialogue_bodies_truncated": False,
        "compacted_records": applied,
    }


def relocate_scenario_zero_event_references(
    rebuilt: bytearray,
    records: list[dict[str, Any]],
    new_record_starts: dict[str, int],
    new_record_raws: dict[str, bytes],
) -> int:
    """Relocate the opening-event tables embedded in scenario 0 records.

    Scenario 0 is not a plain dialogue pool.  Its first two records contain
    five-byte event entries in the form

        opcode, actor/effect u16, signed text displacement s16

    The displacement is relative to its own two-byte operand.  The first u16
    is not a pointer: changing it corrupts the speaker/effect selection.  Keep
    that field byte-for-byte and recalculate only the signed displacement.
    """
    by_id = {row["id"]: row for row in records}
    old_ranges = [
        (
            int(row["source_offset"]),
            int(row["source_offset"]) + len(bytes.fromhex(row["raw_hex"])),
            row,
        )
        for row in records
    ]

    def relocate_target(old_target: int) -> int:
        for old_start, old_end, row in old_ranges:
            if old_start <= old_target < old_end:
                relative = old_target - old_start
                old_raw = bytes.fromhex(row["raw_hex"])
                new_raw = new_record_raws[row["id"]]
                if relative and old_raw[:relative] != new_raw[:relative]:
                    raise ValueError(
                        f"scenario 0 internal event anchor changed before "
                        f"{row['id']}+{relative:#x}"
                    )
                return new_record_starts[row["id"]] + relative
        raise ValueError(
            f"scenario 0 event operand does not target a record: {old_target:#x}"
        )

    changed = 0

    def patch_entries(record_id: str, entry_offsets: list[int]) -> None:
        nonlocal changed
        row = by_id[record_id]
        old_raw = bytes.fromhex(row["raw_hex"])
        for entry_offset in entry_offsets:
            if entry_offset >= len(old_raw) or old_raw[entry_offset] not in (0x80, 0x84):
                raise ValueError(
                    f"unexpected scenario 0 event opcode at {record_id}+{entry_offset:#x}"
                )
            old_actor = struct.unpack_from("<H", old_raw, entry_offset + 1)[0]
            if not 0x0800 <= old_actor <= 0x09FF:
                raise ValueError(
                    f"unexpected scenario 0 actor/effect value at "
                    f"{record_id}+{entry_offset:#x}: {old_actor:#x}"
                )
            old_operand = int(row["source_offset"]) + entry_offset + 3
            old_displacement = struct.unpack_from("<h", old_raw, entry_offset + 3)[0]
            new_target = relocate_target(old_operand + old_displacement)
            new_command = new_record_starts[record_id] + entry_offset
            new_operand = new_command + 3
            new_displacement = new_target - new_operand
            if not -0x8000 <= new_displacement <= 0x7FFF:
                raise ValueError(
                    f"scenario 0 event displacement exceeds s16: "
                    f"{new_displacement} at {record_id}+{entry_offset:#x}"
                )
            output_offset = new_command
            if rebuilt[output_offset] != old_raw[entry_offset]:
                raise ValueError(
                    f"scenario 0 event opcode moved at {record_id}+{entry_offset:#x}"
                )
            if struct.unpack_from("<H", rebuilt, output_offset + 1)[0] != old_actor:
                raise ValueError(
                    f"scenario 0 actor/effect field changed at "
                    f"{record_id}+{entry_offset:#x}"
                )
            current = struct.unpack_from("<h", rebuilt, output_offset + 3)[0]
            struct.pack_into("<h", rebuilt, output_offset + 3, new_displacement)
            if current != new_displacement:
                changed += 1

    # SCE-000-0000: two four-byte header words and 32 full entries.  A 33rd
    # entry starts at +0xA8, but the extractor split it after its displacement
    # low byte FF.  The displacement high byte is the first byte (07) of
    # SCE-000-0001.  In the retail member that cross-record value targets
    # SCE-000-0034.  Treating the FF as a text terminator leaves this pointer
    # stale after repacking and hangs the opening event before its first line.
    patch_entries(
        "SCE-000-0000",
        [8 + 5 * index for index in range(32)],
    )

    split_row = by_id["SCE-000-0000"]
    continuation_row = by_id["SCE-000-0001"]
    split_raw = bytes.fromhex(split_row["raw_hex"])
    continuation_raw = bytes.fromhex(continuation_row["raw_hex"])
    split_entry_offset = 8 + 5 * 32
    if len(split_raw) != split_entry_offset + 4 or not continuation_raw:
        raise ValueError("unexpected scenario 0 split event-entry boundary")
    old_command = int(split_row["source_offset"]) + split_entry_offset
    old_opcode = split_raw[split_entry_offset]
    old_actor = struct.unpack_from("<H", split_raw, split_entry_offset + 1)[0]
    if old_opcode != 0x84 or not 0x0800 <= old_actor <= 0x09FF:
        raise ValueError("unexpected scenario 0 split event entry")
    old_operand = old_command + 3
    old_displacement = struct.unpack(
        "<h", bytes((split_raw[-1], continuation_raw[0]))
    )[0]
    old_target = old_operand + old_displacement
    expected_target = int(by_id["SCE-000-0034"]["source_offset"])
    if old_target != expected_target:
        raise ValueError(
            f"scenario 0 split entry target mismatch: "
            f"{old_target:#x} != {expected_target:#x}"
        )
    new_target = new_record_starts["SCE-000-0034"]
    new_displacement = new_target - old_operand
    if not -0x8000 <= new_displacement <= 0x7FFF:
        raise ValueError("scenario 0 split event displacement exceeds s16")
    if rebuilt[old_command] != old_opcode:
        raise ValueError("scenario 0 split event opcode changed")
    if struct.unpack_from("<H", rebuilt, old_command + 1)[0] != old_actor:
        raise ValueError("scenario 0 split event actor/effect field changed")
    current = struct.unpack_from("<h", rebuilt, old_operand)[0]
    struct.pack_into("<h", rebuilt, old_operand, new_displacement)
    if current != new_displacement:
        changed += 1

    # SCE-000-0001: the leading byte completes the split entry above, followed
    # by 158 five-byte entries and a four-byte prologue header.
    patch_entries(
        "SCE-000-0001",
        [1 + 5 * index for index in range(158)],
    )
    return changed


def relocate_post_pool_event_references(
    rebuilt: bytearray,
    decompressed: bytes,
    records: list[dict[str, Any]],
    new_record_starts: dict[str, int],
    new_record_raws: dict[str, bytes],
    old_signature: int,
    new_signature: int,
) -> int:
    """Retarget event-script operands that point into the moved text pool.

    The scenario's event VM is stored after the shared lookup-table signature.
    Dialogue dispatch commands use the same five-byte form as scenario 0:
    opcode, actor/effect u16, signed displacement s16.  Commands move together
    with the post-pool script, while their target records move by individual
    translation lengths.  Recalculate the displacement at +3 and preserve the
    actor/effect field at +1 exactly.

    Six opcodes carry this form.  Measured over the 73 distinct retail members,
    every one of them resolves onto an exact record start:

        opcode  occurrences  targets a record  lands on a record start
        0x80           1564              1318                    1318
        0x81            410               299                     299
        0x82             81                26                      26
        0x83            913               804                     804
        0x84           3059              3008                    3006
        0x85           1162              1064                    1063

    0x86-0x8F occur too (324, 132, 120, 741, 187, 652, 302, 134, 195, 205
    times) but never resolve into the text pool, so they are a different
    command family and must not be touched.  Omitting 0x81/0x82/0x83 left 1129
    dialogue pointers aimed at pre-move addresses, which is what made mid-game
    lines show the wrong speaker or the wrong text.
    """
    old_ranges = [
        (
            int(row["source_offset"]),
            int(row["source_offset"]) + len(bytes.fromhex(row["raw_hex"])),
            row,
        )
        for row in records
    ]

    def target_row(value: int) -> tuple[dict[str, Any], int] | None:
        for old_start, old_end, row in old_ranges:
            if old_start <= value < old_end:
                return row, value - old_start
        return None

    tail_delta = new_signature - old_signature
    changed = 0
    for old_position in range(old_signature + 8, len(decompressed) - 4):
        opcode = decompressed[old_position]
        if opcode not in DIALOGUE_DISPATCH_OPCODES:
            continue
        old_actor = struct.unpack_from("<H", decompressed, old_position + 1)[0]
        # Each family carries its own actor/effect id window.  Checking it keeps
        # an opcode-shaped byte inside an opaque script payload from being
        # mistaken for a command.
        low, high = DISPATCH_ACTOR_RANGES[opcode]
        if not low <= old_actor <= high:
            continue
        old_operand = old_position + 3
        old_displacement = struct.unpack_from("<h", decompressed, old_operand)[0]
        target = target_row(old_operand + old_displacement)
        if target is None:
            continue
        row, relative = target
        old_raw = bytes.fromhex(row["raw_hex"])
        new_raw = new_record_raws[row["id"]]
        if relative and old_raw[:relative] != new_raw[:relative]:
            # Valid internal entries in this game skip an unchanged dot/prefix
            # run.  A changed prefix indicates an accidental byte-pattern hit.
            continue
        new_target = new_record_starts[row["id"]] + relative
        new_position = old_position + tail_delta
        new_operand = new_position + 3
        new_displacement = new_target - new_operand
        if not -0x8000 <= new_displacement <= 0x7FFF:
            raise ValueError(
                f"scenario event displacement exceeds s16: "
                f"{new_displacement} at {old_position:#x}"
            )
        if new_position < 0 or new_position + 5 > len(rebuilt):
            raise ValueError(
                f"scenario event operand moved outside member: "
                f"{old_position:#x} -> {new_position:#x}"
            )
        if rebuilt[new_position] != opcode:
            raise ValueError(
                f"scenario event opcode mismatch at {new_position:#x}: "
                f"expected {opcode:02X}, got {rebuilt[new_position]:02X}"
            )
        if struct.unpack_from("<H", rebuilt, new_position + 1)[0] != old_actor:
            raise ValueError(
                f"scenario actor/effect field changed at {new_position:#x}"
            )
        current = struct.unpack_from("<h", rebuilt, new_operand)[0]
        struct.pack_into("<h", rebuilt, new_operand, new_displacement)
        if current != new_displacement:
            changed += 1
    return changed


def rebuild_fixed_scenario_member(
    source_member: bytes,
    decompressed: bytes,
    old_consumed: int,
    records: list[dict[str, Any]],
    translations: dict[str, str],
    codec: Codec,
    mapping: dict[str, int],
    member_index: int,
    representative_index: int,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    """Patch text without moving any record or event byte."""
    rebuilt = bytearray(decompressed)
    applied: list[dict[str, Any]] = []
    for row in records:
        record_id = row["id"]
        if record_id not in translations:
            continue
        start = int(row["source_offset"])
        old_raw = bytes.fromhex(row["raw_hex"])
        if rebuilt[start:start + len(old_raw)] != old_raw:
            raise ValueError(f"fixed scenario source mismatch: {record_id}")
        body_skip = int(row.get("body_skip", 0))
        encoded = encode_text(translations[record_id], codec, mapping)
        available = len(old_raw) - body_skip - 1
        actual_korean = translations[record_id]
        fallback_used = False
        if len(encoded) > available:
            fallback = FIXED_SCENARIO_FALLBACKS.get(row.get("japanese", ""))
            if fallback is None:
                raise ValueError(
                    f"fixed scenario translation exceeds {record_id}: "
                    f"{len(encoded)} > {available} bytes"
                )
            encoded = encode_text(fallback, codec, mapping)
            actual_korean = fallback
            fallback_used = True
        if len(encoded) > available:
            raise ValueError(
                f"fixed scenario fallback exceeds {record_id}: "
                f"{len(encoded)} > {available} bytes"
            )
        new_raw = (
            old_raw[:body_skip]
            + encoded
            + bytes(available - len(encoded))
            + b"\xFF"
        )
        if len(new_raw) != len(old_raw):
            raise AssertionError(f"fixed scenario record size changed: {record_id}")
        rebuilt[start:start + len(old_raw)] = new_raw
        applied.append({
            "id": record_id,
            "member_index": member_index,
            "representative_scenario": representative_index,
            "old_record_bytes": len(old_raw),
            "new_record_bytes": len(new_raw),
            "growth_bytes": 0,
            "korean": actual_korean,
            "fixed_size_record": True,
            "fixed_size_fallback": fallback_used,
        })

    unreached = patch_unreached_strings(rebuilt, codec, mapping, member_index)
    applied.extend(unreached)
    if len(rebuilt) > SCEDATA_MEMBER_BUFFER_BYTES:
        raise ValueError(
            f"member {member_index} decompresses to {len(rebuilt)} bytes, past the "
            f"{SCEDATA_MEMBER_BUFFER_BYTES}-byte scenario buffer at 0x801E0000"
        )
    encoder = load_encoder()
    compressed = encoder.compress(bytes(rebuilt), level=8)
    round_trip, consumed = srw_lz_decompress(compressed)
    if round_trip != bytes(rebuilt) or consumed != len(compressed):
        raise ValueError(f"fixed scenario LZ round trip failed at member {member_index}")
    new_chunk = compressed + bytes(align(len(compressed), 4) - len(compressed))
    report = {
        "member_index": member_index,
        "representative_scenario": representative_index,
        "translated_record_count": len(applied),
        "old_decompressed_bytes": len(decompressed),
        "new_decompressed_bytes": len(rebuilt),
        "decompressed_growth_bytes": 0,
        "old_compressed_stream_bytes": old_consumed,
        "new_compressed_stream_bytes": len(compressed),
        "old_member_allocation_bytes": len(source_member),
        "new_member_allocation_bytes": len(new_chunk),
        "relocated_event_references": 0,
        "fixed_record_layout": True,
        "record_boundaries_changed": False,
        "event_program_changed": False,
    }
    return bytes(new_chunk), report, applied


def rebuild_scenario_member(
    source_member: bytes,
    scenario_doc: dict[str, Any],
    translations: dict[str, str],
    codec: Codec,
    mapping: dict[str, int],
    member_index: int,
    representative_index: int,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    representative = scenario_doc["scenarios"][representative_index]
    records = representative["records"]
    decompressed, old_consumed = srw_lz_decompress(source_member)
    if not records:
        raise ValueError(f"scenario {representative_index} has no records")
    if representative_index == 1:
        return rebuild_fixed_scenario_member(
            source_member,
            decompressed,
            old_consumed,
            records,
            translations,
            codec,
            mapping,
            member_index,
            representative_index,
        )
    pool_start = int(representative["text_pool_start"])
    pool_end = int(representative["text_pool_end"])
    old_signature = decompressed.find(SCENARIO_TEXT_TAIL_SIGNATURE, pool_end)
    if old_signature < 0:
        raise ValueError(f"scenario tail signature missing at member {member_index}")

    effective_translations = translations
    scenario_zero_compaction: dict[str, Any] | None = None
    member_compaction: dict[str, Any] | None = None
    if representative_index == 0:
        effective_translations, scenario_zero_compaction = fit_scenario_zero_translations(
            records,
            translations,
            codec,
            mapping,
            old_signature - pool_start,
        )
    else:
        # An ordinary member may grow its pool, but the whole decompressed
        # member still has to land inside the VM's 16 KiB buffer.
        tail_bytes = len(decompressed) - old_signature
        pool_capacity = (
            SCEDATA_MEMBER_BUFFER_BYTES
            - SCEDATA_MEMBER_SAFETY_MARGIN
            - pool_start
            - tail_bytes
            - 3
        )
        effective_translations, member_compaction = fit_member_translations(
            records, translations, codec, mapping, pool_capacity
        )

    new_pool = bytearray()
    applied: list[dict[str, Any]] = []
    new_record_starts: dict[str, int] = {}
    new_record_raws: dict[str, bytes] = {}
    for row in records:
        record_id = row["id"]
        start = int(row["source_offset"])
        old_size = len(bytes.fromhex(row["raw_hex"]))
        old_raw = decompressed[start:start + old_size]
        if len(old_raw) != old_size:
            raise ValueError(f"scenario source record truncated: {record_id}")
        new_record_starts[record_id] = pool_start + len(new_pool)
        if record_id in effective_translations:
            body_skip = int(row.get("body_skip", 0))
            new_raw, actual_korean, fallback_used = rebuild_scenario_record(
                old_raw, row, effective_translations[record_id], codec, mapping
            )
            applied.append({
                "id": record_id,
                "member_index": member_index,
                "representative_scenario": representative_index,
                "old_record_bytes": len(old_raw),
                "new_record_bytes": len(new_raw),
                "growth_bytes": len(new_raw) - len(old_raw),
                "korean": actual_korean,
                "fixed_size_record": body_skip > 0,
                "fixed_size_fallback": fallback_used,
            })
        else:
            new_raw = old_raw
        new_record_raws[record_id] = bytes(new_raw)
        new_pool.extend(new_raw)

    relocated_event_references = 0
    scenario_zero_tail_stable = representative_index == 0
    overflow_record_count = 0
    overflow_payload_bytes = 0
    overflow_start: int | None = None
    if scenario_zero_tail_stable:
        # Scenario 0 is the opening cutscene.  Unlike ordinary map scenarios,
        # its post-pool event program contains opaque internal addresses.  Do
        # not shift that program at all.  Keep the first two table/prologue
        # records at their retail addresses and repack every later translated
        # record inside the immutable retail pool.  The opening table's signed
        # message displacements are then the only values that need relocation.
        rebuilt = bytearray(decompressed)
        for row in records[:2]:
            record_id = row["id"]
            old_start = int(row["source_offset"])
            old_size = len(bytes.fromhex(row["raw_hex"]))
            new_raw = new_record_raws[record_id]
            if len(new_raw) != old_size:
                raise ValueError(
                    f"scenario 0 fixed prefix changed size: {record_id} "
                    f"({len(new_raw)} != {old_size})"
                )
            rebuilt[old_start:old_start + old_size] = new_raw
            new_record_starts[record_id] = old_start

        first_relocatable = int(records[2]["source_offset"])
        rebuilt[first_relocatable:old_signature] = bytes(old_signature - first_relocatable)
        retail_cursor = first_relocatable
        for row in records[2:]:
            record_id = row["id"]
            new_raw = new_record_raws[record_id]
            if retail_cursor + len(new_raw) > old_signature:
                raise ValueError(
                    f"scenario 0 immutable pool overflow at {record_id}: "
                    f"{retail_cursor + len(new_raw):#x} > {old_signature:#x}"
                )
            new_record_starts[record_id] = retail_cursor
            rebuilt[retail_cursor:retail_cursor + len(new_raw)] = new_raw
            retail_cursor += len(new_raw)
        new_signature = old_signature
        tail_delta = 0
        relocated_event_references = relocate_scenario_zero_event_references(
            rebuilt, records, new_record_starts, new_record_raws
        )
    else:
        new_signature = align(pool_start + len(new_pool), 4)
        rebuilt = bytearray(
            decompressed[:pool_start]
            + bytes(new_pool)
            + bytes(new_signature - pool_start - len(new_pool))
            + decompressed[old_signature:]
        )
        tail_delta = new_signature - old_signature

    relocated_event_references += relocate_post_pool_event_references(
        rebuilt,
        decompressed,
        records,
        new_record_starts,
        new_record_raws,
        old_signature,
        new_signature,
    )
    # Sixteen handler slots, not fourteen.  The VM reads a 0xFF00..0xFF0F token as
    # buffer + header[token & 0x0F], so the table has to be 0x40 bytes long, and
    # measuring the archive agrees: the words at 0x38 and 0x3C are a valid
    # post-pool address in all 73 distinct members while 0x40 and 0x44 never are,
    # and slot 14 equals slot 15 in every member.  Rebasing only fourteen of them
    # left the last two pointing into the middle of the Korean pool in all 71
    # members whose tail moved.  They live inside record 0's preserved 12-byte
    # prefix, so writing them back here overwrites the stale copies the repack
    # carried along.
    old_headers = list(struct.unpack_from("<16I", decompressed, 0))
    new_headers = []
    for index, value in enumerate(old_headers):
        adjusted = value + tail_delta if value >= old_signature else value
        struct.pack_into("<I", rebuilt, index * 4, adjusted)
        new_headers.append(adjusted)

    # Handler slot 13 is a three-byte `78 <s16> 75` stub whose displacement is
    # relative to its own operand and aims at a fixed four-byte struct below the
    # pool start.  The stub travels with the post-pool script while its target
    # does not, so the retail displacement goes stale the moment the pool grows -
    # in member 3 it ends up 0x4A0 bytes into the Korean text.  Retarget it.
    stub = new_headers[13]
    if 0 <= stub < len(rebuilt) - 3 and rebuilt[stub] == 0x78 and rebuilt[stub + 3] == 0x75:
        old_stub = old_headers[13]
        old_target = old_stub + 1 + struct.unpack_from("<h", decompressed, old_stub + 1)[0]
        # The stub aims at a four-byte struct that the extractor sees either inside
        # record 0's preserved prefix or as a record of its own, so resolve it the
        # same way a dialogue dispatch is resolved and follow the record.
        new_target = None
        for row in records:
            row_start = int(row["source_offset"])
            row_raw = bytes.fromhex(row["raw_hex"])
            if not row_start <= old_target < row_start + len(row_raw):
                continue
            relative = old_target - row_start
            if relative and row_raw[:relative] != new_record_raws[row["id"]][:relative]:
                break
            new_target = new_record_starts[row["id"]] + relative
            break
        if new_target is not None:
            new_displacement = new_target - (stub + 1)
            if not -0x8000 <= new_displacement <= 0x7FFF:
                raise ValueError(
                    f"member {member_index} handler-13 stub displacement exceeds s16"
                )
            struct.pack_into("<h", rebuilt, stub + 1, new_displacement)
            if stub + 1 + struct.unpack_from("<h", rebuilt, stub + 1)[0] != new_target:
                raise ValueError(f"member {member_index} handler-13 stub retarget failed")
            relocated_event_references += 1
    unreached = patch_unreached_strings(rebuilt, codec, mapping, member_index)
    applied.extend(unreached)
    if rebuilt.find(SCENARIO_TEXT_TAIL_SIGNATURE, pool_start) != new_signature:
        raise ValueError(f"scenario tail shift failed at member {member_index}")
    if len(rebuilt) > SCEDATA_MEMBER_BUFFER_BYTES:
        raise ValueError(
            f"member {member_index} decompresses to {len(rebuilt)} bytes, past the "
            f"{SCEDATA_MEMBER_BUFFER_BYTES}-byte scenario buffer at 0x801E0000"
        )

    encoder = load_encoder()
    compressed = encoder.compress(bytes(rebuilt), level=8)
    round_trip, consumed = srw_lz_decompress(compressed)
    if round_trip != bytes(rebuilt) or consumed != len(compressed):
        raise ValueError(f"scenario LZ round trip failed at member {member_index}")
    new_chunk = compressed + bytes(align(len(compressed), 4) - len(compressed))
    report = {
        "member_index": member_index,
        "representative_scenario": representative_index,
        "translated_record_count": len(applied),
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
        "old_compressed_stream_bytes": old_consumed,
        "new_compressed_stream_bytes": len(compressed),
        "old_member_allocation_bytes": len(source_member),
        "new_member_allocation_bytes": len(new_chunk),
        "relocated_event_references": relocated_event_references,
        "scenario_zero_tail_stable": scenario_zero_tail_stable,
        "overflow_record_count": overflow_record_count,
        "overflow_payload_bytes": overflow_payload_bytes,
        "overflow_start": overflow_start,
        "scenario_zero_compaction": scenario_zero_compaction,
        "member_compaction": member_compaction,
    }
    return bytes(new_chunk), report, applied


def rebuild_scedata(
    source: bytes,
    scenario_doc: dict[str, Any],
    translations: dict[str, str],
    codec: Codec,
    mapping: dict[str, int],
    preserve_scenarios: set[int] | None = None,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    pointers = list(struct.unpack_from(f"<{SCENARIO_INDEX_COUNT}I", source, 0))
    chunks = []
    member_reports = []
    applied: list[dict[str, Any]] = []
    translated_member_count = 0
    preserve_scenarios = preserve_scenarios or set()
    for member_index, start in enumerate(pointers):
        end = pointers[member_index + 1] if member_index + 1 < len(pointers) else len(source)
        old_chunk = source[start:end]
        row = scenario_doc["scenarios"][member_index]
        representative = row.get("duplicate_of_scenario")
        if representative is None:
            representative = member_index
        representative_row = scenario_doc["scenarios"][representative]
        if representative in preserve_scenarios:
            new_chunk = old_chunk
            report = {
                "member_index": member_index,
                "representative_scenario": representative,
                "translated_record_count": 0,
                "preserved_original_member": True,
                "old_member_allocation_bytes": len(old_chunk),
                "new_member_allocation_bytes": len(new_chunk),
            }
        elif representative_row["records"]:
            new_chunk, report, member_applied = rebuild_scenario_member(
                old_chunk, scenario_doc, translations, codec, mapping,
                member_index, representative,
            )
            if member_index in (0, 1):
                if len(new_chunk) > len(old_chunk):
                    raise ValueError(
                        f"startup scenario member {member_index} exceeds its "
                        f"retail allocation: {len(new_chunk)} > {len(old_chunk)}"
                    )
                startup_padding = len(old_chunk) - len(new_chunk)
                new_chunk += bytes(startup_padding)
                report["startup_member_allocation_preserved"] = True
                report["startup_member_padding_bytes"] = startup_padding
                report["new_member_allocation_bytes"] = len(new_chunk)
            translated_member_count += 1
            applied.extend(member_applied)
        else:
            # No records, so nothing relocates - but member 96 keeps the seven
            # defeat messages here, and leaving the chunk alone shipped them as
            # garbled Japanese once the font pass reassigned their kanji slots.
            new_chunk = old_chunk
            member_applied = []
            try:
                decompressed_pass, _consumed = srw_lz_decompress(old_chunk)
            except Exception:
                decompressed_pass = None
            if decompressed_pass is not None:
                buffer = bytearray(decompressed_pass)
                member_applied = patch_unreached_strings(buffer, codec, mapping, member_index)
                if member_applied:
                    encoder = load_encoder()
                    compressed = encoder.compress(bytes(buffer), level=8)
                    round_trip, consumed = srw_lz_decompress(compressed)
                    if round_trip != bytes(buffer) or consumed != len(compressed):
                        raise ValueError(
                            f"pass-through LZ round trip failed at member {member_index}"
                        )
                    new_chunk = compressed + bytes(align(len(compressed), 4) - len(compressed))
                    applied.extend(member_applied)
            report = {
                "member_index": member_index,
                "representative_scenario": representative,
                "translated_record_count": 0,
                "old_member_allocation_bytes": len(old_chunk),
                "new_member_allocation_bytes": len(new_chunk),
            }
        chunks.append(new_chunk)
        member_reports.append(report)

    # Retail fills the 54 unused index slots with byte-identical copies of scenario
    # 0 - 47% of the archive spent on the same 5.7 KB member, which the Korean
    # script cannot afford inside SCEDATA's retail extent.
    #
    # Every slot still has to keep a strictly increasing pointer.  The loader
    # sizes a member as pointer[i + 1] - pointer[i], and the CD request builder
    # reads a length of zero as "read to the end of the file": the rest of the
    # archive would be streamed through the 16 KiB decompression buffer.  An
    # earlier revision pointed whole runs of identical members at one payload,
    # which left 52 slots measuring zero - harmless while nothing selects them,
    # but a hard lock the moment something does.  So give each unused slot its own
    # copy of the archive's smallest genuine member instead: an independently
    # sized, legal entry for a fraction of retail's cost.
    live_members = {
        index
        for index, row in enumerate(scenario_doc["scenarios"])
        if row.get("duplicate_of_scenario") is None
    }
    stub_chunk = min((chunks[index] for index in sorted(live_members)), key=len)
    output = bytearray(0x200)
    new_pointers = []
    stub_member_count = 0
    for index, chunk in enumerate(chunks):
        if index not in live_members:
            chunk = stub_chunk
            stub_member_count += 1
            member_reports[index]["stub_member"] = True
            member_reports[index]["new_member_allocation_bytes"] = len(chunk)
        new_pointers.append(len(output))
        output.extend(chunk)
    if any(b <= a for a, b in zip(new_pointers, new_pointers[1:])):
        raise ValueError("SCEDATA index must increase strictly at every slot")
    struct.pack_into(f"<{SCENARIO_INDEX_COUNT}I", output, 0, *new_pointers)
    report = {
        "stub_member_count": stub_member_count,
        "stub_member_bytes": len(stub_chunk),
        "live_member_count": len(live_members),
        "member_count": len(chunks),
        "translated_member_count": translated_member_count,
        "translated_record_application_count": len(applied),
        "old_scedata_bytes": len(source),
        "new_scedata_bytes": len(output),
        "scedata_growth_bytes": len(output) - len(source),
        "old_pointer_count": len(pointers),
        "new_pointer_count": len(new_pointers),
        "shifted_pointer_count": sum(a != b for a, b in zip(pointers, new_pointers)),
        "member_reports": member_reports,
    }
    return bytes(output), report, applied


def rebuild_bttmes(
    source: bytes,
    battle_doc: dict[str, Any],
    translations: dict[str, str],
    codec: Codec,
    mapping: dict[str, int],
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    table_bytes = struct.unpack_from("<I", source, 0)[0]
    if not table_bytes or table_bytes % 4:
        raise ValueError("invalid BTTMES pointer table")
    pointer_count = table_bytes // 4
    pointers = list(struct.unpack_from(f"<{pointer_count}I", source, 0))
    records = sorted(battle_doc["records"], key=lambda row: int(row["source_offset"]))
    cursor = table_bytes
    output = bytearray(source[:table_bytes])
    changes = []
    for row in records:
        start = int(row["source_offset"])
        end = int(row["record_end"])
        if start < cursor or end > len(source):
            raise ValueError(f"invalid BTTMES record range: {row['id']}")
        output.extend(source[cursor:start])
        header = source[start:start + 4]
        original_body = source[start + 4:end]
        if row["id"] not in translations:
            new_body = original_body
        else:
            new_body = encode_text(translations[row["id"]], codec, mapping) + b"\xFF"
        new_start = len(output)
        output.extend(header + new_body)
        new_end = len(output)
        changes.append({
            "id": row["id"],
            "old_source_offset": start,
            "new_source_offset": new_start,
            "old_record_bytes": end - start,
            "new_record_bytes": 4 + len(new_body),
            "growth_bytes": (4 + len(new_body)) - (end - start),
            "korean": translations.get(row["id"], ""),
        })
        cursor = end
    output.extend(source[cursor:])

    def relocate(old_offset: int) -> int:
        delta = 0
        for change in changes:
            old_start = change["old_source_offset"]
            old_end = old_start + change["old_record_bytes"]
            if old_offset >= old_end:
                delta += change["growth_bytes"]
            elif old_offset >= old_start:
                return old_offset + delta
            else:
                break
        return old_offset + delta

    new_pointers = [relocate(value) for value in pointers]
    struct.pack_into(f"<{pointer_count}I", output, 0, *new_pointers)
    if any(value > len(output) for value in new_pointers):
        raise ValueError("BTTMES pointer moved outside rebuilt file")

    # Every bank opens with a 13-entry little-endian u16 index.  Entry N locates
    # a battle sequence at bank + entry[N] + 2*N (resolved by 0x800DBE68).
    # Entries 1..12 are the FC 09 sequences and sit ahead of the message text,
    # so they never move.  Entry 0 targets the remap table the FC 08 message
    # opcode reads to turn an operand into a message id, and that table sits
    # AFTER the message text: measured on the retail archive, 157 of the 158
    # banks have it there.  Leaving it stale makes the battle text sequence
    # read Korean glyph bytes as a message id and stall with an empty window.
    bank_index_entries = 13
    bank_index_bytes = bank_index_entries * 2
    banks = sorted({value for value in pointers if table_bytes <= value < len(source)})
    bank_bounds = banks + [len(source)]

    def content_length(image: bytes, start: int, end: int) -> int:
        cut = end
        while cut > start and image[cut - 1] == 0:
            cut -= 1
        return cut - start

    rebuilt_banks = 0
    rebuilt_bank_entries = 0
    degenerate_banks = 0
    relocated_cross_bank_entries = 0
    preserved_degenerate_entries = 0
    for position, old_bank in enumerate(banks):
        if old_bank + bank_index_bytes > len(source):
            raise ValueError(f"BTTMES bank at {old_bank:#x} has no room for its index")
        new_bank = relocate(old_bank)
        old_span = content_length(source, old_bank, bank_bounds[position + 1])
        old_entries = struct.unpack_from(f"<{bank_index_entries}H", source, old_bank)
        if any(
            value + 2 * index >= old_span
            for index, value in enumerate(old_entries)
        ):
            # Retail record 145 is a 7-byte bank whose index contains pointers
            # outside its own span.  It is not safe to treat that whole index
            # as a local table, but it is also not safe to leave every value
            # untouched: three of the values are cross-bank pointers into data
            # that moves when earlier Korean messages grow.  Relocate only
            # targets which are valid offsets in a different bank; preserve
            # the in-bank dummy values and genuinely invalid values
            # byte-for-byte.  In this bank the first three entries are the
            # only real cross-bank references; the remaining ten values are
            # an FF sentinel followed by zero-filled index space.
            new_entries = []
            for index, value in enumerate(old_entries):
                old_target = old_bank + value + 2 * index
                is_cross_bank = old_target < old_bank or old_target >= bank_bounds[position + 1]
                if not is_cross_bank or not 0 <= old_target < len(source):
                    new_entries.append(value)
                    preserved_degenerate_entries += 1
                    continue
                new_target = relocate(old_target)
                new_value = new_target - new_bank - 2 * index
                if not 0 <= new_value <= 0xFFFF:
                    new_entries.append(value)
                    preserved_degenerate_entries += 1
                    continue
                new_entries.append(new_value)
                if new_target != old_target:
                    relocated_cross_bank_entries += 1
            if new_entries != list(old_entries):
                rebuilt_banks += 1
                rebuilt_bank_entries += sum(
                    a != b for a, b in zip(old_entries, new_entries)
                )
            struct.pack_into(
                f"<{bank_index_entries}H", output, new_bank, *new_entries
            )
            degenerate_banks += 1
            continue
        new_entries = []
        for index, value in enumerate(old_entries):
            old_target = old_bank + value + 2 * index
            new_value = relocate(old_target) - new_bank - 2 * index
            if not 0 <= new_value <= 0xFFFF:
                raise ValueError(
                    f"BTTMES bank {old_bank:#x} index {index} moved out of u16 range: "
                    f"{new_value}"
                )
            new_entries.append(new_value)
        if new_entries != list(old_entries):
            rebuilt_banks += 1
            rebuilt_bank_entries += sum(
                a != b for a, b in zip(old_entries, new_entries)
            )
        struct.pack_into(f"<{bank_index_entries}H", output, new_bank, *new_entries)

    # Every bank opens with a script prefix, and the way that script reaches a
    # message is a PC-RELATIVE signed 16-bit displacement:
    #     target = (address of the s16 operand) + s16
    # The operands live in the prefix, which does not move, but the FC 08
    # message headers they aim at DO move once Korean changes the text lengths.
    # Measured on retail, 7109 of 7160 operands land exactly on a header; after
    # repacking without this pass only 761 do, and 5922 land in the middle of a
    # message body.  Landing +2 late makes the renderer eat two body bytes as a
    # fake header and start mid-glyph, which is what turned 「그럼!!」 into
    # "お럼!!" on screen.
    #
    # Only operands whose ORIGINAL target was exactly a header are retargeted.
    # Anything else is a branch inside the script (or, for retail record 103,
    # already junk) and is left alone, so a mis-parsed opcode cannot do harm.
    JUMP_OPCODES = (0x07, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x14, 0x1E, 0x1F)

    def message_headers(image: bytes, start: int, end: int) -> list[int]:
        return [
            i - start
            for i in range(start, end - 3)
            if image[i] == 0xFC and image[i + 1] == 0x08
        ]

    # Opcode lengths taken from the handlers themselves:
    #   0x800DB388(FA) 0x800DB5FC(07) 0x800DB7D0(0E) 0x800DB860(0F)
    #   0x800DB8DC/94C/9B8(10/11/12) 0x800DBA2C(14) 0x800DBB0C(1E) 0x800DBB40(1F)
    FC_LENGTH = {
        0x00: 4, 0x01: 2, 0x02: 2, 0x03: 2, 0x04: 2, 0x07: 4, 0x08: 4,
        0x09: 2, 0x0A: 4, 0x0E: 5, 0x0F: 6, 0x10: 4, 0x11: 4, 0x12: 4,
        0x14: 6, 0x1E: 4, 0x1F: 4,
    }

    def jump_operands(block: bytes) -> list[int]:
        """Walk the bank script the way the interpreter does and collect the
        self-relative s16 operands.

        An earlier version scanned for 0xFA with 0xF6 in front of it.  The real
        handler at 0x800DB388 never looks at the preceding byte, and banks whose
        index entry points straight at an FA table have no 0xF6 there, so that
        scan missed 54 FA opcodes and left 208 jumps aimed into the middle of a
        Korean message.

        Both continuation edges must be followed.  Dropping either one loses
        sites the crude scan was already fixing (237 of them), so this walk has
        to be a strict superset:
          * an FA table is followed by the next group at p + 2 + 2*count
          * FC 1F stores a1+4 into win+0x55C (0x800DBB40) and the 0xFF at the end
            of the message returns there (0x800DB2CC)
        """
        size = len(block)
        if size < 2 * bank_index_entries:
            return []

        def displacement(offset: int) -> int:
            return struct.unpack_from("<h", block, offset)[0]

        entries = struct.unpack_from(f"<{bank_index_entries}H", block, 0)
        pending = [
            value + 2 * index
            for index, value in enumerate(entries)
            if index and 0 <= value + 2 * index < size
        ]
        visited: set[int] = set()
        sites: list[int] = []
        while pending:
            pc = pending.pop()
            while 0 <= pc < size - 1 and pc not in visited:
                visited.add(pc)
                opcode = block[pc]
                if opcode == 0xFF:
                    break
                if opcode == 0xFA:
                    count = block[pc + 1]
                    if not 1 <= count <= 16 or pc + 2 + 2 * count > size:
                        break
                    for step in range(count):
                        site = pc + 2 + 2 * step
                        sites.append(site)
                        pending.append(site + displacement(site))
                    pc += 2 + 2 * count
                    continue
                if opcode == 0xFC:
                    sub = block[pc + 1]
                    if sub in JUMP_OPCODES:
                        site = pc + 2
                        sites.append(site)
                        target = site + displacement(site)
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

    retargeted = 0
    inspected = 0
    skipped_banks = 0
    for position, old_bank in enumerate(banks):
        new_bank = relocate(old_bank)
        old_end = bank_bounds[position + 1]
        new_end = min(
            [value for value in new_pointers if value > new_bank] or [len(output)]
        )
        old_headers = message_headers(source, old_bank, old_end)
        new_headers = message_headers(bytes(output), new_bank, new_end)
        if len(old_headers) != len(new_headers):
            skipped_banks += 1
            continue
        header_map = dict(zip(old_headers, new_headers))
        block = source[old_bank:old_end]
        for site in jump_operands(block):
            inspected += 1
            old_displacement = struct.unpack_from("<h", block, site)[0]
            old_target = site + old_displacement
            new_target = header_map.get(old_target)
            if new_target is None:
                continue
            new_displacement = new_target - site
            if not -0x8000 <= new_displacement <= 0x7FFF:
                raise ValueError(
                    f"BTTMES bank {old_bank:#x} jump at {site:#x} exceeds s16: "
                    f"{new_displacement}"
                )
            if new_headers and site >= new_headers[0]:
                raise ValueError(
                    f"BTTMES bank {old_bank:#x} jump site {site:#x} lies inside the "
                    f"message area; refusing to overwrite Korean text"
                )
            if new_displacement != old_displacement:
                struct.pack_into("<h", output, new_bank + site, new_displacement)
                retargeted += 1

    landed = 0
    total_sites = 0
    mid_message = 0
    for position, old_bank in enumerate(banks):
        new_bank = relocate(old_bank)
        new_end = min(
            [value for value in new_pointers if value > new_bank] or [len(output)]
        )
        block = bytes(output[new_bank:new_end])
        headers = set(message_headers(bytes(output), new_bank, new_end))
        old_block = source[old_bank:bank_bounds[position + 1]]
        for site in jump_operands(old_block):
            if site + 2 > len(block):
                continue
            total_sites += 1
            target = site + struct.unpack_from("<h", block, site)[0]
            if target in headers:
                landed += 1
            elif headers and min(headers) <= target < len(block):
                mid_message += 1

    report = {
        "pointer_count": pointer_count,
        "record_count": len(records),
        "translated_record_count": sum(bool(row["korean"]) for row in changes),
        "old_bttmes_bytes": len(source),
        "new_bttmes_bytes": len(output),
        "bttmes_growth_bytes": len(output) - len(source),
        "shifted_pointer_count": sum(a != b for a, b in zip(pointers, new_pointers)),
        "bank_count": len(banks),
        "banks_with_rebuilt_index": rebuilt_banks,
        "rebuilt_bank_index_entries": rebuilt_bank_entries,
        "degenerate_banks_with_cross_bank_index": degenerate_banks,
        "relocated_cross_bank_index_entries": relocated_cross_bank_entries,
        "preserved_degenerate_index_entries": preserved_degenerate_entries,
        "script_jump_operands_inspected": inspected,
        "script_jump_operands_retargeted": retargeted,
        "script_jumps_landing_on_a_message": landed,
        "script_jumps_landing_mid_message": mid_message,
        "script_jump_sites_total": total_sites,
        "banks_skipped_message_count_mismatch": skipped_banks,
        "script_jump_audit": "mid-message landings must be 0",
    }
    if mid_message:
        raise ValueError(
            f"{mid_message} battle-script jumps land in the middle of a message; "
            f"retail has none.  The jump walk missed some operands."
        )
    return bytes(output), report, changes


def mirror_archive_tables(
    patched_exe: bytes,
    source_exe: bytes,
    source_scedata: bytes,
    patched_scedata: bytes,
    source_bttmes: bytes,
    patched_bttmes: bytes,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Rewrite the archive index tables that live inside the executable.

    SLPS_005.50 embeds byte-identical copies of SCEDATA.BIN's 128-entry member
    table and BTTMES.BIN's first 256 record offsets.  The scenario loader at
    0x800C6240 and the battle message loader at 0x800EC1C8 read those copies,
    never the ones in the files, so a repacked archive must update them here.
    """
    output = bytearray(patched_exe)
    report: list[dict[str, Any]] = []
    for name, offset, size, source_archive, patched_archive in (
        (
            "SCEDATA.BIN",
            EXE_SCEDATA_TABLE_OFFSET,
            EXE_SCEDATA_TABLE_BYTES,
            source_scedata,
            patched_scedata,
        ),
        (
            "BTTMES.BIN",
            EXE_BTTMES_TABLE_OFFSET,
            EXE_BTTMES_TABLE_BYTES,
            source_bttmes,
            patched_bttmes,
        ),
    ):
        end = offset + size
        if source_exe[offset:end] != source_archive[:size]:
            raise ValueError(
                f"{name} index mirror is not at executable offset 0x{offset:X}"
            )
        if bytes(output[offset:end]) != source_archive[:size]:
            raise ValueError(
                f"{name} index mirror was already overwritten by another patch"
            )
        output[offset:end] = patched_archive[:size]
        entries = size // 4
        changed = sum(
            struct.unpack_from("<I", source_archive, index * 4)[0]
            != struct.unpack_from("<I", patched_archive, index * 4)[0]
            for index in range(entries)
        )
        report.append({
            "archive": name,
            "exe_offset": offset,
            "exe_offset_hex": f"0x{offset:X}",
            "mirrored_bytes": size,
            "mirrored_entries": entries,
            "changed_entries": changed,
        })
    return bytes(output), report


def bounded_lz_decompress(source: bytes, position: int) -> bytes:
    """Decode exactly like the game, but refuse the reads it would not survive.

    The routine at 0x800DC570 has no bounds check at all: a back reference that
    points below the destination buffer copies whatever RAM happens to be there.
    Reject that here so a stale index table can never ship again.
    """
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

    def copy(displacement: int, length: int) -> None:
        cursor = len(output) + displacement
        if cursor < 0:
            raise ValueError(
                f"back reference reads {-cursor} bytes below the output buffer"
            )
        for _ in range(length):
            output.append(output[cursor])
            cursor += 1

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
            copy(displacement, length)
        else:
            code = (bit() << 1) | bit()
            copy(source[position] - 0x100, code + 2)
            position += 1
        if len(output) > SCEDATA_MEMBER_BUFFER_BYTES:
            raise ValueError(
                f"member decompresses past the {SCEDATA_MEMBER_BUFFER_BYTES}-byte buffer"
            )
    return bytes(output)


def verify_scedata_against_exe(patched_exe: bytes, patched_scedata: bytes) -> dict[str, Any]:
    """Load every member the way the game does and prove the result is sane."""
    table = patched_exe[
        EXE_SCEDATA_TABLE_OFFSET : EXE_SCEDATA_TABLE_OFFSET + EXE_SCEDATA_TABLE_BYTES
    ]
    offsets = list(struct.unpack(f"<{SCENARIO_INDEX_COUNT}I", table))
    if offsets != list(struct.unpack_from(f"<{SCENARIO_INDEX_COUNT}I", patched_scedata, 0)):
        raise ValueError("executable member table does not match the rebuilt SCEDATA")
    largest = 0
    for index, offset in enumerate(offsets):
        if not 0 < offset < len(patched_scedata):
            raise ValueError(f"member {index} offset {offset} is outside SCEDATA")
        try:
            member = bounded_lz_decompress(patched_scedata, offset)
        except (ValueError, IndexError) as error:
            raise ValueError(f"member {index} fails to decompress: {error}") from error
        largest = max(largest, len(member))
        for slot, target in enumerate(struct.unpack_from("<14I", member, 0)):
            if target >= len(member):
                raise ValueError(
                    f"member {index} header slot {slot} targets {target:#x}, "
                    f"past its {len(member)} decompressed bytes"
                )
    return {
        "members_verified": len(offsets),
        "largest_decompressed_bytes": largest,
        "scenario_buffer_bytes": SCEDATA_MEMBER_BUFFER_BYTES,
        "headroom_bytes": SCEDATA_MEMBER_BUFFER_BYTES - largest,
    }


def read_encoded_string(
    image: bytes, offset: int, limit: int | None = None
) -> bytes | None:
    """Return one terminated record, refusing to cross a data-structure bound.

    A number of executable tables reserve unused entries by pointing them at
    the padding immediately before the next table.  Without ``limit`` those
    entries look like a string and the parser consumes the next table, its
    stub, and whatever follows until the first incidental ``FF``.  Repacking
    that pseudo-record is a real executable corruption, not an empty label.
    Callers that know the arena or file boundary must pass it explicitly.
    """
    bound = len(image) if limit is None else min(len(image), limit)
    if not 0 <= offset < bound:
        return None
    cursor = offset
    while cursor < bound:
        byte = image[cursor]
        if byte == 0xFF:
            return image[offset:cursor + 1]
        if 0xF0 <= byte <= 0xF5:
            cursor += 2
        elif byte >= 0xF6:
            cursor += 1 + CONTROL_ARGS.get(byte, 0)
        else:
            cursor += 1
        if cursor - offset > 4096:
            return None
    return None


# Strings inside a scenario member that the record scanner never reaches: the
# mission objectives shown in the objective window.  They sit at the end of a
# structured record rather than being addressed as dialogue, so no table entry
# points at them and the pool rebuild cannot move them.  They are replaced where
# they lie, byte length preserved, which is safe precisely because nothing has to
# be re-pointed - and it is also the constraint: the Korean has to fit the
# Japanese byte count, and a Hangul syllable costs two bytes where kana costs one.
#
# The seven defeat messages in member 96 are the case that needs this.  Nothing
# addresses them, so the build left them alone - and because the Korean font pass
# reassigns the glyph slots their kanji used, they did not merely stay Japanese,
# they came out garbled.  In-place is the only option here, so the Korean is
# written to the byte budget: a Hangul syllable costs two bytes where kana costs
# one, which is why these read tighter than the originals.
#
# The mission objectives are NOT here.  They looked untranslated in the retail
# archive but the rebuilt members already carry 적 전멸, 데빌건담 파괴 and the rest.
UNREACHED_STRINGS = {
    "リュウセイ「なんだ,負けちまったのか?\n しょうがないな,まったく。\n 今度は,ガンバレよ」":
        "류세이「졌나?\n 어쩔 수 없지.\n 다음엔 힘내」",
    "ライ「残念だったね.....ん?\n まさか,もう止める気じゃないだろうね\n さあ,気を取りなおして」":
        "라이「아쉽군...응?\n 설마 그만둘 생각은 아니겠지\n 기운 내」",
    "アヤ「ガッカリしないで。こういうことも\n あるから,うまくいった時は\n うれしいのよ。さあ,ファイト!」":
        "아야「실망 마.\n 잘될 때도 있으니\n 자, 파이팅!」",
    "甲児「なんだ,辛気くさい顔をして。\n こんなことぐらい,どうってことないぜ\n ガッツ出して行こうぜ!」":
        "코우지「그런 얼굴 마.\n 이 정도 아무것도 아냐\n 힘내서 가자!」",
    "ジュンコ「負けた? しょうがないね。\n 嫌なことは早く忘れて,\n 次はガンバルんだよ。いいね」":
        "준코「졌어? 할 수 없지.\n 빨리 잊고\n 다음엔 힘내」",
    "ウッソ「えっ,負けてしまったんですか?\n 僕達は,ガンバッているんですよ。\n それなのに....」":
        "웃소「엣, 졌나요?\n 저흰 힘내는데요.\n 그런데도....」",
    "忍「なに,負けた?\n 誰のせいなんだよ!\n やってられねえぜ,まったく」":
        "시노부「뭐, 졌어?\n 누구 탓이야!\n 참 나」",
}


def patch_unreached_strings(
    member: bytearray, codec: Codec, mapping: dict[str, int], member_index: int
) -> list[dict[str, Any]]:
    """Replace the out-of-table strings in place, keeping every byte offset."""
    applied: list[dict[str, Any]] = []
    for japanese, korean in UNREACHED_STRINGS.items():
        source = encode_text(japanese, codec, mapping)
        target = encode_text(korean, codec, mapping)
        if len(target) > len(source):
            raise ValueError(
                "%r needs %d bytes but only %d are available in place"
                % (korean, len(target), len(source))
            )
        padded = target + bytes(len(source) - len(target))
        start = member.find(source)
        while start >= 0:
            member[start:start + len(source)] = padded
            applied.append({
                "member": member_index,
                "offset": start,
                "japanese": japanese,
                "korean": korean,
                "bytes": len(source),
            })
            start = member.find(source, start + len(source))
    return applied


def patch_terrain_panel_lengths(
    patched_exe: bytes, source_exe: bytes
) -> tuple[bytes, dict[str, Any]]:
    """Retarget the terrain panel's hardcoded ring length prefixes.

    See TERRAIN_PANEL_LENGTH_SITES.  The count the panel pushes has to equal the
    number of bytes it goes on to push, which is the record's content without its
    terminator.  Running the same measurement over the retail image has to come
    back with retail's own constant, so the model checks itself on every build.
    """
    output = bytearray(patched_exe)

    def content_length(image: bytes, index: int) -> int:
        slot = TERRAIN_MOD_TABLE + 4 * index
        target = slot + struct.unpack_from("<I", image, slot)[0]
        record = read_encoded_string(image, target, EXE_SYS_POOL_ARENA[1])
        if record is None:
            raise ValueError(
                "SYS entry %d of table %#x is not a readable record" % (index, TERRAIN_MOD_TABLE)
            )
        return len(record) - 1

    report: dict[str, Any] = {}
    for site, (indices, extra) in TERRAIN_PANEL_LENGTH_SITES.items():
        instruction = struct.unpack_from("<I", source_exe, site)[0]
        if instruction & 0xFFFF0000 != 0x34040000:
            raise ValueError("%#x is not `ori a0, zero, imm`" % site)

        retail = {extra + content_length(source_exe, index) for index in indices}
        if retail != {instruction & 0xFFFF}:
            raise ValueError(
                "terrain length model is wrong at %#x: retail measures %s but the "
                "executable pushes %d" % (site, sorted(retail), instruction & 0xFFFF)
            )

        needed = {extra + content_length(output, index) for index in indices}
        if len(needed) != 1:
            raise ValueError(
                "SYS table %#x entries %s must encode to the same length - one ring "
                "length byte serves them all - but they measure %s"
                % (TERRAIN_MOD_TABLE, ", ".join(str(i) for i in indices), sorted(needed))
            )
        value = needed.pop()
        if not 1 <= value <= 0x3F:
            raise ValueError("terrain ring length %d at %#x is out of range" % (value, site))
        struct.pack_into("<H", output, site, value)
        report["%#x" % site] = {"retail": instruction & 0xFFFF, "korean": value}

    if len(output) != len(patched_exe):
        raise ValueError("executable size changed while patching terrain lengths")
    return bytes(output), report


def patch_fixed_exe_labels(
    patched_exe: bytes,
    source_exe: bytes,
    labels: list[dict[str, Any]],
    codec: Codec,
    mapping: dict[str, int],
) -> tuple[bytes, dict[str, Any]]:
    """Overwrite fixed-width label fields inside the executable in place.

    Two groups need this rather than the arena packer.  The SYSMSG pool has no
    index at all - the code walks it counting group terminators - so every line
    must keep its exact byte length.  The WIN records interleave window geometry
    and layout control bytes with literal labels, and the region has only 245
    bytes of slack, so only the label runs may be touched.

    SYSMSG lines carry their 0xFF terminator inside the budget; WIN labels do
    not.  Short replacements are padded with 0x00, which is the game's space
    glyph, exactly as retail pads its own centred lines.
    """
    output = bytearray(patched_exe)
    applied = 0
    skipped: list[dict[str, Any]] = []
    mismatched: list[str] = []
    unmapped: list[dict[str, Any]] = []
    for label in labels:
        offset = int(label["offset"], 16)
        budget = int(label["budget"])
        korean = label.get("korean") or ""
        japanese = label.get("japanese") or ""
        terminated = label.get("terminated", False)
        original = source_exe[offset:offset + budget]
        rendered = Codec.rendered(codec.tokenize(original, stop_at_terminator=True))
        if "<" in rendered:
            # The retail label contains a glyph the font map cannot name, so a
            # Korean replacement would silently drop it.  Leave it alone.
            unmapped.append({
                "id": label.get("id", hex(offset)),
                "rendered": rendered,
                "korean": korean,
            })
            continue
        if rendered.rstrip(NEWLINE + " ") != japanese.rstrip(NEWLINE + " "):
            mismatched.append(label.get("id", hex(offset)))
            continue
        if not korean or korean == japanese:
            continue
        encoded = encode_fixed_label_text(korean, codec, mapping)
        if terminated:
            encoded += bytes((0xFF,))
        if len(encoded) > budget:
            skipped.append({
                "id": label.get("id", hex(offset)),
                "japanese": japanese,
                "korean": korean,
                "budget": budget,
                "needed": len(encoded),
            })
            continue
        padding = budget - len(encoded)
        if terminated and padding:
            # The terminator has to stay where retail put it.  The SYSMSG list is
            # walked terminator to terminator, so a spare byte left *after* one
            # opens a phantom entry that swallows the labels behind it: shortening
            # パイロット to 인물 put a stray 예/NO pair in the status submenu and
            # crashed the game when it was selected.  Pad in front of it instead -
            # 0x00 is the space glyph, so the label just gains trailing blanks.
            replacement = encoded[:-1] + bytes(padding) + encoded[-1:]
        else:
            replacement = encoded + bytes(padding)
        output[offset:offset + budget] = replacement
        applied += 1
    if mismatched:
        raise ValueError(
            "fixed label source mismatch at: " + ", ".join(mismatched[:8])
        )
    return bytes(output), {
        "labels_considered": len(labels),
        "labels_applied": applied,
        "labels_skipped_over_budget": len(skipped),
        "labels_skipped_unmapped_glyph": len(unmapped),
        "skipped": skipped[:60],
        "unmapped": unmapped,
    }


def encode_fixed_label_text(
    korean: str, codec: Codec, mapping: dict[str, int]
) -> bytes:
    """Encode fixed labels while preserving the retail button-cell width.

    ``아뇨`` is two Korean syllables (four wide-font bytes), but the retail
    ``いいえ`` SYSMSG field has only three data bytes before its terminator.
    The unused 0xED half-width cell supplies the first syllable, followed by
    the normal wide glyph for ``뇨``.  Visually this is the same 8+16 pixel
    advance as the original three half-width kana cells.
    """
    if korean == "아뇨":
        return bytes((SMALL_LABEL_AH_INDEX,)) + encode_text("뇨", codec, mapping)
    return encode_text(korean, codec, mapping)


def patch_enhancement_prompt_literals(
    patched_exe: bytes,
    source_exe: bytes,
    codec: Codec,
    mapping: dict[str, int],
) -> tuple[bytes, dict[str, Any]]:
    """Replace the two inline ``が,`` fragments in enhancement dialogs.

    These bytes sit between two numeric ``F8`` controls, so they are not part
    of any fixed-label record.  Each dialog has a following padding/terminator
    byte that lets the Korean connective grow in place without relocating the
    executable or changing any table address.
    """
    specs = (
        {
            "id": "EXE-WIN-084B00-enhancement",
            "offset": 0x84B29,
            "old": bytes.fromhex(
                "F0 18 F0 04 F0 42 00 F8 00 4B 3A F8 00 "
                "6A 69 89 7D 58 F6 87 8C 56 43 66 58 4A 14 F6 FE 00"
            ),
            "prefix": "공격력 ",
            "tail_after_old": bytes.fromhex("FF FF"),
        },
        {
            "id": "EXE-WIN-084EB0-enhancement",
            "offset": 0x84ECE,
            "old": bytes.fromhex(
                "F8 00 4B 3A F8 00 6A 69 89 7D 58 F6 "
                "87 8C 56 43 66 58 4A 14 F6 FE 00"
            ),
            "prefix": "",
            "tail_after_old": bytes.fromhex("FF 00"),
        },
    )
    output = bytearray(patched_exe)
    applied: list[dict[str, Any]] = []
    for spec in specs:
        offset = spec["offset"]
        old = spec["old"]
        if source_exe[offset:offset + len(old)] != old:
            raise ValueError(f"enhancement prompt source mismatch at 0x{offset:X}")
        old_tail_start = offset + len(old)
        expected_tail = spec["tail_after_old"]
        if source_exe[old_tail_start:old_tail_start + len(expected_tail)] != expected_tail:
            raise ValueError(f"enhancement prompt tail mismatch at 0x{old_tail_start:X}")
        replacement = (
            encode_text(spec["prefix"], codec, mapping)
            + bytes((0xF8, 0x00))
            + encode_text("에서 ", codec, mapping)
            + bytes((0xF8, 0x00))
            + encode_text("으로", codec, mapping)
            + bytes((0xF6,))
            + encode_text("할까요?", codec, mapping)
            + bytes((0xF6, 0xFE, 0x00))
        )
        if len(replacement) <= len(old):
            raise ValueError(f"enhancement replacement did not grow at 0x{offset:X}")
        terminator = offset + len(replacement)
        if terminator >= len(output):
            raise ValueError(f"enhancement replacement exceeds EXE at 0x{offset:X}")
        output[offset:terminator] = replacement
        output[terminator] = 0xFF
        applied.append({
            "id": spec["id"],
            "offset": f"0x{offset:X}",
            "old_bytes": len(old),
            "new_bytes_before_terminator": len(replacement),
            "terminator_offset": f"0x{terminator:X}",
            "connective": "에서 ",
        })
    return bytes(output), {"prompts_considered": len(specs), "prompts_applied": applied}


def patch_status_value_literals(
    patched_exe: bytes,
    source_exe: bytes,
    codec: Codec,
    mapping: dict[str, int],
) -> tuple[bytes, dict[str, Any]]:
    """Replace the inline one-cell status-value table used by unit panels.

    The status window does not obtain these two values from a string-pool
    table.  At 0x70300 it points directly at the two-cell ``無``/``有`` table,
    which is why a pool-only translation still showed Japanese in the shield
    and terrain fields.  Keep the table at exactly four payload bytes followed
    by its four retail padding bytes: the caller expects one 16-pixel glyph per
    value and the surrounding pointer/layout must not move.
    """
    offset = 0x70300
    old = bytes.fromhex("F0 61 F0 AA FF FF FF FF")
    if source_exe[offset:offset + len(old)] != old:
        raise ValueError("status value literal source mismatch at 0x70300")
    encoded = encode_text("없있", codec, mapping)
    if len(encoded) != 4:
        raise ValueError(
            "status value replacement must remain two wide glyphs (4 bytes), "
            f"got {len(encoded)}"
        )
    replacement = encoded + bytes.fromhex("FF FF FF FF")
    output = bytearray(patched_exe)
    output[offset:offset + len(replacement)] = replacement
    return bytes(output), {
        "offset": "0x70300",
        "retail_values": "無/有",
        "korean_values": "없/있",
        "payload_bytes": len(encoded),
        "field_bytes": len(replacement),
    }


def load_fixed_exe_labels(path: Path) -> list[dict[str, Any]]:
    """Load the fixed-width label translations, if the file is present."""
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    labels: list[dict[str, Any]] = []
    for identifier, entry in document.get("entries", {}).items():
        if "offset" not in entry or "budget" not in entry:
            continue
        labels.append({
            "id": identifier,
            "offset": entry["offset"],
            "budget": entry["budget"],
            "japanese": entry.get("japanese", ""),
            "korean": entry.get("korean", ""),
            "terminated": identifier.startswith("EXE-SYSMSG"),
        })
    return labels


# The two encyclopedias use the same self-relative u32 table format as the
# executable string pools: the target of entry i is (4 * i) + value.  Their data
# area fills the file exactly, with no slack, and neither file is mirrored
# anywhere in the executable, so rebuilding one only touches that file.  Keeping
# the rebuilt file at its retail size lets patch_fixed_extent write it in place
# and avoids the ISO relocation path that already cost this project one freeze.
DICTIONARY_FILES = {
    "PILOTDIC.BIN": {"entries": 384, "data_base": 0x600, "lba": 486},
    "ROBOTDIC.BIN": {"entries": 304, "data_base": 0x4C0, "lba": 504},
}

# The scenario-title screen is graphics.  MAP/SBTI0..8.DAT hold it as 16x16
# tiles plus a tilemap; build_ssrw_title_graphics redraws both in Korean and
# folds the slack into the last record, so each file keeps its retail byte
# count and can be written straight over its retail extent.
#
# The four words on the title screen itself are a separate texture: SBDATA.BIN
# member 38 is a 128x96 4bpp TIM.  Only that member is re-encoded, so the file
# keeps its retail size and its extent as well.
TITLE_MENU_FILE = {"name": "SBDATA.BIN", "lba": 515}

TITLE_GRAPHIC_FILES = {
    "SBTI0.DAT": 30798,
    "SBTI1.DAT": 30806,
    "SBTI2.DAT": 30814,
    "SBTI3.DAT": 30822,
    "SBTI4.DAT": 30830,
    "SBTI5.DAT": 30838,
    "SBTI6.DAT": 30846,
    "SBTI7.DAT": 30854,
    "SBTI8.DAT": 30862,
}


def rebuild_dictionary(
    source: bytes,
    translations: dict[str, str],
    spec: dict[str, int],
    codec: Codec,
    mapping: dict[str, int],
    label: str,
) -> tuple[bytes, dict[str, Any]]:
    """Rewrite one encyclopedia file with Korean text, keeping its retail size."""
    count = spec["entries"]
    data_base = spec["data_base"]
    output = bytearray(source)
    payloads: dict[bytes, None] = {}
    slot_payload: dict[int, bytes] = {}
    frozen: list[int] = []
    translated = 0

    for index in range(count):
        slot = 4 * index
        target = slot + struct.unpack_from("<I", source, slot)[0]
        raw = read_encoded_string(source, target, len(source)) if target >= data_base else None
        if raw is None or target + len(raw) > len(source):
            frozen.append(slot)
            continue
        japanese = Codec.rendered(codec.tokenize(raw, stop_at_terminator=True))
        korean = translations.get(japanese)
        if korean is None or korean == japanese:
            encoded = raw
        else:
            encoded = encode_text(korean, codec, mapping) + bytes((0xFF,))
            translated += 1
        payloads.setdefault(encoded, None)
        slot_payload[slot] = encoded

    ordered = sorted(payloads, key=lambda blob: (-len(blob), blob))
    total = sum(len(blob) for blob in ordered)
    capacity = len(source) - data_base
    if total > capacity:
        raise ValueError(
            f"{label}: Korean text needs {total} bytes but the data area holds "
            f"{capacity}; shorten it by {total - capacity} bytes"
        )

    placement: dict[bytes, int] = {}
    cursor = data_base
    for blob in ordered:
        placement[blob] = cursor
        output[cursor:cursor + len(blob)] = blob
        cursor += len(blob)
    for filler in range(cursor, len(source)):
        output[filler] = 0
    for slot, blob in slot_payload.items():
        struct.pack_into("<I", output, slot, placement[blob] - slot)
    for slot in frozen:
        struct.pack_into("<I", output, slot, struct.unpack_from("<I", source, slot)[0])

    if len(output) != len(source):
        raise ValueError(f"{label}: rebuilt file changed size")
    return bytes(output), {
        "file": label,
        "entries": count,
        "distinct_records": len(ordered),
        "translated_records": translated,
        "unused_slots": len(frozen),
        "data_area_bytes": capacity,
        "used_bytes": total,
        "free_bytes": capacity - total,
    }


def load_dictionary_translation(path: Path) -> dict[str, str]:
    """Load {japanese: korean} for one encyclopedia, if the file is present."""
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, str] = {}
    for entry in document.get("entries", {}).values():
        japanese = entry.get("japanese") or ""
        korean = entry.get("korean") or ""
        if not japanese or not korean or korean == japanese:
            continue
        if japanese in table and table[japanese] != korean:
            raise ValueError(f"{path.name}: one source record maps to two Korean texts")
        table[japanese] = korean
    return table


def wide_glyphs_in(raw: bytes) -> list[int]:
    """Wide glyph indices an encoded record emits."""
    indices: list[int] = []
    cursor = 0
    while cursor < len(raw):
        byte = raw[cursor]
        if byte == 0xFF:
            break
        if 0xF0 <= byte <= 0xF5:
            indices.append((byte - 0xF0) * 256 + raw[cursor + 1])
            cursor += 2
        elif byte >= 0xF6:
            cursor += 1 + CONTROL_ARGS.get(byte, 0)
        else:
            cursor += 1
    return indices


def reserved_glyph_slots(
    source_exe: bytes,
    remaining: dict[str, dict[str, str]],
    fixed_labels: list[dict[str, Any]],
    dictionaries: dict[str, dict[str, str]],
    codec: Codec,
) -> tuple[frozenset[int], dict[str, Any]]:
    """Wide glyph slots still emitted by whatever this build leaves in Japanese.

    Those slots must not be repainted with Hangul.  Note the encyclopedias alone
    account for over a thousand of them, so leaving them untranslated makes the
    reserve larger than the free space - finishing the translation is what makes
    the budget work, not restricting it.
    """
    reserve: set[int] = set(INLINE_UI_GLYPH_SLOTS)
    stats = {
        "pool_payloads": 0,
        "labels": 0,
        "dictionary_records": 0,
        "inline_ui_slots": sorted(INLINE_UI_GLYPH_SLOTS),
    }

    pools: list[tuple[str, list[tuple[int, int]], tuple[int, int]]] = [
        (label, [(spec["table"], spec["entries"])], spec["arena"])
        for label, spec in EXE_STRING_POOLS.items()
    ]
    pools.append(("SYS", list(EXE_SYS_POOL_TABLES), EXE_SYS_POOL_ARENA))
    pools.append((
        "DEMO",
        [(EXE_DEMO_POOL["table"], EXE_DEMO_POOL["entries"])],
        EXE_DEMO_POOL["arena"],
    ))
    for label, tables, (arena_start, arena_end) in pools:
        table_map = remaining.get(label, {})
        for table, count in tables:
            for index in range(count):
                slot = table + 4 * index
                target = slot + struct.unpack_from("<I", source_exe, slot)[0]
                raw = read_encoded_string(source_exe, target, arena_end)
                if raw is None or not arena_start <= target < arena_end:
                    continue
                japanese = Codec.rendered(codec.tokenize(raw, stop_at_terminator=True))
                if japanese in table_map:
                    continue
                stats["pool_payloads"] += 1
                reserve.update(wide_glyphs_in(raw))

    for label in fixed_labels:
        korean = label.get("korean") or ""
        japanese = label.get("japanese") or ""
        if korean and korean != japanese:
            continue
        offset = int(label["offset"], 16)
        stats["labels"] += 1
        reserve.update(wide_glyphs_in(source_exe[offset:offset + int(label["budget"])]))

    for name, spec in DICTIONARY_FILES.items():
        path = Path("extracted") / name
        if not path.exists():
            continue
        table_map = dictionaries.get(name, {})
        if not table_map:
            # Nothing translated for this file yet.  Reserving all 1054 of its
            # glyph slots would leave too few for Hangul and fail the build, and
            # the file renders as mojibake either way, so leave it out until its
            # translation lands.
            continue
        blob = path.read_bytes()
        seen: set[int] = set()
        for index in range(spec["entries"]):
            slot = 4 * index
            target = slot + struct.unpack_from("<I", blob, slot)[0]
            if target < spec["data_base"] or target in seen:
                continue
            seen.add(target)
            raw = read_encoded_string(blob, target, len(blob))
            if raw is None:
                continue
            japanese = Codec.rendered(codec.tokenize(raw, stop_at_terminator=True))
            if japanese in table_map:
                continue
            stats["dictionary_records"] += 1
            reserve.update(wide_glyphs_in(raw))

    stats["reserved_slots"] = len(reserve)
    stats["free_slots"] = 0x600 - len(reserve)
    return frozenset(reserve), stats


def load_remaining_translation(path: Path) -> dict[str, dict[str, str]]:
    """Load the Korean for the text outside SCEDATA/BTTMES.

    Returns {pool label: {japanese: korean}}.  Pool labels match
    EXE_STRING_POOLS plus "SYS".  A missing file means those pools keep their
    retail Japanese, which still builds.
    """
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    known = set(EXE_STRING_POOLS) | {"SYS", "DEMO"}
    pools: dict[str, dict[str, str]] = {}
    for label, entries in document.get("pools", {}).items():
        if label not in known:
            raise ValueError(f"unknown executable string pool: {label}")
        table: dict[str, str] = {}
        for japanese, korean in entries.items():
            if not korean or korean == japanese:
                continue
            if japanese in table and table[japanese] != korean:
                raise ValueError(
                    f"{label}: {japanese!r} maps to two different Korean strings"
                )
            table[japanese] = korean
        pools[label] = table
    return pools


def verify_remaining_translation(
    pools: dict[str, dict[str, str]], codec: Codec
) -> None:
    """Reject characters the game font cannot address before the build starts.

    The half-width table has no lowercase latin, so 'Far Away' is unencodable
    while 'FAR AWAY' is fine.  Hangul is exempt because it gets a fresh wide
    slot allocated for it.
    """
    encodable = set(codec.inverse)
    offenders: dict[str, tuple[str, str]] = {}
    for label, table in pools.items():
        for japanese, korean in table.items():
            for character in korean:
                if character == NEWLINE or "가" <= character <= "힣":
                    continue
                if character not in encodable and character not in offenders:
                    offenders[character] = (label, korean)
    if offenders:
        detail = ", ".join(
            f"{character!r} in {label} {korean!r}"
            for character, (label, korean) in list(offenders.items())[:8]
        )
        raise ValueError(f"remaining translation uses unencodable characters: {detail}")


def scan_past_terminator(image: bytes, position: int) -> int:
    """Reproduce the renderer's terminator scan, byte for byte.

    It stops at the first 0xFF whose preceding byte is not 0xF0..0xF5, because
    such a byte means the 0xFF is the second half of a wide glyph.  The
    heuristic is what makes a record ending on an ambiguous glyph run into the
    record behind it, so the check has to use the game's rule, not the
    token-aware one in read_encoded_string.
    """
    while True:
        while image[position] != 0xFF:
            position += 1
        position += 1
        if 0xF0 <= image[position - 2] <= 0xF5:
            continue
        return position


def verify_adjacent_tables(
    output: bytes,
    runs: dict[int, tuple[bytes, dict[int, int], dict[int, int]]],
    placement_run: dict[int, int],
    run_floor: dict[int, int],
) -> list[dict[str, Any]]:
    """Fail the build if a description's second line is a foreign string.

    Walking the renderer's own arithmetic, every entry has to land on one of
    exactly three things: the next table entry (the line is suppressed), an
    empty record (the line draws blank), or the continuation record retail
    welded behind that very entry.  Anything else is the bug this layout exists
    to prevent.
    """
    checked: list[dict[str, Any]] = []
    for table, (blob, offsets, continuations) in runs.items():
        base = placement_run[table]
        count = (run_floor[table] - table) // 4
        second_lines = 0
        for index in range(count):
            slot = table + 4 * index
            target = slot + struct.unpack_from("<I", output, slot)[0]
            reached = scan_past_terminator(output, target)
            following = slot + 4 + struct.unpack_from("<I", output, slot + 4)[0]
            if reached == following or output[reached] == 0xFF:
                continue
            if reached - base == continuations.get(target - base, -1):
                second_lines += 1
                continue
            raise ValueError(
                f"table {table:#x} entry {index} at {target:#x}: its second "
                f"line reads {reached:#x}, which is neither the next entry nor "
                f"its own continuation record"
            )
        checked.append({
            "table": f"{table:#x}",
            "entries": count,
            "placed_at": f"{base:#x}",
            "block_bytes": len(blob),
            "two_line_entries": second_lines,
        })
    return checked


def repack_exe_string_pools(
    patched_exe: bytes,
    source_exe: bytes,
    translations: dict[str, dict[str, str]],
    codec: Codec,
    mapping: dict[str, int],
) -> tuple[bytes, list[dict[str, Any]]]:
    """Repack the executable's self-relative string pools.

    Two rules are needed, because some of this text is read SEQUENTIALLY rather
    than through its table.  The description box on the spirit-command screen
    prints a second line by continuing past the first string's 0xFF terminator,
    so whatever sits next in memory becomes that line.  Retail depends on it:
    entry i of the description table at 0x704E0 is immediately followed by entry
    i+1, verified for every entry.  Packing those pools freely put an unrelated
    string there - "출격 전함 선택" landed behind 대근성's description - so the
    pools in ORDER_PRESERVING_POOLS now reproduce retail's layout exactly: same
    sequence, and a payload shared only where retail already shared an address.

    The label pools (unit / pilot / weapon names) are only ever reached through
    their table, so they keep the space-saving treatment: identical text stored
    once, placed in whichever arena has room.  Without that the Korean does not
    fit at all.
    """
    output = bytearray(patched_exe)
    pools: list[tuple[str, list[tuple[int, int]], tuple[int, int], bool]] = [
        (label, [(spec["table"], spec["entries"])], spec["arena"], True)
        for label, spec in EXE_STRING_POOLS.items()
    ]
    pools.append(("SYS", list(EXE_SYS_POOL_TABLES), EXE_SYS_POOL_ARENA, True))
    pools.append((
        "DEMO",
        [(EXE_DEMO_POOL["table"], EXE_DEMO_POOL["entries"])],
        EXE_DEMO_POOL["arena"],
        False,
    ))

    pilot_name_stats = {
        "translated_records": 0,
        "compact_glyphs": 0,
        "wide_fallback_hangul": 0,
        "max_display_advance": 0,
        "max_display_advance_entries": [],
    }

    def encode_for(
        raw: bytes, table_map: dict[str, str], pool_label: str
    ) -> tuple[bytes, bool]:
        japanese = Codec.rendered(codec.tokenize(raw, stop_at_terminator=True))
        korean = table_map.get(japanese)
        if korean is None or korean == japanese:
            return raw, False
        if pool_label == "PILOTNAME":
            encoded, compact_glyphs = encode_pilot_name_text(korean, codec, mapping)
            pilot_name_stats["translated_records"] += 1
            pilot_name_stats["compact_glyphs"] += compact_glyphs
            pilot_name_stats["wide_fallback_hangul"] += sum(
                1 for character in korean
                if "가" <= character <= "힣"
                and character not in SMALL_BATTLE_NAME_GLYPHS
            )
            advance = encoded_display_advance(encoded)
            if advance > pilot_name_stats["max_display_advance"]:
                pilot_name_stats["max_display_advance"] = advance
                pilot_name_stats["max_display_advance_entries"] = [japanese]
            elif advance == pilot_name_stats["max_display_advance"]:
                pilot_name_stats["max_display_advance_entries"].append(japanese)
            return encoded + bytes((0xFF,)), True
        return encode_text(korean, codec, mapping) + bytes((0xFF,)), True

    every_slot_target = {
        slot + struct.unpack_from("<I", source_exe, slot)[0]
        for table, count in (
            [(spec["table"], spec["entries"]) for spec in EXE_STRING_POOLS.values()]
            + list(EXE_SYS_POOL_TABLES)
            + [(EXE_DEMO_POOL["table"], EXE_DEMO_POOL["entries"])]
        )
        for slot in range(table, table + 4 * count, 4)
    }

    def build_adjacent_run(
        table: int, count: int, arena: tuple[int, int], table_map: dict[str, str],
        pool_label: str,
    ) -> tuple[bytes, dict[int, int], dict[int, int], int]:
        """Lay one adjacency-critical table out as a single contiguous block.

        Distinct records keep retail's order, and a record that no table entry
        names - retail's second description line, reachable only by reading past
        the terminator - is welded directly behind its owner.  A one byte 0xFF
        closes the block so the final entry finds an empty record rather than
        whatever the packer parked next to it.
        """
        arena_start, arena_end = arena
        blob = bytearray()
        offsets: dict[int, int] = {}
        continuations: dict[int, int] = {}   # owner offset -> continuation offset
        translated = 0
        for index in range(count):
            slot = table + 4 * index
            target = slot + struct.unpack_from("<I", source_exe, slot)[0]
            if target in offsets:
                continue
            raw = read_encoded_string(source_exe, target, arena_end)
            if raw is None or not arena_start <= target < arena_end:
                raise ValueError(
                    f"table {table:#x} entry {index} targets {target:#x}, "
                    f"outside its own arena - adjacency cannot be rebuilt"
                )
            offsets[target] = len(blob)
            encoded, did = encode_for(raw, table_map, pool_label)
            translated += did
            blob += encoded
            follow = target + len(raw)
            if follow in every_slot_target or not arena_start <= follow < arena_end:
                continue
            continuation = read_encoded_string(source_exe, follow, arena_end)
            if continuation is None:
                continue
            continuations[offsets[target]] = len(blob)
            encoded, did = encode_for(continuation, table_map, pool_label)
            translated += did
            blob += encoded
        blob.append(0xFF)
        return bytes(blob), offsets, continuations, translated

    reports: list[dict[str, Any]] = []
    free_payloads: dict[bytes, None] = {}
    free_slot_payload: dict[int, bytes] = {}
    free_arenas: list[tuple[int, int]] = []
    runs: dict[int, tuple[bytes, dict[int, int], dict[int, int]]] = {}
    run_floor: dict[int, int] = {}

    for label, tables, arena, require_stub in pools:
        arena_start, arena_end = arena
        table_map = translations.get(label, {})
        counters = {"entries": 0, "translated": 0, "empty": 0}
        slots: list[tuple[int, int]] = []
        for table, count in tables:
            stub = struct.unpack_from("<I", source_exe, table - 4)[0]
            if require_stub and stub != EXE_TEXT_BASE + table - 0x800:
                raise ValueError(f"{label}: table {table:#x} has no announcing stub")
            for index in range(count):
                slot = table + 4 * index
                target = slot + struct.unpack_from("<I", source_exe, slot)[0]
                counters["entries"] += 1
                slots.append((table, slot, target))

        if label in ORDER_PRESERVING_POOLS:
            payloads: dict[int, bytes] = {}

            def take(target: int) -> bytes | None:
                raw = read_encoded_string(source_exe, target, arena_end)
                if raw is None:
                    return None
                if target not in payloads:
                    encoded, did = encode_for(raw, table_map, label)
                    payloads[target] = encoded
                    counters["translated"] += did
                return raw

            addressed = {target for _table, _slot, target in slots
                         if arena_start <= target < arena_end}
            for target in addressed:
                take(target)
            # Retail also leaves records in here that no entry addresses - nine
            # of them in SYS - reached only by a window reading on past the
            # previous terminator.  Emitting just the addressed records drops
            # those and the read runs into the wrong text, which is what left
            # the battle map's terrain panel completely blank.  Only keep the
            # ones that actually chain back to an addressed record; anything
            # else in the arena is unreachable filler.
            walk: list[int] = []
            chained: set[int] = set()
            if label in CONTINUATION_POOLS:
                probe = arena_start
                while probe < arena_end:
                    raw = read_encoded_string(source_exe, probe, arena_end)
                    if raw is None:
                        break
                    walk.append(probe)
                    probe += len(raw)
                reachable = False
                for target in walk:
                    if target in addressed:
                        reachable = True
                        continue
                    if reachable:
                        chained.add(target)
                        take(target)
                for position, target in enumerate(walk):
                    if target in chained and position:
                        chained.add(walk[position - 1])
            # A record matters to its neighbour only when a window reads on from
            # it, which is exactly the records with no table entry of their own
            # and the records they sit behind.  Those keep their retail order and
            # their own copy; everything else may share an identical payload with
            # a record already written, which is what buys the room the Korean
            # needs - 193 bytes against the 103 it is over.
            # No sharing here: a window can read on from any record into the one
            # physically behind it, so every record keeps its own copy in retail's
            # order.  Sharing identical payloads bought room but silently moved
            # what follows a record, which put 근성 on the end of ???? in the
            # spirit-command box.
            placement: dict[int, int] = {}
            cursor = arena_start
            for target in sorted(payloads):
                blob = payloads[target]
                if cursor + len(blob) > arena_end:
                    raise ValueError(
                        f"{label}: retail-order layout runs past the arena at "
                        f"{target:#x}; shorten this pool's Korean by "
                        f"{cursor + len(blob) - arena_end} bytes"
                    )
                placement[target] = cursor
                output[cursor:cursor + len(blob)] = blob
                cursor += len(blob)
            total = cursor - arena_start
            # 0xFF, not 0x00: retail's last record ends flush with the arena, so
            # a window that reads on past a terminator stops there.  Zero is the
            # blank glyph, not a terminator, so zero filler would let such a read
            # run out of the arena and into the font bitmaps behind it.
            for filler in range(cursor, arena_end):
                output[filler] = 0xFF
            for _table, slot, target in slots:
                if target in placement:
                    struct.pack_into("<I", output, slot, placement[target] - slot)
                else:
                    counters["empty"] += 1
                    struct.pack_into(
                        "<I", output, slot,
                        struct.unpack_from("<I", source_exe, slot)[0],
                    )
            reports.append({
                "pool": label,
                "mode": "retail-order",
                "entries": counters["entries"],
                "translated_entries": counters["translated"],
                "empty_slot_entries": counters["empty"],
                "arena_bytes": arena_end - arena_start,
                "used_bytes": total,
                "free_bytes": arena_end - arena_start - total,
            })
            continue

        free_arenas.append(arena)
        for table, count in tables:
            if table not in ADJACENT_TABLES:
                continue
            blob, offsets, continuations, did = build_adjacent_run(
                table, count, arena, table_map, label
            )
            counters["translated"] += did
            runs[table] = (blob, offsets, continuations)
            run_floor[table] = table + 4 * count
        for table, slot, target in slots:
            if table in ADJACENT_TABLES:
                continue
            raw = read_encoded_string(source_exe, target, arena_end)
            if raw is None or not arena_start <= target < arena_end:
                # Unused table entries point into the padding immediately before
                # the next announcing stub.  That is not a terminated string;
                # preserving the pointer would let an out-of-range lookup read
                # the next table and its following data.  Route only this dead
                # slot to the shared one-byte empty record.  Live entries keep
                # their normal self-relative relocation and are never affected.
                counters["empty"] += 1
                empty = bytes((0xFF,))
                free_payloads.setdefault(empty, None)
                free_slot_payload[slot] = empty
                continue
            encoded, did = encode_for(raw, table_map, label)
            counters["translated"] += did
            free_payloads.setdefault(encoded, None)
            free_slot_payload[slot] = encoded
        reports.append({
            "pool": label,
            "mode": "shared",
            "entries": counters["entries"],
            "translated_entries": counters["translated"],
            "empty_slot_entries": counters["empty"],
            "adjacent_run_bytes": {
                f"{table:#x}": len(runs[table][0])
                for table, _count in tables if table in ADJACENT_TABLES
            },
        })

    arenas = sorted(set(free_arenas))
    highest_slot: dict[bytes, int] = {}
    for slot, blob in free_slot_payload.items():
        if blob not in highest_slot or slot > highest_slot[blob]:
            highest_slot[blob] = slot
    arena_starts = [arena[0] for arena in arenas]

    def first_usable_arena(blob: bytes) -> int:
        floor = highest_slot[blob] + 4
        for position, start in enumerate(arena_starts):
            if start >= floor or arenas[position][1] > floor:
                return position
        return len(arena_starts)

    ordered = sorted(
        free_payloads, key=lambda blob: (-first_usable_arena(blob), -len(blob), blob)
    )
    placement_free: dict[bytes, int] = {}
    cursors = {arena: arena[0] for arena in arenas}
    placement_run: dict[int, int] = {}
    for table in sorted(runs, key=lambda table: -len(runs[table][0])):
        blob = runs[table][0]
        floor = run_floor[table]
        for arena in arenas:
            position = max(cursors[arena], floor if arena[0] < floor else cursors[arena])
            if position >= floor and position + len(blob) <= arena[1]:
                placement_run[table] = position
                cursors[arena] = position + len(blob)
                break
        else:
            raise ValueError(
                f"table {table:#x} needs {len(blob)} contiguous bytes above "
                f"{floor:#x} and no arena has them; shorten its Korean"
            )
    for blob in ordered:
        floor = highest_slot[blob] + 4
        for arena in arenas:
            position = max(cursors[arena], floor if arena[0] < floor else cursors[arena])
            if position >= floor and position + len(blob) <= arena[1]:
                placement_free[blob] = position
                cursors[arena] = position + len(blob)
                break
        else:
            capacity = sum(end - start for start, end in arenas)
            needed = sum(len(item) for item in ordered)
            raise ValueError(
                f"label string pools need {needed} bytes but their arenas hold "
                f"{capacity}; shorten the Korean for the name and weapon pools"
            )
    for arena in arenas:
        for filler in range(cursors[arena], arena[1]):
            output[filler] = 0
    for blob, position in placement_free.items():
        output[position:position + len(blob)] = blob
    for slot, blob in free_slot_payload.items():
        struct.pack_into("<I", output, slot, placement_free[blob] - slot)
    for table, (blob, offsets, _continuations) in runs.items():
        base = placement_run[table]
        output[base:base + len(blob)] = blob
        count = (run_floor[table] - table) // 4
        for index in range(count):
            slot = table + 4 * index
            target = slot + struct.unpack_from("<I", source_exe, slot)[0]
            struct.pack_into("<I", output, slot, base + offsets[target] - slot)

    pilot_table = EXE_STRING_POOLS["PILOTNAME"]["table"]
    pilot_count = EXE_STRING_POOLS["PILOTNAME"]["entries"]
    pilot_arena_start, pilot_arena_end = EXE_STRING_POOLS["PILOTNAME"]["arena"]
    valid_pilot_entries = []
    for index in range(pilot_count):
        slot = pilot_table + 4 * index
        target = slot + struct.unpack_from("<I", source_exe, slot)[0]
        if (
            pilot_arena_start <= target < pilot_arena_end
            and read_encoded_string(source_exe, target, pilot_arena_end) is not None
        ):
            valid_pilot_entries.append(index)

    def output_string_limit(target: int) -> int | None:
        for start, end in arenas:
            if start <= target < end:
                return end
        return None

    pilot_widths: list[tuple[int, int]] = []
    pilot_name_failures: list[dict[str, Any]] = []
    for index in valid_pilot_entries:
        slot = pilot_table + 4 * index
        target = slot + struct.unpack_from("<I", output, slot)[0]
        limit = output_string_limit(target)
        raw = read_encoded_string(bytes(output), target, limit)
        if raw is None:
            pilot_name_failures.append({
                "entry": index,
                "reason": "unterminated_or_invalid_record",
                "target": f"0x{target:X}",
            })
            continue
        advance = encoded_display_advance(raw)
        pilot_widths.append((index, advance))
    if pilot_name_failures:
        raise ValueError(
            "PILOTNAME contains invalid rebuilt records: "
            + ", ".join(str(row["entry"]) for row in pilot_name_failures[:8])
        )
    max_pilot_advance = max((advance for _index, advance in pilot_widths), default=0)
    pilot_stats = dict(pilot_name_stats)
    pilot_stats.update({
        "compact_code_page": {
            character: f"0x{index:02X}"
            for character, index in sorted(
                SMALL_BATTLE_NAME_GLYPHS.items(), key=lambda item: item[1]
            )
        },
        "name_line_retail_advance_capacity": (
            BATTLE_NAME_LINE_END - BATTLE_NAME_LINE_START
        ),
        "max_rebuilt_entry_advance": max_pilot_advance,
        "max_rebuilt_entry_ids": [
            index for index, advance in pilot_widths if advance == max_pilot_advance
        ][:20],
        "valid_entry_count": len(valid_pilot_entries),
        "sanitized_invalid_entry_count": pilot_count - len(valid_pilot_entries),
        "entries_over_retail_battle_name_capacity": sum(
            advance > BATTLE_NAME_LINE_END - BATTLE_NAME_LINE_START
            for _index, advance in pilot_widths
        ),
    })
    reports.append({"pool": "PILOTNAME compact-name audit", **pilot_stats})

    adjacency_report = verify_adjacent_tables(output, runs, placement_run, run_floor)

    if len(output) != len(source_exe):
        raise ValueError("executable size changed while repacking string pools")

    reports.append({
        "pool": "<shared arenas>",
        "distinct_payloads": len(ordered),
        "adjacent_runs": adjacency_report,
        "adjacent_run_bytes": sum(len(blob) for blob, _o, _c in runs.values()),
        "arena_bytes": sum(end - start for start, end in arenas),
        "used_bytes": (
            sum(len(item) for item in ordered)
            + sum(len(blob) for blob, _o, _c in runs.values())
        ),
        "free_bytes": sum(arena[1] - cursors[arena] for arena in arenas),
    })
    return bytes(output), reports


def parse_directory(data: bytes) -> list[dict[str, Any]]:
    records = []
    position = 0
    while position < len(data):
        length = data[position]
        if length == 0:
            position = align(position + 1, USER_DATA_SIZE)
            continue
        record = data[position:position + length]
        if len(record) < 34:
            break
        name_length = record[32]
        name = record[33:33 + name_length].decode("ascii", "replace")
        records.append({
            "offset": position,
            "length": length,
            "name": name,
            "extent": struct.unpack_from("<I", record, 2)[0],
            "size": struct.unpack_from("<I", record, 10)[0],
        })
        position += length
    return records


def set_both_endian_u32(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", blob, offset, value)
    struct.pack_into(">I", blob, offset + 4, value)


def bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def make_mode2_form1_sector(lba: int, user_data: bytes) -> bytes:
    if len(user_data) > USER_DATA_SIZE:
        raise ValueError("sector payload too large")
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


def read_directory(track, extent: int, size: int) -> tuple[bytearray, list[dict[str, Any]]]:
    sector_count = math.ceil(size / USER_DATA_SIZE)
    data = bytearray()
    for index in range(sector_count):
        track.seek((extent + index) * RAW_SECTOR_SIZE)
        sector = track.read(RAW_SECTOR_SIZE)
        verify_mode2_form1(sector)
        data.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
    return data[:size], parse_directory(bytes(data[:size]))


def write_directory(track, extent: int, original: bytes, updated: bytes) -> None:
    sector_count = math.ceil(len(original) / USER_DATA_SIZE)
    for index in range(sector_count):
        start = index * USER_DATA_SIZE
        end = min(start + USER_DATA_SIZE, len(original))
        track.seek((extent + index) * RAW_SECTOR_SIZE)
        sector = bytearray(track.read(RAW_SECTOR_SIZE))
        if sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - start] != original[start:end]:
            raise ValueError(f"directory source mismatch at LBA {extent + index}")
        sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - start] = updated[start:end]
        rebuild_mode2_form1(sector)
        track.seek((extent + index) * RAW_SECTOR_SIZE)
        track.write(sector)


def patch_iso(
    output_track: Path,
    source_track: Path,
    source_exe: bytes,
    patched_exe: bytes,
    source_scedata: bytes,
    patched_scedata: bytes,
    source_bttmes: bytes,
    patched_bttmes: bytes,
    dictionaries: dict[str, tuple[bytes, bytes]] | None = None,
    title_graphics: dict[str, tuple[bytes, bytes]] | None = None,
    title_menu: tuple[bytes, bytes] | None = None,
) -> dict[str, Any]:
    old_track_sectors = source_track.stat().st_size // RAW_SECTOR_SIZE
    old_scedata_sectors = math.ceil(len(source_scedata) / USER_DATA_SIZE)
    if len(patched_scedata) > len(source_scedata):
        raise ValueError(
            "translated SCEDATA no longer fits its retail extent; "
            "the game does not reliably load a relocated SCEDATA archive"
        )
    stored_scedata = patched_scedata + bytes(len(source_scedata) - len(patched_scedata))
    bttmes_sectors = math.ceil(len(patched_bttmes) / USER_DATA_SIZE)
    new_bttmes_lba = old_track_sectors
    added_sectors = bttmes_sectors + BTTMES_READAHEAD_GUARD_SECTORS
    new_track_sectors = old_track_sectors + added_sectors

    with output_track.open("r+b") as track:
        # The executable size is unchanged, so it can be safely patched in place.
        changed_exe = patch_fixed_extent(track, EXE_LBA, source_exe, patched_exe)

        # The encyclopedias keep their retail size too, so they are written in
        # place as well - no directory record, no PVD, no relocation.
        changed_dictionaries: dict[str, int] = {}
        for name, (retail, rebuilt) in (dictionaries or {}).items():
            changed_dictionaries[name] = patch_fixed_extent(
                track, DICTIONARY_FILES[name]["lba"], retail, rebuilt
            )

        # Same story for the scenario-title graphics and the title-screen menu.
        changed_titles: dict[str, int] = {}
        for name, (retail, rebuilt) in (title_graphics or {}).items():
            changed_titles[name] = patch_fixed_extent(
                track, TITLE_GRAPHIC_FILES[name], retail, rebuilt
            )
        changed_title_menu = 0
        if title_menu is not None:
            changed_title_menu = patch_fixed_extent(
                track, TITLE_MENU_FILE["lba"], title_menu[0], title_menu[1]
            )

        track.seek(PVD_LBA * RAW_SECTOR_SIZE)
        pvd_sector = bytearray(track.read(RAW_SECTOR_SIZE))
        pvd = bytearray(pvd_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
        if pvd[:7] != b"\x01CD001\x01":
            raise ValueError("primary volume descriptor signature mismatch")
        old_volume_sectors = struct.unpack_from("<I", pvd, 80)[0]
        set_both_endian_u32(pvd, 80, old_volume_sectors + added_sectors)
        pvd_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE] = pvd
        rebuild_mode2_form1(pvd_sector)
        track.seek(PVD_LBA * RAW_SECTOR_SIZE)
        track.write(pvd_sector)

        track.seek(ROOT_DIRECTORY_LBA * RAW_SECTOR_SIZE)
        root_sector = bytearray(track.read(RAW_SECTOR_SIZE))
        root_data = bytearray(root_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
        root_records = parse_directory(bytes(root_data))
        root_by_name = {row["name"]: row for row in root_records}
        for required in ("SCEDATA.BIN;1", "NULL.DA;1"):
            if required not in root_by_name:
                raise ValueError(f"missing root directory record: {required}")
        btt_root_name = "BTT" if "BTT" in root_by_name else "BTT;1"
        if btt_root_name not in root_by_name:
            raise ValueError("missing root BTT directory record")
        sce_record = root_by_name["SCEDATA.BIN;1"]
        null_record = root_by_name["NULL.DA;1"]
        btt_dir_record = root_by_name[btt_root_name]
        old_scedata_lba = sce_record["extent"]
        old_btt_dir_lba = btt_dir_record["extent"]
        old_btt_dir_size = btt_dir_record["size"]
        old_null_lba = null_record["extent"]
        if old_scedata_lba + old_scedata_sectors > old_track_sectors:
            raise ValueError("retail SCEDATA extent lies outside track 1")
        changed_scedata = patch_fixed_extent(
            track, old_scedata_lba, source_scedata, stored_scedata
        )
        # Keep both the retail LBA and directory size.  The startup loader uses
        # the retail SCEDATA placement while the archive's own pointer table
        # determines the translated payload's true end.
        set_both_endian_u32(root_data, null_record["offset"] + 2, new_track_sectors + AUDIO_PREGAP_SECTORS)
        root_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE] = root_data
        rebuild_mode2_form1(root_sector)
        track.seek(ROOT_DIRECTORY_LBA * RAW_SECTOR_SIZE)
        track.write(root_sector)

        btt_data, btt_records = read_directory(track, old_btt_dir_lba, old_btt_dir_size)
        btt_by_name = {row["name"]: row for row in btt_records}
        if "BTTMES.BIN;1" not in btt_by_name:
            raise ValueError("BTTMES.BIN directory record not found")
        bttmes_record = btt_by_name["BTTMES.BIN;1"]
        old_bttmes_lba = bttmes_record["extent"]
        old_bttmes_size = bttmes_record["size"]
        updated_btt_data = bytearray(btt_data)
        set_both_endian_u32(updated_btt_data, bttmes_record["offset"] + 2, new_bttmes_lba)
        set_both_endian_u32(updated_btt_data, bttmes_record["offset"] + 10, len(patched_bttmes))
        write_directory(track, old_btt_dir_lba, bytes(btt_data), bytes(updated_btt_data))

        track.seek(0, 2)
        for index in range(bttmes_sectors):
            start = index * USER_DATA_SIZE
            user_data = patched_bttmes[start:start + USER_DATA_SIZE]
            track.write(make_mode2_form1_sector(new_bttmes_lba + index, user_data))
        for index in range(BTTMES_READAHEAD_GUARD_SECTORS):
            track.write(
                make_mode2_form1_sector(
                    new_bttmes_lba + bttmes_sectors + index, bytes(USER_DATA_SIZE)
                )
            )

    if output_track.stat().st_size != source_track.stat().st_size + added_sectors * RAW_SECTOR_SIZE:
        raise ValueError("full translation track growth mismatch")
    with output_track.open("rb") as track:
        verify_fixed_extent(track, EXE_LBA, patched_exe)
        track.seek(PVD_LBA * RAW_SECTOR_SIZE)
        verify_mode2_form1(track.read(RAW_SECTOR_SIZE))
        track.seek(ROOT_DIRECTORY_LBA * RAW_SECTOR_SIZE)
        root_sector = track.read(RAW_SECTOR_SIZE)
        verify_mode2_form1(root_sector)
        root = root_sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE]
        root_records = {row["name"]: row for row in parse_directory(root)}
        if root_records["SCEDATA.BIN;1"]["extent"] != old_scedata_lba:
            raise ValueError("SCEDATA extent verification failed")
        if root_records["SCEDATA.BIN;1"]["size"] != len(source_scedata):
            raise ValueError("SCEDATA size verification failed")
        if root_records["NULL.DA;1"]["extent"] != new_track_sectors + AUDIO_PREGAP_SECTORS:
            raise ValueError("NULL.DA relocation verification failed")
        btt_data, btt_records = read_directory(track, old_btt_dir_lba, old_btt_dir_size)
        btt_record = {row["name"]: row for row in btt_records}["BTTMES.BIN;1"]
        if btt_record["extent"] != new_bttmes_lba or btt_record["size"] != len(patched_bttmes):
            raise ValueError("BTTMES directory verification failed")
        for name, (_, rebuilt) in (dictionaries or {}).items():
            verify_fixed_extent(track, DICTIONARY_FILES[name]["lba"], rebuilt)
        for name, (_, rebuilt) in (title_graphics or {}).items():
            verify_fixed_extent(track, TITLE_GRAPHIC_FILES[name], rebuilt)
        if title_menu is not None:
            verify_fixed_extent(track, TITLE_MENU_FILE["lba"], title_menu[1])
        verify_fixed_extent(track, old_scedata_lba, stored_scedata)
        verify_appended_payload(track, new_bttmes_lba, patched_bttmes)

    return {
        "old_track_sectors": old_track_sectors,
        "new_track_sectors": new_track_sectors,
        "added_track_sectors": added_sectors,
        "old_track_bytes": source_track.stat().st_size,
        "new_track_bytes": output_track.stat().st_size,
        "track_growth_bytes": output_track.stat().st_size - source_track.stat().st_size,
        "old_volume_sectors": old_volume_sectors,
        "new_volume_sectors": old_volume_sectors + added_sectors,
        "old_scedata_lba": old_scedata_lba,
        "new_scedata_lba": old_scedata_lba,
        "old_scedata_size": len(source_scedata),
        "new_scedata_size": len(patched_scedata),
        "stored_scedata_size": len(stored_scedata),
        "scedata_storage": "retail extent (zero-padded after translated archive)",
        "old_bttmes_lba": old_bttmes_lba,
        "new_bttmes_lba": new_bttmes_lba,
        "old_bttmes_size": old_bttmes_size,
        "new_bttmes_size": len(patched_bttmes),
        "old_audio_pseudo_file_lba": old_null_lba,
        "new_audio_pseudo_file_lba": new_track_sectors + AUDIO_PREGAP_SECTORS,
        "appended_scedata_sectors": 0,
        "appended_bttmes_sectors": bttmes_sectors,
        "bttmes_readahead_guard_sectors": BTTMES_READAHEAD_GUARD_SECTORS,
        "patched_exe_sectors": changed_exe,
        "patched_dictionary_sectors": changed_dictionaries,
        "patched_title_graphic_sectors": changed_titles,
        "patched_title_menu_sectors": changed_title_menu,
        "patched_scedata_sectors": changed_scedata,
        "verified_appended_form1_sectors": added_sectors,
    }


def patch_fixed_extent(track, lba: int, source: bytes, patched: bytes) -> int:
    if len(source) != len(patched):
        raise ValueError("fixed ISO extent size changed")
    changed = 0
    for index in range(math.ceil(len(source) / USER_DATA_SIZE)):
        start = index * USER_DATA_SIZE
        end = min(start + USER_DATA_SIZE, len(source))
        track.seek((lba + index) * RAW_SECTOR_SIZE)
        sector = bytearray(track.read(RAW_SECTOR_SIZE))
        if sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - start] != source[start:end]:
            raise ValueError(f"fixed extent source mismatch at LBA {lba + index}")
        replacement = patched[start:end]
        if replacement != sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - start]:
            sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - start] = replacement
            rebuild_mode2_form1(sector)
            track.seek((lba + index) * RAW_SECTOR_SIZE)
            track.write(sector)
            changed += 1
    return changed


def verify_fixed_extent(track, lba: int, patched: bytes) -> None:
    for index in range(math.ceil(len(patched) / USER_DATA_SIZE)):
        start = index * USER_DATA_SIZE
        end = min(start + USER_DATA_SIZE, len(patched))
        track.seek((lba + index) * RAW_SECTOR_SIZE)
        sector = track.read(RAW_SECTOR_SIZE)
        if sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - start] != patched[start:end]:
            raise ValueError(f"fixed extent verification mismatch at LBA {lba + index}")
        verify_mode2_form1(sector)


def verify_appended_payload(track, lba: int, payload: bytes) -> None:
    extracted = bytearray()
    for index in range(math.ceil(len(payload) / USER_DATA_SIZE)):
        track.seek((lba + index) * RAW_SECTOR_SIZE)
        sector = track.read(RAW_SECTOR_SIZE)
        verify_mode2_form1(sector)
        extracted.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
    if bytes(extracted[:len(payload)]) != payload:
        raise ValueError(f"appended payload mismatch at LBA {lba}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, default=Path("Shin Super Robot Taisen (Track 1).bin"))
    parser.add_argument("--track2", type=Path, default=Path("Shin Super Robot Taisen (Track 2).bin"))
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument("--scedata", type=Path, default=Path("extracted/SCEDATA.BIN"))
    parser.add_argument("--bttmes", type=Path, default=Path("extracted/BTT/BTTMES.BIN"))
    parser.add_argument("--scenario-json", type=Path, default=Path("text_extracted/ssrw_scenario_dialogue.json"))
    parser.add_argument("--battle-json", type=Path, default=Path("text_extracted/ssrw_battle_dialogue.json"))
    parser.add_argument("--menu-json", type=Path, default=Path("text_extracted/ssrw_menu_text.json"))
    parser.add_argument("--translation", type=Path, default=Path("full_translation_ko.json"))
    parser.add_argument(
        "--opening-translation",
        type=Path,
        default=OPENING_TRANSLATION,
        help="tested fixed-slot translations for the first opening sequence",
    )
    parser.add_argument(
        "--scenario-title-translation",
        type=Path,
        default=Path("scenario_title_ko.json"),
        help="Korean text for the scenario-title screen, which is graphics rather than text",
    )
    parser.add_argument("--bdf", type=Path, default=_REPO / "font" / "Galmuri14.bdf")
    parser.add_argument("--output-dir", type=Path, default=Path("korean_translation_full"))
    parser.add_argument(
        "--remaining-translation",
        type=Path,
        default=Path("remaining_translation_ko.json"),
        help="Korean for the executable string pools and the encyclopedias",
    )
    parser.add_argument(
        "--fixed-label-translation",
        type=Path,
        default=Path("fixed_label_translation_ko.json"),
        help="Korean for the fixed-width SYSMSG and window-label fields",
    )
    parser.add_argument(
        "--speaker-fix",
        type=Path,
        default=Path("speaker_fix_ko.json"),
        help="scenario-1 lines with their full speaker names restored",
    )
    parser.add_argument(
        "--dictionary-translation-dir",
        type=Path,
        default=Path("."),
        help="directory holding pilotdic_ko.json / robotdic_ko.json",
    )
    parser.add_argument(
        "--preserve-scenario-zero",
        action="store_true",
        help="keep the original scenario-0 compressed members for boot isolation",
    )
    args = parser.parse_args()

    translation = load_translation(args.translation)
    scenario_doc = json.loads(args.scenario_json.read_text(encoding="utf-8"))
    battle_doc = json.loads(args.battle_json.read_text(encoding="utf-8"))
    menu_doc = json.loads(args.menu_json.read_text(encoding="utf-8"))
    mapping_doc = json.loads(Path("text_extracted/ssrw_japanese_font_mapping.json").read_text(encoding="utf-8"))
    codec = Codec(mapping_doc)

    opening_doc = json.loads(args.opening_translation.read_text(encoding="utf-8"))
    opening_translations = opening_doc.get("translations")
    if not isinstance(opening_translations, dict):
        raise ValueError("opening translation file has no translations object")
    scenario_translations = dict(translation["scenario"])
    scenario_translations.update(opening_translations)
    scenario_translations.update(SCENARIO_ONE_FIXED_OVERRIDES)
    speaker_fixes = load_speaker_fixes(args.speaker_fix)
    scenario_translations.update(speaker_fixes)

    remaining = load_remaining_translation(args.remaining_translation)
    verify_remaining_translation(remaining, codec)
    fixed_labels = load_fixed_exe_labels(args.fixed_label_translation)
    remaining_values = [
        korean
        for pool in remaining.values()
        for korean in pool.values()
    ] + [label["korean"] for label in fixed_labels if label["korean"]]
    dictionaries = {
        name: load_dictionary_translation(
            args.dictionary_translation_dir / f"{Path(name).stem.lower()}_ko.json"
        )
        for name in DICTIONARY_FILES
    }
    dictionary_values = [
        korean for table in dictionaries.values() for korean in table.values()
    ]
    all_values = (
        list(scenario_translations.values())
        + list(translation["battle"].values())
        + list(translation["menu"].values())
        + remaining_values
        + dictionary_values
        # These are emitted by the inline status enum below, not by a JSON
        # string pool, so include them in the glyph allocator explicitly.
        + ["없", "있"]
    )
    usage = count_wide_usage([args.scenario_json, args.battle_json, args.menu_json])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = args.output_dir / "hangul_mapping.json"
    source_exe = args.exe.read_bytes()
    reserved, reserve_report = reserved_glyph_slots(
        source_exe, remaining, fixed_labels, dictionaries, codec
    )
    mapping, mapping_report = build_full_mapping(
        mapping_path, all_values, usage, reserved
    )
    mapping_report["reserve"] = reserve_report
    encoded_report = encode_all(all_values, codec, mapping)

    source_scedata = args.scedata.read_bytes()
    source_bttmes = args.bttmes.read_bytes()
    patched_exe = patch_full_font(source_exe, args.bdf, mapping)
    patched_exe, small_label_font_report = patch_small_label_font(
        patched_exe, args.bdf
    )
    if len(patched_exe) != len(source_exe):
        raise ValueError("patched executable size changed")

    # Patch the 28 known menu anchors in-place inside the executable.
    patched_menu = bytearray(patched_exe)
    menu_applied = []
    menu_fallback_count = 0
    occupied: list[tuple[int, int]] = []
    rows = sorted(menu_doc["records"], key=lambda row: (-len(bytes.fromhex(row["raw_hex"])), row["source_offset"]))
    for row in rows:
        source = row["japanese"]
        if source not in translation["menu"]:
            raise ValueError(f"menu translation missing: {source}")
        start = int(row["source_offset"])
        original = bytes.fromhex(row["raw_hex"])
        end = start + len(original)
        if any(start < used_end and used_start < end for used_start, used_end in occupied):
            continue
        if source_exe[start:end] != original:
            raise ValueError(f"menu source mismatch at 0x{start:X}")
        korean_menu = translation["menu"][source]
        encoded = encode_text(korean_menu, codec, mapping)
        if len(encoded) > len(original):
            fallback = COMPACT_MENU_TRANSLATIONS.get(source)
            if fallback is None:
                raise ValueError(f"menu field grew beyond EXE allocation: {source}")
            fallback_encoded = encode_text(fallback, codec, mapping)
            if len(fallback_encoded) > len(original):
                raise ValueError(f"compact menu field still exceeds allocation: {source}")
            korean_menu = fallback
            encoded = fallback_encoded
            menu_fallback_count += 1
        patched_menu[start:end] = encoded + bytes(len(original) - len(encoded))
        occupied.append((start, end))
        menu_applied.append({
            "id": row["id"],
            "japanese": source,
            "korean": korean_menu,
            "field_bytes": len(original),
            "encoded_bytes": len(encoded),
        })
    patched_exe = bytes(patched_menu)

    patched_exe, exe_pool_reports = repack_exe_string_pools(
        patched_exe, source_exe, remaining, codec, mapping
    )
    patched_exe, fixed_label_report = patch_fixed_exe_labels(
        patched_exe, source_exe, fixed_labels, codec, mapping
    )
    # After the SYS repack: the sites are measured from the table entries, which
    # only point at the Korean records once the pool has been rebuilt.
    patched_exe, terrain_panel_report = patch_terrain_panel_lengths(
        patched_exe, source_exe
    )
    patched_exe, enhancement_prompt_report = patch_enhancement_prompt_literals(
        patched_exe, source_exe, codec, mapping
    )
    patched_exe, status_value_report = patch_status_value_literals(
        patched_exe, source_exe, codec, mapping
    )

    dictionary_images: dict[str, tuple[bytes, bytes]] = {}
    dictionary_reports = []
    for name, spec in DICTIONARY_FILES.items():
        table = dictionaries.get(name, {})
        if not table:
            continue
        retail = (Path("extracted") / name).read_bytes()
        rebuilt, dictionary_report = rebuild_dictionary(
            retail, table, spec, codec, mapping, name
        )
        dictionary_images[name] = (retail, rebuilt)
        dictionary_reports.append(dictionary_report)

    # The scenario-title screen never goes through the message encoder: it is a
    # tile sheet, so the Korean has to be drawn rather than encoded.
    title_images: dict[str, tuple[bytes, bytes]] = {}
    title_menu_image: tuple[bytes, bytes] | None = None
    title_report: dict[str, Any] = {}
    if args.scenario_title_translation.is_file():
        title_translation = title_graphics.load_translation(args.scenario_title_translation)
        rebuilt_titles, title_report = title_graphics.build_files(
            Path("extracted"),
            title_translation,
            load_encoder(),
            args.scenario_title_translation.with_name("scenario_title_render_cache.json"),
        )
        for name, rebuilt in rebuilt_titles.items():
            title_images[name] = ((Path("extracted") / "MAP" / name).read_bytes(), rebuilt)
        if title_translation.get("title_menu"):
            menu_retail = (Path("extracted") / TITLE_MENU_FILE["name"]).read_bytes()
            menu_rebuilt, menu_report = title_graphics.rebuild_sbdata(
                menu_retail,
                title_translation["title_menu"],
                parse_bdf(args.bdf),
                load_encoder(),
            )
            title_menu_image = (menu_retail, menu_rebuilt)
            title_report["title_menu"] = menu_report

    patched_scedata, scedata_report, scenario_applied = rebuild_scedata(
        source_scedata,
        scenario_doc,
        scenario_translations,
        codec,
        mapping,
        preserve_scenarios={0} if args.preserve_scenario_zero else None,
    )
    patched_bttmes, bttmes_report, battle_applied = rebuild_bttmes(
        source_bttmes, battle_doc, translation["battle"], codec, mapping
    )

    # The executable keeps its own copies of both index tables.  Without this
    # step every member after the first loads from a retail offset and the
    # scenario VM crashes on the first member switch after the opening.
    patched_exe, mirror_report = mirror_archive_tables(
        patched_exe,
        source_exe,
        source_scedata,
        patched_scedata,
        source_bttmes,
        patched_bttmes,
    )
    scedata_verification = verify_scedata_against_exe(patched_exe, patched_scedata)

    extracted_dir = args.output_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    (extracted_dir / args.exe.name).write_bytes(patched_exe)
    (extracted_dir / args.scedata.name).write_bytes(patched_scedata)
    (extracted_dir / "BTT" / args.bttmes.name).parent.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "BTT" / args.bttmes.name).write_bytes(patched_bttmes)
    for name, (_, rebuilt) in dictionary_images.items():
        (extracted_dir / name).write_bytes(rebuilt)
    if title_images:
        (extracted_dir / "MAP").mkdir(parents=True, exist_ok=True)
        for name, (_, rebuilt) in title_images.items():
            (extracted_dir / "MAP" / name).write_bytes(rebuilt)
    if title_menu_image is not None:
        (extracted_dir / TITLE_MENU_FILE["name"]).write_bytes(title_menu_image[1])
    (args.output_dir / "translation_applied.json").write_text(json.dumps({
        "reference": translation.get("reference"),
        "menu": menu_applied,
        "scenario": scenario_applied,
        "battle": battle_applied,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base = "Shin Super Robot Taisen Korean Full Translation"
    output_track = args.output_dir / f"{base} (Track 1).bin"
    shutil.copyfile(args.track, output_track)
    iso_report = patch_iso(
        output_track, args.track, source_exe, patched_exe,
        source_scedata, patched_scedata,
        source_bttmes, patched_bttmes,
        dictionary_images,
        title_images,
        title_menu_image,
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
        # The first 150 sectors in the retail audio file are the Track 2
        # pregap.  NULL.DA points at data-track-end + 150, so make that
        # boundary explicit for cores which do not infer it from the ISO.
        "    INDEX 00 00:00:00\n"
        "    INDEX 01 00:02:00\n",
        encoding="ascii",
    )

    report = {
        "build": "complete Korean scenario, battle, menu translation",
        "translation_source": str(args.translation.resolve()),
        "scenario_translation_record_count": len(translation["scenario"]),
        "battle_translation_record_count": len(translation["battle"]),
        "menu_translation_string_count": len(translation["menu"]),
        "menu_fixed_field_fallback_count": menu_fallback_count,
        "scenario_speaker_fixes": len(speaker_fixes),
        "exe_string_pools": exe_pool_reports,
        "exe_fixed_labels": fixed_label_report,
        "small_label_font": small_label_font_report,
        "enhancement_prompt_literals": enhancement_prompt_report,
        "status_value_literals": status_value_report,
        "terrain_panel_ring_lengths": terrain_panel_report,
        "encyclopedias": dictionary_reports,
        "scenario_title_graphics": title_report,
        "encoded_translation": encoded_report,
        "font_mapping": mapping_report,
        "scedata": scedata_report,
        "bttmes": bttmes_report,
        "exe_index_mirrors": mirror_report,
        "scedata_runtime_verification": scedata_verification,
        "iso": iso_report,
        "cue_track2_pregap_sectors": AUDIO_PREGAP_SECTORS,
        "sha256": {
            output_track.name: sha256(output_track.read_bytes()),
            output_track2.name: sha256(output_track2.read_bytes()),
            cue.name: sha256(cue.read_bytes()),
            "patched_SCEDATA.BIN": sha256(patched_scedata),
            "patched_BTTMES.BIN": sha256(patched_bttmes),
            "patched_SLPS_005.50": sha256(patched_exe),
        },
    }
    (args.output_dir / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # PowerShell commonly exposes a cp949 stdout on Korean Windows.  The
    # report is already written as UTF-8 above; keep console output from
    # turning a completed build into a non-zero exit when it contains Japanese.
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    print(report_text.encode("ascii", "backslashreplace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
