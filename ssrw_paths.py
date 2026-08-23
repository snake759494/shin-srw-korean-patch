"""Where everything lives.

The build scripts in tools/ read most of their inputs as paths relative to the
current directory - `extracted/`, `text_extracted/`, `korean_patch/`, and the
translation JSONs at the top - because that is the layout they were written
against.  Rather than rewrite twenty path literals and risk changing the bytes
that came out of a verified build, the repository keeps that layout in a
scratch directory and runs the build with that directory as the CWD.

    <repo>/work/            the CWD the build sees
        extracted/          the five files the build reads off the disc
        text_extracted/     the Japanese script, re-extracted from the disc
        korean_patch/       hangul_mapping.json
        *.json              the translation data, copied out of <repo>/data
        korean_translation_full_fixed/   the build's output

Nothing under work/ is committed: it is either game data or a build product.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent
WORK = Path(os.environ.get("SSRW_WORK", REPO / "work"))
TOOLS = REPO / "tools"
DATA = REPO / "data"
FONT = REPO / "font" / "Galmuri14.bdf"
RELEASE = REPO / "release"

EXTRACTED = WORK / "extracted"
TEXT_EXTRACTED = WORK / "text_extracted"
BUILD_OUTPUT = WORK / "korean_translation_full_fixed"

# The retail disc, as dumped by the user.  Track 2 is audio and the patch does
# not touch it, but the build reads it so it can copy it beside the output.
DISC_TRACK1 = "Shin Super Robot Taisen (Track 1).bin"
DISC_TRACK2 = "Shin Super Robot Taisen (Track 2).bin"

RETAIL_TRACK1_SIZE = 650_139_840
RETAIL_TRACK1_SHA256 = "ef06dcf085fcccdc4617c2efed01f8b90d1dfd72f67e7cb325623eccb2514915"
RETAIL_TRACK1_MD5 = "7aee4811d8cac9f439cec85722f5dc42"
RETAIL_TRACK2_SIZE = 32_104_800
RETAIL_TRACK2_SHA256 = "2fbf5a94ffc8b475741529c4a95d580c937ca37db31db227e0d6c7a917a1e95f"
RETAIL_TRACK2_MD5 = "65aea234c174ee35fb574d981fe3fc4f"

# What a correct build produces.
PATCHED_TRACK1_SIZE = 650_607_888
PATCHED_TRACK1_SHA256 = "2eabf6f98bb9e5e664a3a121945ce4c29c52df41531b5bef635c688110cfc988"
PATCHED_TRACK1_MD5 = "30c00911909d7b8f014192b8003fb2f4"

BUILD_TRACK1_NAME = "Shin Super Robot Taisen Korean Full Translation (Track 1).bin"

# The five disc files the build actually opens.  Everything else on the disc -
# 540 MB of audio, movies and graphics - is copied through untouched, so there
# is no reason to extract it.
NEEDED_FROM_DISC = (
    "SLPS_005.50",
    "SCEDATA.BIN",
    "BTT/BTTMES.BIN",
    "PILOTDIC.BIN",
    "ROBOTDIC.BIN",
)

# Repository asset -> where the build expects to find it, relative to work/.
WORKSPACE_LAYOUT = (
    ("data/full_translation_ko.json", "full_translation_ko.json"),
    ("data/screenshot_translation_ko.json", "screenshot_translation_ko.json"),
    ("data/remaining_translation_ko.json", "remaining_translation_ko.json"),
    ("data/fixed_label_translation_ko.json", "fixed_label_translation_ko.json"),
    ("data/speaker_fix_ko.json", "speaker_fix_ko.json"),
    ("data/pilotdic_ko.json", "pilotdic_ko.json"),
    ("data/robotdic_ko.json", "robotdic_ko.json"),
    ("data/hangul_mapping.json", "korean_patch/hangul_mapping.json"),
    ("data/ssrw_japanese_font_mapping.json", "text_extracted/ssrw_japanese_font_mapping.json"),
)


def sha256(path: Path, chunk: int = 1 << 22) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def find_disc(explicit: str | None, name: str, *, search: list[Path] | None = None) -> Path | None:
    """Resolve a disc track: an explicit path wins, otherwise look around."""
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    for directory in search or [Path.cwd(), REPO, REPO.parent]:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None
