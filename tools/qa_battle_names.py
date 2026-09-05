"""Check that no pilot name can hang or mis-render the battle dialogue window.

FC09 hands a PILOTNAME record to the battle window's own interpreter, so the
name is executed as script, not merely drawn.  Two properties have to hold and
neither was checked before:

  * No top-level byte may land in 0xEC..0xEF / 0xF8 / 0xFB / 0xFD.  The
    dispatcher at RAM 0x800DAFA4 reads 0xEC and up as opcodes, and the table at
    0x80086044 sends those seven to 0x800DB418 - `bnez s1, 0x800DAFA4` with the
    script pointer untouched.  That is an unbreakable spin: issues #136, #140
    and #143.

  * No single-byte glyph may denote a repainted half-width cell.  The battle
    window's layout record (table RAM 0x800FCBBC, record 0) carries the
    full-width flag, so the dispatcher draws a lone byte as wide glyph
    0x600|byte out of the companion bank at file 0x7F438, which this patch
    never translates.  A byte whose half-width cell was repainted Korean
    therefore comes out as the retail kana still sitting in the companion bank:
    0x95 as ウ and 0x49 as お, the "산ウお" of #137.  Latin and punctuation
    bytes are fine - retail names use them and the companion cell is faithful.

Both are read out of the finished image, not out of the source.
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ssrw_paths as P  # noqa: E402

RAW_SECTOR, USER_OFFSET, USER_SIZE = 2352, 24, 2048
EXE_LBA, EXE_SIZE = 24, 946_176
PILOT_TABLE, PILOT_ENTRIES = 0x6A938, 512
WIDE_FONT, SMALL_FONT = 0x73438, 0x72438
COMPANION_BANK = 0x7F438           # 0x73438 + 0x600 * 32
COMPANION_BYTES = 256 * 32
DEAD_OPCODES = {0xEC, 0xED, 0xEE, 0xEF, 0xF8, 0xFB, 0xFD}
CONTROL_ARGS = {0xF8: 1, 0xF9: 1, 0xFE: 1, 0xFB: 2, 0xFC: 2, 0xFD: 2}
WINDOW_COLUMNS = 64


def read_extent(track: Path, lba: int, size: int) -> bytes:
    out = bytearray()
    with io.open(track, "rb") as handle:
        for index in range((size + USER_SIZE - 1) // USER_SIZE):
            handle.seek((lba + index) * RAW_SECTOR + USER_OFFSET)
            out += handle.read(USER_SIZE)
    return bytes(out[:size])


def record(exe: bytes, entry: int) -> bytes | None:
    slot = PILOT_TABLE + entry * 4
    target = slot + struct.unpack_from("<i", exe, slot)[0]
    if not 0 <= target < len(exe):
        return None
    out = bytearray()
    at = target
    while at < len(exe) and len(out) < 128:
        byte = exe[at]
        if byte == 0xFF:
            return bytes(out)
        out.append(byte)
        if 0xF0 <= byte <= 0xF5:
            out.append(exe[at + 1])
            at += 2
            continue
        if byte >= 0xF6:
            for step in range(CONTROL_ARGS.get(byte, 0)):
                out.append(exe[at + 1 + step])
            at += 1 + CONTROL_ARGS.get(byte, 0)
            continue
        at += 1
    return None


def top_level(raw: bytes):
    """Yield the bytes the interpreter actually dispatches."""
    at = 0
    while at < len(raw):
        byte = raw[at]
        yield byte
        if 0xF0 <= byte <= 0xF5:
            at += 2
        elif byte >= 0xF6:
            at += 1 + CONTROL_ARGS.get(byte, 0)
        else:
            at += 1


def code_units(raw: bytes) -> int:
    return sum(1 for _ in top_level(raw))


def main() -> int:
    built = Path(sys.argv[1]) if len(sys.argv) > 1 else P.BUILD_OUTPUT / P.BUILD_TRACK1_NAME
    if not built.is_file():
        print("error: no built image at %s" % built, file=sys.stderr)
        return 1
    exe = read_extent(built, EXE_LBA, EXE_SIZE)
    retail = (P.EXTRACTED / "SLPS_005.50").read_bytes()

    repainted = {b for b in range(256)
                 if exe[SMALL_FONT + b * 16:SMALL_FONT + b * 16 + 16]
                 != retail[SMALL_FONT + b * 16:SMALL_FONT + b * 16 + 16]}

    hangs, kana, wide_line = [], [], []
    checked = translated = 0
    for entry in range(PILOT_ENTRIES):
        raw = record(exe, entry)
        if raw is None:
            continue
        checked += 1
        # a record the patch left alone still holds retail's half-width name and
        # renders exactly as retail did; only translated records are ours to hold
        # to this standard
        if raw == record(retail, entry):
            continue
        translated += 1
        for byte in top_level(raw):
            if byte in DEAD_OPCODES:
                hangs.append((entry, "%#04x" % byte, raw.hex(" ")))
                break
        for byte in top_level(raw):
            if byte < 0xF0 and byte in repainted:
                kana.append((entry, "%#04x" % byte, raw.hex(" ")))
                break
        if code_units(raw) * 3 > WINDOW_COLUMNS:
            wide_line.append((entry, code_units(raw)))

    companion_intact = (exe[COMPANION_BANK:COMPANION_BANK + COMPANION_BYTES]
                        == retail[COMPANION_BANK:COMPANION_BANK + COMPANION_BYTES])
    print("PILOTNAME records read from the image : %d (%d translated)" % (checked, translated))
    print("records that would hang the battle VM : %d" % len(hangs))
    for entry, byte, raw in hangs[:8]:
        print("    entry %3d  byte %s  %s" % (entry, byte, raw))
    print("names drawing a repainted half-width cell: %d" % len(kana))
    for entry, byte, raw in kana[:8]:
        print("    entry %3d  byte %s  %s" % (entry, byte, raw))
    print("records wider than the %d-column line  : %d" % (WINDOW_COLUMNS, len(wide_line)))
    for entry, units in wide_line[:8]:
        print("    entry %3d  %d code units" % (entry, units))
    print("companion full-width bank untouched   : %s" % companion_intact)
    print("half-width cells repainted            : %d %s"
          % (len(repainted), ["%#04x" % b for b in sorted(repainted)]))

    failed = bool(hangs or kana or wide_line) or not companion_intact
    print()
    print("QA %s: battle pilot names" % ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
