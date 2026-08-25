"""Build the release assets from a finished image.

    py -3.14 make_release.py --version v0.9.0 --disc "...(Track 1).bin"

Produces, under release/:

    shin-srw-korean-<version>.xdelta      the binary patch
    shin-srw-korean-<version>-easy-apply.zip
    SHA256SUMS_<version>.txt

and then decodes the patch back against the retail disc and compares the result
with the image it was made from, so a patch is never published without having
been applied at least once.

xdelta3 is not part of this repository (see easy-apply/README.md).  Point at one
with --xdelta or put it on PATH.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import ssrw_paths as P

EASY_APPLY_FILES = (
    "한글패치 적용하기.bat",
    "apply.ps1",
    "사용법 - 먼저 읽어주세요.txt",
    "xdelta3-정보.txt",
)


def find_xdelta(explicit: str | None) -> str:
    if explicit:
        return explicit
    for name in ("xdelta3", "xdelta3.exe", "xdelta", "xdelta.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "error: xdelta3 not found.  Install it or pass --xdelta <path>.\n"
        "       https://github.com/jmacd/xdelta"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="release tag, e.g. v0.9.0")
    parser.add_argument("--disc", help="retail Track 1 .bin")
    parser.add_argument("--built", help="patched Track 1 .bin (default: the build output under work/)")
    parser.add_argument("--xdelta", help="path to xdelta3")
    parser.add_argument("--no-zip", action="store_true", help="only produce the .xdelta and the checksum file")
    parser.add_argument("--bundle-xdelta", help="xdelta binary to put in the easy-apply zip as xdelta.exe")
    args = parser.parse_args()

    xdelta = find_xdelta(args.xdelta)
    source = P.find_disc(args.disc, P.DISC_TRACK1)
    if source is None:
        raise SystemExit("error: pass the retail Track 1 with --disc")
    built = Path(args.built) if args.built else P.BUILD_OUTPUT / P.BUILD_TRACK1_NAME
    if not built.is_file():
        raise SystemExit("error: no patched image at %s (run build_all.py first)" % built)

    P.RELEASE.mkdir(parents=True, exist_ok=True)
    patch = P.RELEASE / ("shin-srw-korean-%s.xdelta" % args.version)

    print("[1/4] encoding the patch (a few minutes) ...")
    encode = subprocess.run(
        [xdelta, "-e", "-9", "-S", "lzma", "-B", "671088640", "-W", "16777216", "-f",
         "-s", str(source), str(built), str(patch)]
    )
    if encode.returncode != 0:
        raise SystemExit("error: xdelta encode failed (exit %d)" % encode.returncode)
    print("  %s (%d bytes)" % (patch.name, patch.stat().st_size))

    print("[2/4] applying it back, to prove it works ...")
    with tempfile.TemporaryDirectory() as scratch:
        decoded = Path(scratch) / "decoded.bin"
        decode = subprocess.run(
            [xdelta, "-d", "-f", "-s", str(source), str(patch), str(decoded)]
        )
        if decode.returncode != 0:
            patch.unlink(missing_ok=True)
            raise SystemExit("error: xdelta decode failed (exit %d); patch deleted" % decode.returncode)
        digest = P.sha256(decoded)
    expected = P.sha256(built)
    if digest != expected:
        patch.unlink(missing_ok=True)
        raise SystemExit(
            "error: the patch does not reproduce the image it was made from.\n"
            "         built   %s\n         decoded %s\n       Patch deleted." % (expected, digest)
        )
    print("  round trip ok: %s" % digest)

    if not args.no_zip:
        print("[3/4] packing the easy-apply zip ...")
        bundle = P.RELEASE / ("shin-srw-korean-%s-easy-apply.zip" % args.version)
        missing = [n for n in EASY_APPLY_FILES if not (P.REPO / "easy-apply" / n).is_file()]
        if missing:
            raise SystemExit("error: easy-apply is missing " + ", ".join(missing))
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in EASY_APPLY_FILES:
                archive.write(P.REPO / "easy-apply" / name, name)
            archive.write(patch, patch.name)
            # xdelta is third-party and stays out of the repository, but the zip
            # is useless without it, so it is bundled here at release time.
            if args.bundle_xdelta:
                archive.write(Path(args.bundle_xdelta), "xdelta.exe")
        print("  %s (%d bytes)" % (bundle.name, bundle.stat().st_size))
        if not args.bundle_xdelta:
            print("  NOTE: pass --bundle-xdelta <path> so the zip carries xdelta.exe;"
                  " without it the zip cannot apply the patch.")
    else:
        print("[3/4] zip skipped")

    print("[4/4] writing the checksum file ...")
    result_name = "Shin Super Robot Taisen Korean %s (Track 1).bin" % args.version
    lines = [
        "# Shin Super Robot Taisen Korean patch %s" % args.version,
        "#",
        "# source (retail Track 1, MODE2/2352, %s bytes)" % format(P.RETAIL_TRACK1_SIZE, ","),
        "%s  %s" % (P.RETAIL_TRACK1_SHA256, P.DISC_TRACK1),
        "# source (retail Track 2, audio, %s bytes - not modified)" % format(P.RETAIL_TRACK2_SIZE, ","),
        "%s  %s" % (P.RETAIL_TRACK2_SHA256, P.DISC_TRACK2),
        "# result (%s bytes)" % format(P.PATCHED_TRACK1_SIZE, ","),
        "%s  %s" % (expected, result_name),
        "# patch (%s bytes)" % format(patch.stat().st_size, ","),
        "%s  %s" % (P.sha256(patch), patch.name),
    ]
    sums = P.RELEASE / ("SHA256SUMS_%s.txt" % args.version)
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("  %s" % sums.name)
    print()
    print("release assets are in %s" % P.RELEASE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
