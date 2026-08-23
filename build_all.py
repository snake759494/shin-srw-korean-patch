"""Step 2 of 2: build the patched disc image.

    py -3.14 build_all.py --disc "...\\Shin Super Robot Taisen (Track 1).bin"

Runs tools/build_ssrw_full_translation.py with work/ as the current directory,
then checks the result against the hash this repository was released with.  A
matching hash means your build is byte for byte the image the .xdelta release
produces.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import ssrw_paths as P


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disc", help="retail Track 1 .bin")
    parser.add_argument("--track2", help="retail Track 2 .bin")
    parser.add_argument("--output-dir", default="korean_translation_full_fixed", help="output directory, relative to work/")
    args = parser.parse_args()

    if not P.WORK.is_dir():
        print("error: no workspace - run setup_workspace.py first", file=sys.stderr)
        return 1

    track1 = P.find_disc(args.disc, P.DISC_TRACK1)
    if track1 is None:
        print("error: pass the retail Track 1 with --disc", file=sys.stderr)
        return 1
    track2 = P.find_disc(args.track2, P.DISC_TRACK2, search=[track1.parent, Path.cwd(), P.REPO])
    if track2 is None:
        print("error: pass the retail Track 2 with --track2", file=sys.stderr)
        return 1

    command = [
        sys.executable,
        str(P.TOOLS / "build_ssrw_full_translation.py"),
        "--track", str(track1),
        "--track2", str(track2),
        "--bdf", str(P.FONT),
        "--output-dir", args.output_dir,
    ]
    print("building (this takes a few minutes) ...")
    print("  cwd: %s" % P.WORK)
    result = subprocess.run(command, cwd=str(P.WORK))
    if result.returncode != 0:
        print("error: build failed (exit %d)" % result.returncode, file=sys.stderr)
        return result.returncode

    produced = P.WORK / args.output_dir / P.BUILD_TRACK1_NAME
    if not produced.is_file():
        print("error: the build did not produce %s" % produced, file=sys.stderr)
        return 1

    print()
    print("verifying the result ...")
    size = produced.stat().st_size
    digest = P.sha256(produced)
    print("  size    %d %s" % (size, "ok" if size == P.PATCHED_TRACK1_SIZE else "MISMATCH (expected %d)" % P.PATCHED_TRACK1_SIZE))
    print("  sha256  %s" % digest)
    if digest == P.PATCHED_TRACK1_SHA256:
        print("  matches the released build exactly.")
        print()
        print("open this in your emulator:")
        print("  %s" % (P.WORK / args.output_dir / "Shin Super Robot Taisen Korean Full Translation.cue"))
        return 0

    print("  expected %s" % P.PATCHED_TRACK1_SHA256)
    print()
    print("The build finished but does not match the release.  That is not")
    print("necessarily wrong - it means something in the inputs differs.  The")
    print("usual causes are a different retail dump, an edited file under data/,")
    print("or a stale work/ directory (delete it and run setup_workspace.py again).")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
