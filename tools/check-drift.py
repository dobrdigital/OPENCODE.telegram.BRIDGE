#!/usr/bin/env python3
"""Compare this repository against the live installation.

Editing the repo and wondering why the running install does not behave the
way the change suggests is a slow way to lose an afternoon. The two copies
drift: the repo gets sanitized for publication, the live copy gets patched
by hand, and the gap is invisible until something misbehaves.

Intentional differences are normalized away before comparing:
  - placeholder paths (<USERPROFILE>, <NODE>, ...) vs the real ones
  - host-specific absolute paths in either direction

Anything still differing is real drift and is reported as a failure.

    python tools/check-drift.py              # report
    python tools/check-drift.py --sync       # copy repo -> live
"""
from __future__ import annotations

import argparse
import filecmp
import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = pathlib.Path(os.environ.get("USERPROFILE", str(pathlib.Path.home()))) / ".cc-connect"

PAIRS = [
    ("start-cc-connect.ps1", "start-cc-connect.ps1"),
    ("gigaam-stt/server.py", "gigaam-stt/server.py"),
    ("gigaam-stt/start-gigaam-stt.ps1", "gigaam-stt/start-gigaam-stt.ps1"),
    ("opencode-shim/opencode-shim.js", "opencode-shim/opencode-shim.js"),
    ("opencode-shim/test-opencode-shim.js", "opencode-shim/test-opencode-shim.js"),
    ("opencode-shim/install-npm-shim.ps1", "opencode-shim/install-npm-shim.ps1"),
]

PLACEHOLDER = re.compile(r"<[A-Z_]+>")
# Quoted absolute path first: paths routinely contain spaces, and a
# whitespace-excluding character class would truncate "C:\Program Files"
# and report a sanitized placeholder as drift.
QUOTED_PATH = re.compile(r"(['\"])[A-Za-z]:[\\/][^'\"\n]*\1")
BARE_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|\n]*")


def normalize(text: str) -> str:
    """Collapse host paths and placeholders so only real differences show."""
    text = QUOTED_PATH.sub("<PATH>", text)
    text = BARE_PATH.sub("<PATH>", text)
    text = PLACEHOLDER.sub("<PATH>", text)
    # QUOTED_PATH consumes the quotes around a real path, while a placeholder
    # keeps them. Drop them so the two forms compare equal.
    text = text.replace("'<PATH>'", "<PATH>").replace('"<PATH>"', "<PATH>")
    return text.replace("\r\n", "\n")


def same(repo: pathlib.Path, live: pathlib.Path) -> bool:
    try:
        if filecmp.cmp(repo, live, shallow=False):
            return True
    except OSError:
        return False
    return normalize(repo.read_text(encoding="utf-8", errors="replace")) == \
           normalize(live.read_text(encoding="utf-8", errors="replace"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync", action="store_true", help="copy repo files over the live install")
    args = ap.parse_args()

    if not LIVE.exists():
        print(f"live install not found: {LIVE}")
        print("nothing to compare - setup.py has not run here")
        return 0

    drifted = []
    missing = []

    for repo_rel, live_rel in PAIRS:
        r, l = ROOT / repo_rel, LIVE / live_rel
        if not r.exists():
            continue
        if not l.exists():
            missing.append(live_rel)
            continue
        if same(r, l):
            print(f"  same     {live_rel}")
        else:
            print(f"  DRIFT    {live_rel}")
            drifted.append((repo_rel, live_rel))

    if missing:
        print()
        print("not installed:")
        for m in missing:
            print("  -", m)

    if args.sync and drifted:
        print()
        for repo_rel, live_rel in drifted:
            dst = LIVE / live_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / repo_rel, dst)
            print("  synced   ", live_rel)

    if drifted and not args.sync:
        print()
        print(f"{len(drifted)} file(s) drifted. Apply with:")
        print("  python tools/check-drift.py --sync")

    return 1 if drifted and not args.sync else 0


if __name__ == "__main__":
    sys.exit(main())