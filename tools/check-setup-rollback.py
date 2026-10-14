#!/usr/bin/env python3
"""Fault-injection: prove the installer refuses to leave a bad config behind.

The wizard claims to verify what it wrote and to roll back when the check
fails. Nothing tested that claim, because every existing test drives a
correct install. A rollback path that has never executed is a claim, not a
feature - and the failure it guards against is exactly the one that costs
someone an afternoon of debugging a broken bridge.

Injects a defect into the template, runs the wizard, and asserts that
  1. the wizard reports the mismatch,
  2. the config on disk is NOT left in the broken state,
  3. a pre-existing config is restored rather than deleted.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "config.example.toml"
TOKEN = "123456789:AAhFAKEtokenForTheInstallerTestOnly0000000"

failures = 0


MISMATCH = "did not match your answers"


def check(ok: bool, name: str, detail: str = "") -> None:
    global failures
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures += 1


def run_wizard(home: pathlib.Path) -> str:
    env = dict(os.environ, USERPROFILE=str(home))
    r = subprocess.run(
        [sys.executable, "setup.py", "--yes", "--no-autostart",
         "--project-dir", str(home / "proj"),
         "--name", "rollback-bridge",
         "--token", TOKEN],
        cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=300,
    )
    return (r.stdout + r.stderr).decode("utf-8", "replace")


def scenario_fresh_install(broken_template: str) -> None:
    """No prior config: a failed write must leave nothing behind."""
    home = pathlib.Path(tempfile.mkdtemp(prefix="cc-rollback-fresh-"))
    original = TEMPLATE.read_text(encoding="utf-8")
    try:
        (home / "proj").mkdir(parents=True, exist_ok=True)
        TEMPLATE.write_text(broken_template, encoding="utf-8", newline="")
        out = run_wizard(home)

        cfg = home / ".cc-connect" / "config.toml"
        check(MISMATCH in out, "wizard reported the mismatch")
        check(not cfg.exists(),
              "no config left behind on a fresh install",
              "absent" if not cfg.exists() else "STILL PRESENT")
    finally:
        TEMPLATE.write_text(original, encoding="utf-8", newline="")
        shutil.rmtree(home, ignore_errors=True)


def scenario_existing_install(broken_template: str) -> None:
    """Prior config: the failed write must restore it, not delete it."""
    home = pathlib.Path(tempfile.mkdtemp(prefix="cc-rollback-existing-"))
    original = TEMPLATE.read_text(encoding="utf-8")
    try:
        (home / "proj").mkdir(parents=True, exist_ok=True)
        (home / ".cc-connect").mkdir(parents=True, exist_ok=True)
        cfg = home / ".cc-connect" / "config.toml"
        sentinel = '# PRECIOUS-FROM-BEFORE\nlanguage = "en"\n'
        cfg.write_text(sentinel, encoding="utf-8", newline="")

        TEMPLATE.write_text(broken_template, encoding="utf-8", newline="")
        out = run_wizard(home)

        check(MISMATCH in out, "wizard reported the mismatch (existing install)")
        check("rolled back" in out, "wizard said it rolled back")
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        check("PRECIOUS-FROM-BEFORE" in text,
              "previous config restored intact",
              text.splitlines()[0] if text else "(missing)")
    finally:
        TEMPLATE.write_text(original, encoding="utf-8", newline="")
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    print("== rollback on a fresh install ==")
    # Rename the work_dir key so the wizard's substitution cannot land, which
    # is precisely the class of silent divergence the verification exists for.
    original = TEMPLATE.read_text(encoding="utf-8")
    broken = original.replace('work_dir = "<PROJECT_DIR>"', 'workdir_typo = "<PROJECT_DIR>"')
    check(broken != original, "fault injected into the template")
    scenario_fresh_install(broken)

    print()
    print("== rollback over an existing install ==")
    scenario_existing_install(broken)

    print()
    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())