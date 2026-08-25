"""Redraw the scenario-title screen in Korean.

The screen is graphics, not text: MAP/SBTI0..8.DAT are chains of
``[u32 offset-of-next-header][LZ stream]`` holding two groups of four records -

    28800  a 240x240 4bpp sheet cut into 16x16 tiles, 15 tiles per sheet row
     2048  CLUTs
     8192  a tilemap of 4096 u16 tile indices, 32 to a row
     2048  CLUTs

The first group is byte-identical in all nine files and carries the 제N화
prefixes; the second is per-file and carries that file's subtitle lines.  One
line of text is two tilemap rows - the upper halves of its characters and, two
rows further down, the lower halves - so a strip is 32 px tall and is sliced
into 16 px columns.

Measured off the retail sheets and reproduced here: ink on rows 7..24, about
20 px of advance per character, and an italic lean of one pixel left every
three rows down.  Colour index 0 is clear, 1 is white and 2..12 are the
anti-aliasing ramp, so glyph coverage maps straight onto an index.

Rendering needs a Korean font.  The result is cached next to the translation so
a machine without that font still builds the same image.
"""
from __future__ import annotations

import io
import json
import os
import struct
from pathlib import Path
from typing import Any

TILE = 16
PER_ROW = 15
SHEET = 240
SHEET_BYTES = SHEET * SHEET // 2
MAP_STRIDE = 32
MAP_ROWS = 128
MAX_TILE_INDEX = PER_ROW * (SHEET // TILE) - 1  # 224; index 0 is the blank tile

STRIP_HEIGHT = 32
INK_TOP = 7
INK_HEIGHT = 18
SHEAR_RUN = 3
ADVANCE = 20
MIN_ADVANCE = 8
RAMP_TOP = 1
RAMP_BOTTOM = 12
COVERAGE_FLOOR = 24
OVERSAMPLE = 4

# the retail subtitle lines all centre their ink on the same pixel, and the
# widest one ends exactly 18 tiles from the left edge
LINE_CENTRE = 144
LINE_TILES = 18

PREFIX_COUNT = 40  # 제1화 .. 제40화

FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\NotoSansKR-VF.ttf", "Bold"),
    (r"C:\Windows\Fonts\malgunbd.ttf", None),
    (r"C:\Windows\Fonts\gulim.ttc", None),
)

NARROW = ",.!?:;·'\"()[]"


# ---------------------------------------------------------------- file format


def records(data: bytes) -> list[tuple[int, int, bytes]]:
    """[(header_offset, size, decompressed)] by following the length chain."""
    from extract_ssrw_japanese_text import srw_lz_decompress

    out: list[tuple[int, int, bytes]] = []
    head = 0
    while head + 4 < len(data):
        size = struct.unpack_from("<I", data, head)[0]
        if size < 8 or head + size > len(data):
            break
        blob, _consumed = srw_lz_decompress(data[head + 4:head + size])
        out.append((head, size, blob))
        head += size
    return out


def assemble(blobs: list[bytes], encoder, total_size: int | None = None) -> bytes:
    """Pack blobs back into the length-chained container.

    With ``total_size`` the slack is folded into the last record's length, so
    the file ends where the retail one did and its ISO extent and directory
    entry can stay exactly as they are.
    """
    from extract_ssrw_japanese_text import srw_lz_decompress

    chunks: list[bytes] = []
    for blob in blobs:
        stream = encoder.compress(blob, level=8)
        round_trip, consumed = srw_lz_decompress(stream)
        if round_trip != blob or consumed != len(stream):
            raise ValueError("subtitle stream does not survive its own compressor")
        padding = (4 - ((len(stream) + 4) % 4)) % 4
        chunks.append(stream + bytes(padding))
    body = sum(4 + len(chunk) for chunk in chunks)
    if total_size is not None:
        if total_size < body:
            raise ValueError("rebuilt subtitle file is %d bytes over its retail size"
                             % (body - total_size))
        chunks[-1] = chunks[-1] + bytes(total_size - body)
    out = bytearray()
    for chunk in chunks:
        out += struct.pack("<I", 4 + len(chunk)) + chunk
    return bytes(out)


