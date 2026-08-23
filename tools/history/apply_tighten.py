"""Build tighten_ko.json: the space-saving overrides for the two tightest members.

Members 58 and 65 are the branch endings of the V Gundam route.  Their scenario
buffers leave the least head-room of the whole game, so their lines get two
treatments, in this order:

  1. hand-written shorter wording for the worst offenders (tighten_src.json,
     keyed by record id so it is easy to review against the game),
  2. a cosmetic collapse of "....."-style pauses down to ".." for whatever is
     left in those two members.

The result is keyed by the Japanese source string, the same key the rest of the
retranslation uses, so merge_retrans.py can apply it with a single update().
"""
import io
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

TIGHT_MEMBERS = (58, 65)

base = json.load(io.open("retranslation_ko.json", encoding="utf-8"))
manual_by_id = json.load(io.open("tighten_src.json", encoding="utf-8"))
scenarios = json.load(
    io.open("text_extracted/ssrw_scenario_dialogue.json", encoding="utf-8")
)

japanese_of = {}
for member in scenarios["scenarios"]:
    for record in member["records"]:
        if record.get("japanese"):
            japanese_of[record["id"]] = record["japanese"]

out = {}
unresolved = []
for record_id, korean in manual_by_id.items():
    japanese = japanese_of.get(record_id)
    if japanese is None:
        unresolved.append(record_id)
        continue
    out[japanese] = korean

collapsed = 0
for member in scenarios["scenarios"]:
    if member["scenario_index"] not in TIGHT_MEMBERS:
        continue
    for record in member["records"]:
        japanese = record.get("japanese")
        if not japanese:
            continue
        korean = out.get(japanese, base.get(japanese))
        if not korean:
            continue
        trimmed = re.sub(r"\.{3,}", "..", korean)
        if trimmed != korean:
            out[japanese] = trimmed
            collapsed += 1

json.dump(out, io.open("tighten_ko.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(
    "tighten map: %d japanese keys (%d hand-written, %d ellipsis-collapsed, "
    "%d ids unresolved)" % (len(out), len(manual_by_id), collapsed, len(unresolved))
)
if unresolved:
    print(unresolved)
