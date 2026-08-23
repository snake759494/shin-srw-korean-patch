"""Fold the hand retranslation into full_translation_ko.json.

retranslation_ko.json holds one Korean line per unique Japanese source string;
tighten_ko.json overrides the handful of lines that have to be shorter for the
two cramped branch-ending members.  Both are keyed by the Japanese text, so a
record picks up its translation no matter which member it appears in.

No global reflow is applied: the archive gained its head-room by letting the 54
byte-identical copies of scenario 0 share one chunk, so the wording and the
line layout stay exactly as translated.  Only the two cramped branch-ending
members (58 / 65) carry the shorter variants in tighten_ko.json.
"""
import io
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

NEWLINE = chr(10)

translations = json.load(io.open("retranslation_ko.json", encoding="utf-8"))
try:
    translations.update(json.load(io.open("tighten_ko.json", encoding="utf-8")))
except FileNotFoundError:
    pass

document = json.load(io.open("full_translation_ko.json", encoding="utf-8"))
scenarios = json.load(
    io.open("text_extracted/ssrw_scenario_dialogue.json", encoding="utf-8")
)
battles = json.load(
    io.open("text_extracted/ssrw_battle_dialogue.json", encoding="utf-8")
)

hits = misses = 0
for member in scenarios["scenarios"]:
    for record in member["records"]:
        japanese = record.get("japanese")
        if not japanese:
            continue
        if japanese in translations:
            document["scenario"][record["id"]] = translations[japanese]
            hits += 1
        else:
            misses += 1
for record in battles["records"]:
    japanese = record.get("japanese")
    if not japanese:
        continue
    if japanese in translations:
        document["battle"][record["id"]] = translations[japanese]
        hits += 1
    else:
        misses += 1


document["translation_engine"] = "hand-retranslation (glossary: ssrw_glossary_ko.json)"
json.dump(
    document,
    io.open("full_translation_ko.json", "w", encoding="utf-8"),
    ensure_ascii=False,
    indent=0,
)
print("merged %d records, %d without a retranslation" % (hits, misses))
