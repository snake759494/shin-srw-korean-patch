"""Step 1 of 2: turn a retail disc plus this repository into a build workspace.

    py -3.14 setup_workspace.py --disc "...\\Shin Super Robot Taisen (Track 1).bin"

What it does, in order:

  1. verifies the disc is the one this patch was built against (size + SHA-256),
  2. pulls the five files the build reads out of the disc image into work/extracted,
     checking each against data/extracted_manifest.tsv,
  3. copies the repository's translation data into the layout the build expects,
  4. re-extracts the Japanese script from the disc into work/text_extracted.

Step 3 is what keeps game data out of the repository: the Japanese script is 45 MB
of the publisher's text, so it is regenerated here rather than committed.
"""
from __future__ import annotations

import argparse
import csv

import shutil
import subprocess
import sys
from pathlib import Path

import ssrw_paths as P


def die(message: str) -> "NoReturn":  # noqa: F821
    print("error: " + message, file=sys.stderr)
    raise SystemExit(1)


def check_disc(path: Path, expected_size: int, expected_sha: str, expected_md5: str, label: str) -> None:
    size = path.stat().st_size
    print("  %s: %s" % (label, path))
    if size != expected_size:
        die(
            "%s is %d bytes, expected %d.\n"
            "       This patch is built against the Japanese release (SLPS-00550),\n"
            "       dumped as raw MODE2/2352 sectors.  A MODE1/2048 or re-encoded\n"
            "       dump will not match." % (label, size, expected_size)
        )
    print("    size ok (%d bytes), hashing ..." % size)
    actual = P.sha256(path)
    if actual != expected_sha:
        die(
            "%s SHA-256 mismatch.\n"
            "         expected %s\n"
            "         actual   %s\n"
            "       (MD5 of the expected dump is %s)" % (label, expected_sha, actual, expected_md5)
        )
    print("    sha256 ok")


def load_manifest() -> dict[str, tuple[int, int, str]]:
    rows: dict[str, tuple[int, int, str]] = {}
    with open(P.DATA / "extracted_manifest.tsv", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            name = (row.get("path") or "").strip().lstrip("/")
            if not name:
                continue
            try:
                rows[name] = (int(row["lba"]), int(row["size"]), (row.get("sha256") or "").strip())
            except (KeyError, ValueError):
                continue
    return rows


def extract(disc: Path, everything: bool) -> None:
    P.EXTRACTED.mkdir(parents=True, exist_ok=True)
    print("  extracting from the disc image ...")
    # A subprocess, not runpy: the extractor ends with `raise SystemExit(main())`,
    # and a SystemExit(0) travelling up through runpy would quietly end this script
    # too - which looked exactly like a successful run that skipped steps 3 and 4.
    result = subprocess.run(
        [
            sys.executable,
            str(P.TOOLS / "extract_psx_iso.py"),
            str(disc),
            str(P.EXTRACTED),
            "--manifest",
            str(P.WORK / "extracted_manifest.tsv"),
        ]
    )
    if result.returncode != 0:
        die("extract_psx_iso.py failed (exit %d)" % result.returncode)

    manifest = load_manifest()
    missing = []
    for name in P.NEEDED_FROM_DISC:
        target = P.EXTRACTED / name
        if not target.is_file():
            missing.append(name)
            continue
        expected = manifest.get(name)
        if not expected or not expected[2]:
            print("    %-16s extracted (no reference hash in the manifest)" % name)
            continue
        actual = P.sha256(target)
        if actual != expected[2]:
            die(
                "%s does not match data/extracted_manifest.tsv.\n"
                "         expected %s\n"
                "         actual   %s\n"
                "       The disc passed its own hash check, so this points at a\n"
                "       problem in extraction rather than at your dump." % (name, expected[2], actual)
            )
        print("    %-16s ok" % name)
    if missing:
        die("the disc did not yield: " + ", ".join(missing))

    if not everything:
        keep = {(P.EXTRACTED / name).resolve() for name in P.NEEDED_FROM_DISC}
        removed = 0
        for path in sorted(P.EXTRACTED.rglob("*"), reverse=True):
            if path.is_file() and path.resolve() not in keep:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        if removed:
            print("    dropped %d extracted files the build never opens (--all keeps them)" % removed)


def place_data() -> None:
    print("  placing the translation data ...")
    for source, destination in P.WORKSPACE_LAYOUT:
        src = P.REPO / source
        dst = P.WORK / destination
        if not src.is_file():
            die("missing repository file: %s" % source)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    print("    %d files in place" % len(P.WORKSPACE_LAYOUT))


def extract_text() -> None:
    print("  re-extracting the Japanese script from the disc ...")
    result = subprocess.run(
        [sys.executable, str(P.TOOLS / "extract_ssrw_japanese_text.py")],
        cwd=str(P.WORK),
    )
    if result.returncode != 0:
        die("extract_ssrw_japanese_text.py failed (exit %d)" % result.returncode)
    for name in ("ssrw_scenario_dialogue.json", "ssrw_battle_dialogue.json", "ssrw_menu_text.json"):
        if not (P.TEXT_EXTRACTED / name).is_file():
            die("text extraction did not produce %s" % name)
    print("    scenario, battle and menu text written to work/text_extracted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disc", help="retail Track 1 .bin (MODE2/2352)")
    parser.add_argument("--track2", help="retail Track 2 .bin (audio)")
    parser.add_argument("--all", action="store_true", help="keep every extracted file, not just the five the build reads")
    parser.add_argument("--skip-disc-check", action="store_true", help="skip the source hash check (for a known-good variant dump)")
    args = parser.parse_args()

    track1 = P.find_disc(args.disc, P.DISC_TRACK1)
    if track1 is None:
        die(
            "could not find the retail Track 1.\n"
            "       Pass it explicitly:  --disc \"path\\to\\%s\"" % P.DISC_TRACK1
        )
    track2 = P.find_disc(args.track2, P.DISC_TRACK2, search=[track1.parent, Path.cwd(), P.REPO])

    print("[1/4] verifying the source disc")
    if args.skip_disc_check:
        print("  skipped at your request")
    else:
        check_disc(track1, P.RETAIL_TRACK1_SIZE, P.RETAIL_TRACK1_SHA256, P.RETAIL_TRACK1_MD5, "Track 1")
        if track2 is not None:
            check_disc(track2, P.RETAIL_TRACK2_SIZE, P.RETAIL_TRACK2_SHA256, P.RETAIL_TRACK2_MD5, "Track 2")
        else:
            print("  Track 2 not found - the build needs it, pass --track2")

    P.WORK.mkdir(parents=True, exist_ok=True)
    print("[2/4] extracting the disc")
    extract(track1, args.all)
    print("[3/4] staging the translation data")
    place_data()
    print("[4/4] extracting the Japanese script")
    extract_text()

    print()
    print("workspace ready: %s" % P.WORK)
    print("next:  py -3.14 build_all.py --disc \"%s\"" % track1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
