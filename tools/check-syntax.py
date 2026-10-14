#!/usr/bin/env python3
"""Syntax-check every script in the repo before publishing.

Cheap insurance: the path sanitizer rewrites files mechanically, and a broken
PowerShell or JS file would only surface on a stranger's machine.
"""
import ast
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NODE = r"C:\Program Files\nodejs\node.exe"


def check_python() -> int:
    bad = 0
    for p in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in p.parts or ".git" in p.parts:
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            print("  PY OK   ", p.relative_to(ROOT))
        except SyntaxError as e:
            print("  PY FAIL ", p.relative_to(ROOT), e)
            bad += 1
    return bad


def check_node() -> int:
    bad = 0
    if not pathlib.Path(NODE).exists():
        print("  node not found, skipping JS checks")
        return 0
    for p in sorted(ROOT.rglob("*.js")):
        if ".git" in p.parts or "node_modules" in p.parts:
            continue
        r = subprocess.run([NODE, "--check", str(p)], capture_output=True)
        if r.returncode == 0:
            print("  JS OK   ", p.relative_to(ROOT))
        else:
            print("  JS FAIL ", p.relative_to(ROOT), r.stderr.decode("utf-8", "replace")[:200])
            bad += 1
    return bad


def check_powershell() -> int:
    # The path must travel via the environment: with -Command, trailing
    # arguments do not populate $args, which previously caused the scripts
    # to be EXECUTED instead of parsed.
    ps = (
        "$e=$null;"
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "$env:CHECK_TARGET,[ref]$null,[ref]$e);"
        "if($e.Count){"
        "  $e | ForEach-Object { $_.Message } | Select-Object -First 1;"
        "  'FAIL'"
        "} else { 'OK' }"
    )
    bad = 0
    for p in sorted(ROOT.rglob("*.ps1")):
        if ".git" in p.parts:
            continue
        env = dict(os.environ, CHECK_TARGET=str(p))
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
            env=env,
        )
        lines = r.stdout.decode("utf-8", "replace").strip().splitlines()
        verdict = lines[-1] if lines else "?"
        note = lines[0] if verdict == "FAIL" and len(lines) > 1 else ""
        print(f"  PS {verdict:4s}  {p.relative_to(ROOT)}" + (f"  {note}" if note else ""))
        if verdict != "OK":
            bad += 1
    return bad


def main() -> int:
    print("== python ==")
    a = check_python()
    print("== javascript ==")
    b = check_node()
    print("== powershell ==")
    c = check_powershell()
    total = a + b + c
    print()
    print("failures:", total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())