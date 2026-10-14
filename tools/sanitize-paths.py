#!/usr/bin/env python3
"""Replace machine-specific paths with placeholders before publishing.

Output is deliberately ASCII-only: the Windows console here is cp1252, and
printing Cyrillic to it raises UnicodeEncodeError. Same trap the skill
documents for PowerShell 5.1.
"""
import os
import pathlib
import sys

# Build the host-specific patterns from the environment instead of hardcoding
# a username, so this file is safe to publish.
_HOME = os.environ.get("USERPROFILE", "").replace("\\", "/")
_PY311 = f"{_HOME}/AppData/Roaming/uv/python/cpython-3.11-windows-x86_64-none/python.exe"

SUBS = [
    # longest / most specific first
    (_PY311, "<PYTHON311>"),
    (f"C:/{_HOME}/AppData/Roaming/uv/python/cpython-3.11-windows-x86_64-none/python.exe", "<PYTHON311>"),
    ("C:/Program Files/Python/Python310/python.exe", "<PYTHON310>"),
    ("C:\\Program Files\\Python\\Python310\\python.exe", "<PYTHON310>"),
    ("C:/Program Files/nodejs/node.exe", "<NODE>"),
    ("C:\\Program Files\\nodejs\\node.exe", "<NODE>"),
    (f"C:/{_HOME}/AppData/Local/Handy", "<LOCALAPPDATA>/Handy"),
    (f"C:\\{_HOME}\\AppData\\Local\\Handy", "<LOCALAPPDATA>\\Handy"),
    (f"C:/{_HOME}", "<USERPROFILE>"),
    (f"C:\\{_HOME}", "<USERPROFILE>"),
    ("./_cc_connect", "<PROJECT_DIR>"),
    ("_cc_connect", "<PROJECT_DIR>"),
]

FILES = [
    "SKILL.md",
    "README.md",
    "config.example.toml",
    "install-autostart.ps1",
    "start-cc-connect.ps1",
    "autostart-test.ps1",
    "stt-selftest.txt",
    "gigaam-stt/server.py",
    "gigaam-stt/start-gigaam-stt.ps1",
    "opencode-shim/opencode-shim.js",
    "opencode-shim/test-opencode-shim.js",
    "opencode-shim/stub-opencode.js",
    "opencode-shim/install-npm-shim.ps1",
]


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    touched = 0
    for rel in FILES:
        p = root / rel
        if not p.exists():
            print("  missing:", rel)
            continue
        original = p.read_text(encoding="utf-8")
        s = original
        for a, b in SUBS:
            s = s.replace(a, b)
        if s != original:
            p.write_text(s, encoding="utf-8", newline="")
            print("  sanitized:", rel)
            touched += 1
    print("files changed:", touched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())