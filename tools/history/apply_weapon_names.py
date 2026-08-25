"""Apply the weapon-name pass to remaining_translation_ko.json.

Two changes, in this order:

  1. corrections from the SRW-canon audit (weapon_fixes.json, keyed by the
     Japanese source string, same key the pool itself uses),
  2. removal of every space, because the weapon field on screen is narrow and
     the Japanese originals carry no spaces either.

Removing spaces can only make a name narrower, so it cannot introduce an overflow
that the shipping build did not already have.  Two pairs of entries collapse onto
the same Korean string once the spaces go - they are the same weapon spelled two
ways in the retail data ("マザ-テンクル"/"マザ-テンタクル") - and that is harmless
because the pool stores identical payloads once and points both entries at it.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path

POOL = "WEAPON"
TRANSLATION = Path("remaining_translation_ko.json")
BACKUP_SUFFIX = ".prewpn"

# A weapon takes its prefix from the machine that carries it, so four of the
# corrections only hold if the unit list agrees.  Namu.wiki's articles for
# Voltes V and Daiku-Maryu Gaiking give 볼트 팬저, 익룡 스카이라, 검룡 바조라, and
# the project glossary already fixes 보스보로트 - in each case the weapon list and
# the unit list currently disagree with each other.
UNIT_FIXES = {
    "ボルト.パンザ-": "볼트팬저",        # was 볼트판처, against 팬저암 / 팬저너클 / 팬저미사일
    "翼竜スカイラ-": "익룡스카이라",      # was 익룡스카일러, against 스카이라빔
    "剣竜バゾラ-": "검룡바조라",         # was 검룡바졸러, against the five 바조라 weapons
    "ス-パ-ボスボロット": "슈퍼보스보로트",  # was 슈퍼보스보롯, against 보로트펀치
    "バトルクラッシャ-": "배틀크러셔",     # was 배틀크래셔, against the four 크러셔 weapons
}


def width(text: str) -> int:
    """Rendered width in pixels: a Hangul syllable is a 12 px wide glyph, the rest 8 px."""
    return sum(12 if "가" <= character <= "힣" else 8 for character in text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixes", type=Path, help="JSON map of japanese -> corrected Korean")
    parser.add_argument("--write", action="store_true", help="write the changes (otherwise dry run)")
    args = parser.parse_args()

    document = json.load(io.open(TRANSLATION, encoding="utf-8"))
    pool = document["pools"][POOL]

    fixes: dict[str, str] = {}
    if args.fixes and args.fixes.is_file():
        fixes = json.load(io.open(args.fixes, encoding="utf-8"))

    applied, unknown, despaced = [], [], 0
    for japanese, corrected in fixes.items():
        if japanese not in pool:
            unknown.append(japanese)
            continue
        if pool[japanese] != corrected:
            applied.append((japanese, pool[japanese], corrected))
            pool[japanese] = corrected

    for japanese, korean in list(pool.items()):
        stripped = " ".join(korean.split()).replace(" ", "")
        if stripped != korean:
            pool[japanese] = stripped
            despaced += 1

    print("corrections applied : %d" % len(applied))
    for japanese, before, after in applied:
        print("   %-28r %r -> %r" % (japanese, before, after))
    if unknown:
        print("corrections whose Japanese key is not in the pool: %d" % len(unknown))
        for japanese in unknown:
            print("   %r" % japanese)
    print("entries despaced    : %d" % despaced)

    units = document["pools"]["UNITNAME"]
    unit_applied, unit_missing = [], []
    for japanese, corrected in UNIT_FIXES.items():
        if japanese not in units:
            unit_missing.append(japanese)
            continue
        if units[japanese] != corrected:
            unit_applied.append((japanese, units[japanese], corrected))
            units[japanese] = corrected
    print("unit names fixed    : %d" % len(unit_applied))
    for japanese, before, after in unit_applied:
        print("   %-24r %r -> %r" % (japanese, before, after))
    if unit_missing:
        print("   unit keys not found: %s" % ", ".join(repr(k) for k in unit_missing))

    widest = max(pool.values(), key=width)
    print("widest name after   : %d px  %r" % (width(widest), widest))
    print("entries still over 128 px (the widest retail name): %d"
          % sum(1 for value in pool.values() if width(value) > 128))
    leftover = [value for value in pool.values() if " " in value]
    print("entries still holding a space: %d" % len(leftover))

    if not args.write:
        print("\ndry run - pass --write to save")
        return 0

    shutil.copyfile(TRANSLATION, TRANSLATION.with_suffix(TRANSLATION.suffix + BACKUP_SUFFIX))
    json.dump(document, io.open(TRANSLATION, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nwrote %s (backup at %s%s)" % (TRANSLATION, TRANSLATION, BACKUP_SUFFIX))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
