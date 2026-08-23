"""Re-lay-out Korean lines that overrun the message window.

The renderer measures a line the way the width helper at 0x800B7FA8 does: eight
pixels per glyph and four more for a two-byte one.  Hangul is always two bytes,
so a Korean line costs 12 px per syllable where the retail kana costs 8, and a
line that fitted in Japanese can run past the window.  The game then wraps by
itself and drops the overflowing word onto a row of its own, pushing the rest of
the record down a row.

This pass does the wrap properly instead: the word that does not fit moves down
and joins the next line, and if that line now overruns its own tail moves down in
turn, cascading to the end of the page.  Lines after the first keep the
single-space indent retail uses.

Budget per record: 216 px, the widest line retail itself writes in the ordinary
speaker window, or the record's own widest retail line where that is larger,
which is how the few wide narration and battle-cry windows keep their room.

Joining needs to know whether the original break fell between two words or inside
one - retail splits 来たん / です and the Korean followed it with 것뿐입 / 니다 -
so the junction is resolved against a vocabulary built from the whole
translation: if the two sides concatenate into a word the script uses elsewhere
they are glued, otherwise they are separated by a space.
"""
import io
import json
import re
import sys
from collections import Counter

sys.path.insert(0, ".")
from build_ssrw_screenshot_korean_test import Codec, encode_text

sys.stdout.reconfigure(encoding="utf-8")

BASE_BUDGET = 216
CONTROL_OPERANDS = {0xF8: 1, 0xF9: 1, 0xFE: 1, 0xFB: 2, 0xFC: 2, 0xFD: 2}
TOKEN = re.compile(r"<WAIT>|<[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]*)?>")
PUNCTUATION = ".,!?」「·:;~-()"
# Bare verb endings: a line can only open on one of these as the back half of a
# word split across the break (것뿐입 / 니다).
WORD_ENDINGS = frozenset({"니다", "습니다", "읍니다", "이옵니다", "옵니다", "니까", "습니까"})

font = json.load(io.open("text_extracted/ssrw_japanese_font_mapping.json", encoding="utf-8"))
codec = Codec(font)
hangul = {
    entry["character"]: entry["glyph_index"]
    for entry in json.load(
        io.open("korean_translation_full_fixed/hangul_mapping.json", encoding="utf-8")
    )["entries"]
}


def width(text, mapping=None):
    mapping = hangul if mapping is None else mapping
    encoded = encode_text(text, codec, mapping)
    total = 0
    position = 0
    while position < len(encoded):
        byte = encoded[position]
        if byte == 0xFF:
            break
        if 0xF0 <= byte <= 0xF5:
            total += 12
            position += 2
        elif byte in (0xF6, 0xF7, 0xFA):
            position += 1
        elif byte in CONTROL_OPERANDS:
            position += 1 + CONTROL_OPERANDS[byte]
        else:
            total += 8
            position += 1
    return total


def words(text):
    stripped = TOKEN.sub(" ", text)
    return [w.strip(PUNCTUATION) for w in re.split(r"[\s\n]+", stripped) if w.strip(PUNCTUATION)]


def build_vocabulary(document):
    counts = Counter()
    for kind in ("scenario", "battle"):
        for value in document[kind].values():
            counts.update(words(value))
    return counts


def separator(previous, following, vocabulary):
    """A space between two lines, unless they are two halves of one word.

    Two signals decide it.  If the two sides concatenate into something the
    script uses elsewhere as a word, they are one word cut in half.  Failing
    that, a following line that opens on a bare verb ending can only be the back
    half of the word above it, because nothing in Korean starts that way.
    Everything else gets a space.
    """
    tail = words(previous)
    head = words(following)
    if not tail or not head:
        return " "
    left, right = tail[-1], head[0]
    if vocabulary.get(left + right, 0) > 0:
        return ""
    if right in WORD_ENDINGS:
        return ""
    return " "


def split_point(line, budget):
    """Last space at which the head still fits, or None."""
    best = None
    for index, character in enumerate(line):
        if character != " " or index == 0:
            continue
        if line[:index].strip() and width(line[:index]) <= budget:
            best = index
    return best


def reflow(page, budget, vocabulary):
    lines = page.split("\n")
    joins = [separator(lines[i], lines[i + 1], vocabulary) for i in range(len(lines) - 1)]
    index = 0
    while index < len(lines):
        guard = 0
        while width(lines[index]) > budget and guard < 64:
            guard += 1
            cut = split_point(lines[index], budget)
            if cut is None:
                break
            head, tail = lines[index][:cut], lines[index][cut + 1:]
            lines[index] = head
            if index + 1 < len(lines):
                following = lines[index + 1]
                indent = " " if following.startswith(" ") else ""
                lines[index + 1] = indent + tail + joins[index] + following[len(indent):]
                joins[index] = " "
            else:
                lines.append(" " + tail)
                joins.append(" ")
        index += 1
    return "\n".join(lines)


def rewrap(text, budget, vocabulary):
    return "<WAIT>".join(reflow(page, budget, vocabulary) for page in text.split("<WAIT>"))


if __name__ == "__main__":
    scenarios = json.load(io.open("text_extracted/ssrw_scenario_dialogue.json", encoding="utf-8"))
    battles = json.load(io.open("text_extracted/ssrw_battle_dialogue.json", encoding="utf-8"))
    document = json.load(io.open("full_translation_ko.json", encoding="utf-8"))
    vocabulary = build_vocabulary(document)
    rows = [(r["id"], r.get("japanese"), "scenario")
            for m in scenarios["scenarios"] for r in m["records"]]
    rows += [(r["id"], r.get("japanese"), "battle") for r in battles["records"]]

    changed = added = 0
    leftover = []
    for record_id, japanese, kind in rows:
        korean = document[kind].get(record_id)
        if not japanese or not korean:
            continue
        budget = max(BASE_BUDGET, max(width(l, {}) for l in re.split(r"<WAIT>|\n", japanese)))
        if all(width(l) <= budget for l in re.split(r"<WAIT>|\n", korean)):
            continue
        result = rewrap(korean, budget, vocabulary)
        over = [w for w in (width(l) for l in re.split(r"<WAIT>|\n", result)) if w > budget]
        if over:
            leftover.append((record_id, budget, max(over)))
        added += result.count("\n") - korean.count("\n")
        document[kind][record_id] = result
        changed += 1

    if "--write" in sys.argv:
        json.dump(document, io.open("full_translation_ko.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=0)
    print("records relaid    : %d" % changed)
    print("lines added       : %d" % added)
    print("still over budget : %d  %s" % (len(leftover), leftover[:5]))
    print("written" if "--write" in sys.argv else "dry run (pass --write to apply)")