def sheet_rows(blob: bytes) -> list[list[int]]:
    rows = []
    for y in range(SHEET):
        base = y * (SHEET // 2)
        row: list[int] = []
        for i in range(SHEET // 2):
            v = blob[base + i]
            row.append(v & 15)
            row.append((v >> 4) & 15)
        rows.append(row)
    return rows


def pack_sheet(rows: list[list[int]]) -> bytes:
    out = bytearray(SHEET_BYTES)
    for y in range(SHEET):
        base = y * (SHEET // 2)
        row = rows[y]
        for i in range(SHEET // 2):
            out[base + i] = (row[i * 2] & 15) | ((row[i * 2 + 1] & 15) << 4)
    return bytes(out)


def tilemap(blob: bytes) -> list[list[int]]:
    values = struct.unpack("<%dH" % (len(blob) // 2), blob)
    return [list(values[r * MAP_STRIDE:(r + 1) * MAP_STRIDE]) for r in range(MAP_ROWS)]


def pack_tilemap(grid: list[list[int]]) -> bytes:
    flat: list[int] = []
    for row in grid:
        flat.extend(row)
    return struct.pack("<%dH" % len(flat), *flat)


def used_rows(grid: list[list[int]]) -> list[int]:
    return [r for r, row in enumerate(grid) if any(row)]


# -------------------------------------------------------------------- drawing


def _font(size: int):
    from PIL import ImageFont

    for path, variation in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        font = ImageFont.truetype(path, size)
        if variation:
            try:
                font.set_variation_by_name(variation)
            except Exception:
                pass
        return font
    return None


def _advance_of(ch: str, advance: int) -> int:
    if ch == " ":
        return max(6, advance // 2)
    if ch in NARROW:
        return max(8, advance * 3 // 5)
    return advance


def _draw(text: str, advance: int):
    """One line at a given pitch, each glyph condensed to fit its cell.

    Korean needs more characters than the Japanese it replaces, and the title
    screen clips to the pixels the Japanese occupied, so a line has to be able
    to tighten.  Narrowing the pitch alone would just overlap the glyphs;
    squeezing each one horizontally keeps the height - and so the weight the
    retail face has - while the line as a whole gets narrower.
    """
    from PIL import Image, ImageDraw

    font = _font(INK_HEIGHT * OVERSAMPLE)
    if font is None:
        raise RuntimeError("no Korean font available and no cached render")
    steps = [_advance_of(ch, advance) for ch in text]
    width = sum(steps) + advance
    big = Image.new("L", (width * OVERSAMPLE, STRIP_HEIGHT * OVERSAMPLE), 0)
    measure = ImageDraw.Draw(big)
    x = 0
    for ch, step in zip(text, steps):
        if ch != " ":
            box = measure.textbbox((0, 0), ch, font=font)
            cell = Image.new("L", (max(1, box[2] - box[0]), max(1, box[3] - box[1])), 0)
            ImageDraw.Draw(cell).text((-box[0], -box[1]), ch, font=font, fill=255)
            room = (step - 2) * OVERSAMPLE
            if cell.width > room > 0:
                cell = cell.resize((room, cell.height), Image.LANCZOS)
            ox = (step * OVERSAMPLE - cell.width) // 2
            oy = INK_TOP * OVERSAMPLE + (INK_HEIGHT * OVERSAMPLE - cell.height) // 2
            big.paste(cell, (x * OVERSAMPLE + ox, oy), cell)
        x += step
    return big.resize((width, STRIP_HEIGHT), Image.LANCZOS)


def _shear(image):
    from PIL import Image

    lean = (INK_TOP + INK_HEIGHT - 1) // SHEAR_RUN
    out = Image.new("L", (image.width + lean, STRIP_HEIGHT), 0)
    src, dst = image.load(), out.load()
    for y in range(STRIP_HEIGHT):
        shift = max(0, (INK_TOP + INK_HEIGHT - 1 - y) // SHEAR_RUN)
        for x in range(image.width):
            v = src[x, y]
            if v:
                dst[x + shift, y] = v
    return out


def _indices(image) -> list[list[int]]:
    src = image.load()
    rows = []
    for y in range(image.height):
        row = []
        for x in range(image.width):
            v = src[x, y]
            if v < COVERAGE_FLOOR:
                row.append(0)
            else:
                step = RAMP_TOP + round((255 - v) / 255 * (RAMP_BOTTOM - RAMP_TOP))
                row.append(min(RAMP_BOTTOM, max(RAMP_TOP, step)))
        rows.append(row)
    return rows


def _trim(rows: list[list[int]]) -> tuple[list[list[int]], int]:
    """Drop clear columns from both ends; return the strip and its left margin."""
    width = len(rows[0])
    lit = [x for x in range(width) if any(rows[y][x] for y in range(STRIP_HEIGHT))]
    if not lit:
        return [[0] for _ in range(STRIP_HEIGHT)], 0
    left, right = lit[0], lit[-1]
    return [row[left:right + 1] for row in rows], left


class Renderer:
    """Draws strips, remembering them so a fontless machine gets the same image."""

    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path
        self.cache: dict[str, Any] = {}
        self.hits = 0
        self.drawn = 0
        if cache_path and cache_path.is_file():
            self.cache = json.loads(cache_path.read_text(encoding="utf-8"))["strips"]

    def strip(self, text: str, advance: int = ADVANCE) -> list[list[int]]:
        key = "%d\u0000%s" % (advance, text)
        entry = self.cache.get(key)
        if entry is not None:
            self.hits += 1
            return [[int(c, 16) for c in row] for row in entry]
        rows, _left = _trim(_indices(_shear(_draw(text, advance))))
        self.cache[key] = ["".join("%X" % v for v in row) for row in rows]
        self.drawn += 1
        return rows

    def fitted(self, text: str, max_px: int) -> list[list[int]]:
        """The widest pitch that keeps the line inside the pixels it may use."""
        for advance in range(ADVANCE, MIN_ADVANCE - 1, -1):
            rows = self.strip(text, advance)
            if len(rows[0]) <= max_px:
                return rows
        raise ValueError("%r will not fit %d px even at the tightest pitch" % (text, max_px))

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.write_text(
            json.dumps({"format": "ssrw-title-strip-cache-1", "strips": self.cache},
                       ensure_ascii=False, indent=0, sort_keys=True),
            encoding="utf-8",
        )


# --------------------------------------------------------------------- packing


class SheetPacker:
    """Collects 16x16 tiles into one sheet, sharing repeats and blanks."""

    def __init__(self):
        self.rows = [[0] * SHEET for _ in range(SHEET)]
        self.index_of: dict[bytes, int] = {}
        self.next_index = 1

    def add(self, tile: list[list[int]]) -> int:
        key = bytes(v for row in tile for v in row)
        if not any(key):
            return 0
        existing = self.index_of.get(key)
        if existing is not None:
            return existing
        index = self.next_index
        if index > MAX_TILE_INDEX:
            raise ValueError("the subtitle sheet is full (%d tiles)" % MAX_TILE_INDEX)
        self.next_index += 1
        self.index_of[key] = index
        ox, oy = (index % PER_ROW) * TILE, (index // PER_ROW) * TILE
        for y in range(TILE):
            self.rows[oy + y][ox:ox + TILE] = tile[y]
        return index

    def place(self, grid, strip, top_row: int, slot: int) -> None:
        """Cut a 32-row strip into 16 px columns and write both tilemap rows."""
        width = len(strip[0])
        for column in range((width + TILE - 1) // TILE):
            x = column * TILE
            piece = [row[x:x + TILE] + [0] * (TILE - len(row[x:x + TILE])) for row in strip]
            grid[top_row][slot + column] = self.add(piece[:TILE])
            grid[top_row + 2][slot + column] = self.add(piece[TILE:])

    def sheet(self) -> bytes:
        return pack_sheet(self.rows)


def prefix_layout() -> list[tuple[int, int, int]]:
    """(number, tilemap top row, first slot) for 제1화 .. 제40화.

    The retail prefix group packs its lines the same way: numbers run in order,
    one-digit prefixes take five slots and two-digit ones take six, filling a
    row until the next would not fit.
    """
    out = []
    number, pair = 1, 0
    while number <= PREFIX_COUNT:
        slot = 0
        while number <= PREFIX_COUNT:
            length = 5 if number < 10 else 6
            if slot + length > MAP_STRIDE - 12:
                break
            out.append((number, pair * 4, slot))
            slot += length
            number += 1
        pair += 1
    return out


def ink_extents(sheet: bytes, tmap: bytes) -> list[tuple[int, int]]:
    """(first lit pixel, last lit pixel) for every line in a group.

    The title screen shows the line where the retail one was, so this is the
    budget the Korean has to live inside.
    """
    rows = sheet_rows(sheet)
    grid = tilemap(tmap)
    live = used_rows(grid)
    out = []
    for i in range(0, len(live) - 1, 2):
        lit = set()
        for row_index in (live[i], live[i + 1]):
            for slot, tile in enumerate(grid[row_index]):
                if tile == 0:
                    continue
                ox, oy = (tile % PER_ROW) * TILE, (tile // PER_ROW) * TILE
                for y in range(TILE):
                    line = rows[oy + y]
                    for x in range(TILE):
                        if line[ox + x]:
                            lit.add(slot * TILE + x)
        out.append((min(lit), max(lit)))
    return out


def build_group(renderer: Renderer, lines):
    """lines: (top_row, text, left, right) -> (sheet, tilemap, tiles used).

    ``left``/``right`` are the pixels the retail line covered; the Korean is
    fitted into them and centred on the same middle.
    """
    packer = SheetPacker()
    grid = [[0] * MAP_STRIDE for _ in range(MAP_ROWS)]
    for top_row, text, left, right in lines:
        strip = renderer.fitted(text, right - left + 1)
        width = len(strip[0])
        start = left + (right - left + 1 - width) // 2
        slot = start // TILE
        pad = start - slot * TILE
        strip = [[0] * pad + row for row in strip]
        if slot + (len(strip[0]) + TILE - 1) // TILE > MAP_STRIDE:
            raise ValueError("line %r does not fit the tilemap row" % text)
        packer.place(grid, strip, top_row, slot)
    return packer.sheet(), pack_tilemap(grid), packer.next_index - 1


# ----------------------------------------------------------------------- build


def load_translation(path: Path) -> dict[str, Any]:
    return json.loads(io.open(path, encoding="utf-8").read())


# ------------------------------------------------------- title-screen menu

# The four words on the title screen are a TIM inside SBDATA.BIN: member 38,
# 4bpp, 128x96, with four 16-colour CLUTs (index 7 is 0x0000, which the GPU
# draws as transparent, and CLUT 3 is the yellow used for the highlighted
# entry).  Each word is its own sprite, so the Korean has to stay inside the
# box the Japanese occupied: same rows, no wider.
MENU_MEMBER = 38
MENU_WIDTH = 128
MENU_HEIGHT = 96
MENU_CLEAR = 7
MENU_BOXES = [
    # (label, cell first row, cell last row, ink first column, ink last column)
    ("スタート", 0, 15, 0, 32),
    ("ロード", 16, 31, 2, 28),
    ("コンティニュー", 32, 47, 0, 55),
    ("オプション", 48, 63, 1, 46),
]
MENU_EDGE = 6


def bdf_word(font: dict, text: str) -> list[list[int]]:
    """One word as a 1-bit bitmap, trimmed to its ink.

    A pixel font is what this size wants: the retail katakana are 13 px tall
    and effectively one-bit, and an anti-aliased outline face just turns to
    mud when it is scaled down that far.
    """
    glyphs = []
    for ch in text:
        glyph = font.get(ord(ch))
        if glyph is None:
            raise ValueError("Galmuri has no glyph for %r" % ch)
        glyphs.append(glyph)
    height = max(g.height + g.y_offset for g in glyphs)
    # a glyph's bitmap row is wider than its advance, so leave room and trim
    width = sum(g.width for g in glyphs) + max(g.bitmap_bits for g in glyphs)
    grid = [[0] * width for _ in range(height)]
    pen = 0
    for glyph in glyphs:
        top = height - glyph.height - glyph.y_offset
        for y, bits in enumerate(glyph.bitmap):
            for x in range(glyph.bitmap_bits):
                if (bits >> (glyph.bitmap_bits - 1 - x)) & 1:
                    grid[top + y][pen + glyph.x_offset + x] = 1
        pen += glyph.width
    columns = [x for x in range(width) if any(grid[y][x] for y in range(height))]
    rows = [y for y in range(height) if any(grid[y])]
    return [[grid[y][x] for x in range(columns[0], columns[-1] + 1)]
            for y in range(rows[0], rows[-1] + 1)]


def draw_menu_word(pixels, bitmap: list[list[int]], top: int, bottom: int,
                   left: int, right: int) -> tuple[int, int]:
    """Clear the cell and centre the Korean word in the box the Japanese used.

    The retail face is brightest at the top and steps down the ramp as it
    descends, with index 6 tracing the outline, so rows pick the fill index and
    the neighbours of any lit pixel get the edge.
    """
    height, width = len(bitmap), len(bitmap[0])
    # the retail katakana are two pixels thick, so a one-pixel pixel-font stroke
    # reads as far too light next to them; widening each run by one to the right
    # matches the weight without closing up the counters
    width += 1
    if height > bottom - top + 1 or width > right - left + 1:
        raise ValueError("the Korean word is %dx%d, larger than its %dx%d box"
                         % (width, height, right - left + 1, bottom - top + 1))
    for y in range(top, bottom + 1):
        for x in range(min(right + 2, MENU_WIDTH)):
            pixels[y][x] = MENU_CLEAR
    ox = left + (right - left + 1 - width) // 2
    oy = top + (bottom - top + 1 - height) // 2
    for dy, row in enumerate(bitmap):
        shade = 1 if dy < height // 2 else 2
        for dx, on in enumerate(row):
            if not on:
                continue
            pixels[oy + dy][ox + dx] = shade
            # widen only into a gap that is at least two pixels, so the one-pixel
            # gaps that separate a jamo from its vowel stay open
            if ((dx + 1 >= len(row) or not row[dx + 1])
                    and (dx + 2 >= len(row) or not row[dx + 2])):
                pixels[oy + dy][ox + dx + 1] = shade
    return width, height


def menu_pixels(blob: bytes) -> list[list[int]]:
    start = menu_pixel_offset(blob)
    rows = []
    for y in range(MENU_HEIGHT):
        base = start + y * (MENU_WIDTH // 2)
        row = []
        for i in range(MENU_WIDTH // 2):
            v = blob[base + i]
            row.append(v & 15)
            row.append((v >> 4) & 15)
        rows.append(row)
    return rows


def menu_pixel_offset(blob: bytes) -> int:
    """Walk the TIM header to the pixel block's data."""
    magic, flags = struct.unpack_from("<II", blob, 0)
    if magic != 0x10:
        raise ValueError("SBDATA member %d is not a TIM" % MENU_MEMBER)
    at = 8
    if flags & 8:
        at += struct.unpack_from("<I", blob, at)[0]
    size, _x, _y, halfwords, rows = struct.unpack_from("<I4H", blob, at)
    if halfwords * 4 != MENU_WIDTH or rows != MENU_HEIGHT:
        raise ValueError("menu texture is %dx%d, expected %dx%d"
                         % (halfwords * 4, rows, MENU_WIDTH, MENU_HEIGHT))
    if size - 12 != MENU_WIDTH * MENU_HEIGHT // 2:
        raise ValueError("menu texture pixel block is the wrong size")
    return at + 12


def rebuild_menu(blob: bytes, words: list[str], font: dict) -> tuple[bytes, list[Any]]:
    if len(words) != len(MENU_BOXES):
        raise ValueError("the title menu has %d entries" % len(MENU_BOXES))
    pixels = menu_pixels(blob)
    report = []
    for (japanese, top, bottom, left, right), korean in zip(MENU_BOXES, words):
        drawn_w, drawn_h = draw_menu_word(pixels, bdf_word(font, korean), top, bottom, left, right)
        report.append({
            "japanese": japanese, "korean": korean,
            "box": [left, top, right - left + 1, bottom - top + 1],
            "drawn": [drawn_w, drawn_h],
        })
    start = menu_pixel_offset(blob)
    out = bytearray(blob)
    for y in range(MENU_HEIGHT):
        base = start + y * (MENU_WIDTH // 2)
        row = pixels[y]
        for i in range(MENU_WIDTH // 2):
            out[base + i] = (row[i * 2] & 15) | ((row[i * 2 + 1] & 15) << 4)
    return bytes(out), report


def rebuild_sbdata(retail: bytes, words: list[str], font: dict,
                   encoder) -> tuple[bytes, dict[str, Any]]:
    """Put the Korean menu back into SBDATA.BIN at its retail byte count."""
    from extract_ssrw_japanese_text import srw_lz_decompress

    count = struct.unpack_from("<I", retail, 0)[0] // 4
    offsets = list(struct.unpack_from("<%dI" % count, retail, 0))
    bounds = offsets + [len(retail)]
    blob, _consumed = srw_lz_decompress(retail[offsets[MENU_MEMBER]:bounds[MENU_MEMBER + 1]])
    patched, report = rebuild_menu(blob, words, font)
    stream = encoder.compress(patched, level=8)
    round_trip, consumed = srw_lz_decompress(stream)
    if round_trip != patched or consumed != len(stream):
        raise ValueError("menu texture does not survive its own compressor")

    # The executable carries its own copy of this offset table, so a member that
    # changes length moves every member after it out from under the table the
    # game actually reads.  Pad the re-encoded stream back to the retail length
    # instead: the decompressor stops at the end of the stream, the trailing
    # bytes are never looked at, and not one offset moves.
    members = [retail[bounds[i]:bounds[i + 1]] for i in range(count)]
    room = len(members[MENU_MEMBER])
    if len(stream) > room:
        raise ValueError("the Korean menu texture is %d bytes over its retail member"
                         % (len(stream) - room))
    members[MENU_MEMBER] = stream + bytes(room - len(stream))
    out = bytearray(struct.pack("<%dI" % count, *offsets))
    for member in members:
        out += member
    if len(out) != len(retail):
        raise ValueError("SBDATA changed size")
    rebuilt_offsets = list(struct.unpack_from("<%dI" % count, out, 0))
    if rebuilt_offsets != offsets:
        raise ValueError("SBDATA member offsets moved")
    return bytes(out), {
        "member": MENU_MEMBER,
        "member_bytes": room,
        "stream_bytes": len(stream),
        "padding_bytes": room - len(stream),
        "file_bytes": len(out),
        "offsets_unchanged": True,
        "entries": report,
    }


def build_files(extracted: Path, translation: dict[str, Any], encoder,
                cache_path: Path | None = None) -> tuple[dict[str, bytes], dict[str, Any]]:
    renderer = Renderer(cache_path)
    report: dict[str, Any] = {"files": {}, "prefix_count": PREFIX_COUNT}

    retail_prefix = (extracted / "MAP" / "SBTI0.DAT").read_bytes()
    prefix_blobs = [blob for _o, _s, blob in records(retail_prefix)]
    prefix_ink = ink_extents(prefix_blobs[0], prefix_blobs[2])
    prefix_lines = []
    for index, (number, top, slot) in enumerate(prefix_layout()):
        run = (5 if number < 10 else 6) * TILE
        prefix_lines.append((top, translation["prefix"] % number,
                             slot * TILE, slot * TILE + run - 1))
    prefix_sheet, prefix_map, prefix_tiles = build_group(renderer, prefix_lines)
    report["prefix_tiles"] = prefix_tiles
    report["prefix_lines"] = len(prefix_ink)

    out: dict[str, bytes] = {}
    for key in sorted(translation["titles"], key=int):
        name = "SBTI%s.DAT" % key
        retail = (extracted / "MAP" / name).read_bytes()
        blobs = [blob for _off, _size, blob in records(retail)]
        if len(blobs) != 8:
            raise ValueError("%s has %d records, expected 8" % (name, len(blobs)))
        rows = used_rows(tilemap(blobs[6]))
        pairs = [rows[i] for i in range(0, len(rows) - 1, 2)]
        extents = ink_extents(blobs[4], blobs[6])
        titles = translation["titles"][key]
        if len(pairs) != len(titles) or len(extents) != len(titles):
            raise ValueError(
                "%s has %d title lines but the translation lists %d"
                % (name, len(pairs), len(titles))
            )
        lines = [(top, korean, left, right)
                 for top, (_japanese, korean), (left, right) in zip(pairs, titles, extents)]
        sheet, grid, tiles = build_group(renderer, lines)
        blobs[0], blobs[2] = prefix_sheet, prefix_map
        blobs[4], blobs[6] = sheet, grid
        rebuilt = assemble(blobs, encoder, total_size=len(retail))
        if len(rebuilt) != len(retail):
            raise ValueError("%s changed size" % name)
        out[name] = rebuilt
        report["files"][name] = {
            "bytes": len(retail),
            "payload_bytes": len(assemble(blobs, encoder)),
            "title_lines": len(titles),
            "tiles_used": tiles,
        }
    renderer.save()
    report["strips_drawn"] = renderer.drawn
    report["strips_from_cache"] = renderer.hits
    return out, report
