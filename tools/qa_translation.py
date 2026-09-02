"""Static whole-script QA for the Korean translation build.

This deliberately does not start an emulator.  It compares the translation
coverage with the extracted source record set, rejects ordinary Japanese left
in Korean payloads, checks the reviewed regression phrases, and optionally
checks the inline status-value patch in a built executable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work" / "text_extracted" / "ssrw_japanese_text.json"
TRANSLATION = ROOT / "data" / "full_translation_ko.json"

EXPECTED_COUNTS = {"scenario": 8427, "battle": 5355, "menu": 28}
JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
SPECIAL_GLYPH = re.compile(r"<FC:[0-9A-Fa-f]+>[\u3040-\u30ff]")

REGRESSION_PATTERNS = (
    "군규 위반",
    "싸우 기",
    "가 볼까",
    "주리라고 는",
    "눈앞 의",
    "대 중을",
    "가치관 으로",
    "일소하 기",
    "매복 을",
    "더스트 가",
    "재 료",
    "톰리앗",
    "리인호스",
    "아나하임",
    "순순한 착한",
    "또 하고 있군",
    "내 방해는 용납하지",
    "사악한 공간을 끊는다",
    "기첸가",
    "베첸",
    "겟타 첸지",
    "트로스D7",
    "톰리아트으로",
    "톰리아트을",
    "간이지",
    "대령님은, 바쁘시다.",
    "너희 상대는, 내가 하지",
    "핑거하트",
    "핑거핫트",
    "레우르라",
    "조로앗",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def source_maps(source: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    scenarios = {
        row["id"]: row["japanese"]
        for scenario in source["scenario"]["scenarios"]
        for row in scenario["records"]
        if row.get("japanese")
    }
    battles = {
        row["id"]: row["japanese"]
        for row in source["battle"]["records"]
        if row.get("japanese")
    }
    return scenarios, battles


def remove_allowed_markup(value: str) -> str:
    value = SPECIAL_GLYPH.sub("", value)
    value = re.sub(r"<FC:[0-9A-Fa-f]+>", "", value)
    # The original script uses this as a choice separator, not as a Japanese
    # character that will be drawn by the new font.
    return value.replace("・", "")


def korean_payloads() -> Iterable[tuple[str, str]]:
    full = read_json(TRANSLATION)
    for section in ("scenario", "battle", "menu"):
        for record_id, value in full[section].items():
            if isinstance(value, str):
                yield f"full_translation/{section}/{record_id}", value

    for filename, collection, field in (
        ("remaining_translation_ko.json", "pools", None),
        ("fixed_label_translation_ko.json", "entries", "korean"),
        ("pilotdic_ko.json", "entries", "korean"),
        ("robotdic_ko.json", "entries", "korean"),
    ):
        document = read_json(ROOT / "data" / filename)
        values = document[collection]
        if field is None:
            for pool, entries in values.items():
                for key, value in entries.items():
                    if isinstance(value, str):
                        yield f"{filename}/{pool}/{key}", value
        else:
            for key, entry in values.items():
                value = entry.get(field)
                if isinstance(value, str):
                    yield f"{filename}/{key}/{field}", value


def check_built_exe(path: Path) -> list[str]:
    errors: list[str] = []
    data = path.read_bytes()
    expected = bytes.fromhex("F0 61 F0 AA FF FF FF FF")
    offset = 0x70300
    actual = data[offset:offset + len(expected)]
    if actual == expected:
        errors.append("built EXE still contains the retail status values at 0x70300")
    # The exact bytes are allocated by the current Hangul mapping.  Keep the
    # check structural so a future glyph-table reorder does not hard-code an
    # incidental glyph index here.
    if len(actual) != 8 or actual[4:] != bytes.fromhex("FF FF FF FF"):
        errors.append("built EXE status field at 0x70300 lost its four-byte padding")
    if actual[:2] == expected[:2]:
        errors.append("built EXE status field did not replace 無")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--built-exe", type=Path, help="optional built SLPS_005.50")
    args = parser.parse_args()

    source = read_json(SOURCE)
    translation = read_json(TRANSLATION)
    scenario_source, battle_source = source_maps(source)
    actual_counts = {
        "scenario": len(translation["scenario"]),
        "battle": len(translation["battle"]),
        "menu": len(translation["menu"]),
    }
    errors: list[str] = []
    if actual_counts != EXPECTED_COUNTS:
        errors.append(f"translation counts {actual_counts} != {EXPECTED_COUNTS}")
    if set(translation["scenario"]) != set(scenario_source):
        errors.append("scenario translation IDs do not match the extracted source")
    if set(translation["battle"]) != set(battle_source):
        errors.append("battle translation IDs do not match the extracted source")
    if "가 주지" in translation["scenario"].get("SCE-046-0007", ""):
        errors.append("SCE-046-0007 still contains the reported spacing error")

    for location, value in korean_payloads():
        residual = JAPANESE.findall(remove_allowed_markup(value))
        if residual:
            errors.append(f"Japanese residue {''.join(residual)!r} at {location}")
        for pattern in REGRESSION_PATTERNS:
            if pattern in value:
                errors.append(f"regression phrase {pattern!r} at {location}")

    if args.built_exe:
        if not args.built_exe.is_file():
            errors.append(f"built EXE not found: {args.built_exe}")
        else:
            errors.extend(check_built_exe(args.built_exe))

    print(f"source scenario records: {len(scenario_source)}")
    print(f"source battle records:   {len(battle_source)}")
    print(f"translation counts:       {actual_counts}")
    print(f"Korean payloads checked:  {sum(1 for _ in korean_payloads())}")
    if errors:
        print("QA FAILED:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("QA PASS: source coverage, Japanese residue, regression phrases, and optional EXE checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
