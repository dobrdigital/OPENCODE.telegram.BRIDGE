#!/usr/bin/env python3
"""Prove the audit actually fires.

`python tools/audit.py` on a healthy machine prints "no mismatches found",
which looks identical to a checker whose conditions never match anything. Both
of the readings are wrong: this file builds a deliberately broken environment
in a temporary directory, runs the real CLI against it, and asserts that each
expected finding appears.

Nothing here touches the real installation. USERPROFILE and APPDATA are
redirected for the child process only.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

BROKEN_CONFIG = """\
language = "en"

[management]
enabled = false
port = 9820
token = "CHANGE-ME-management-token"

[[projects]]
name = "broken"
admin_from = "*"
reset_on_idle_mins = 30

[projects.agent]
type = "opencode"

[projects.agent.options]
cmd      = ["node.exe", "opencode-shim.js", "--dir"]
work_dir = "<PROJECT_DIR>"
model    = "opencode/space-bunny-free"

[[projects.platforms]]
type = "telegram"

[projects.platforms.options]
token = "CHANGE-ME-your-telegram-bot-token"

[tts]
enabled  = true
provider = "edge"
voice    = "ru-RU-DmitryNeural"

[speech]
enabled  = true
provider = "openai"
language = "ru"

[speech.openai]
base_url = "http://127.0.0.1:8301/v1"
api_key  = "local"
model    = "whisper-1"
"""

# TOML reads [projects] and [[projects]] equally happily, so writing the wrong
# one is silent. Used to prove the audit reports the shape instead of dying.
WRONG_SHAPE_CONFIG = BROKEN_CONFIG.replace(
    "[[projects]]\nname = \"broken\"",
    "[projects]\nname = \"broken\"")

# (check id, why it matters) - each must appear in the audit output
EXPECT_CONFIG = [
    ("config.cmd", "a multi-element cmd makes /list exit 1"),
    ("config.model", "a pinned model silently breaks /model switch"),
    ("config.idle", "a nonzero idle reset splits the session across clients"),
    ("config.work_dir", "a missing work_dir stops the agent"),
    ("config.placeholder", "an unreplaced placeholder never reaches Telegram"),
    ("config.stt", "speech enabled with nothing listening loses every message"),
]


def run_audit(home: pathlib.Path, appdata: pathlib.Path | None, only: str) -> list[dict]:
    # appdata=None means inherit. Overriding it also moves npm's global
    # prefix, which makes every npm package look uninstalled - the first
    # version of this test passed for entirely the wrong reason because of it.
    env = dict(os.environ, USERPROFILE=str(home))
    if appdata is not None:
        env["APPDATA"] = str(appdata)
    env.pop("PYTHONIOENCODING", None)
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audit.py"),
         "--json", "--only", only],
        env=env, capture_output=True, timeout=600,
    )
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        print("  audit did not return JSON:")
        print("  " + r.stdout.decode("utf-8", "replace")[-800:])
        print("  " + r.stderr.decode("utf-8", "replace")[-800:])
        return []


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cc-audit-test-"))
    home = tmp / "home"
    appdata = tmp / "appdata"
    (home / ".cc-connect").mkdir(parents=True)
    (appdata / "npm").mkdir(parents=True)
    (home / ".cc-connect" / "config.toml").write_text(BROKEN_CONFIG, encoding="utf-8")

    # Launchers npm writes: present, but not routed through the shim.
    (appdata / "npm" / "opencode.cmd").write_text(
        "@ECHO off\r\nnode \"%~dp0\\node_modules\\@opencode\\cli\\bin\"\r\n",
        encoding="utf-8")
    (appdata / "npm" / "opencode").write_text(
        "#!/bin/sh\nexec node \"$basedir/node_modules/@opencode/cli/bin\" \"$@\"\n",
        encoding="utf-8")

    failures = 0
    try:
        print()
        print("  audit self-test")
        print()

        # ---- config group -------------------------------------------
        findings = run_audit(home, appdata, "config")
        seen = {f["check"] for f in findings}
        for check, why in EXPECT_CONFIG:
            hit = check in seen
            print(f"  {'OK  ' if hit else 'FAIL'}  {check:22} {why}")
            if not hit:
                failures += 1

        sev = {f["check"]: f["severity"] for f in findings}
        for check in ("config.cmd", "config.model", "config.work_dir",
                      "config.placeholder"):
            if sev.get(check) != "FAIL":
                print(f"  FAIL  {check:22} should be FAIL, got {sev.get(check)}")
                failures += 1

        # every finding must carry a repair command; a warning nobody can act
        # on is the same as no warning
        for f in findings:
            if f["severity"] in ("FAIL", "WARN") and not f["fix"]:
                print(f"  FAIL  {f['check']:22} reported without a fix")
                failures += 1

        # ---- wrong TOML shape ----------------------------------------
        # A check that raises on a plausible config is worse than no check:
        # it reports nothing while the thing it should have caught is broken.
        (home / ".cc-connect" / "config.toml").write_text(
            WRONG_SHAPE_CONFIG, encoding="utf-8")
        wrong = run_audit(home, appdata, "config")
        ids = {f["check"] for f in wrong}
        ok = "config.project" in ids and not any(
            f["check"].endswith(".crashed") for f in wrong)
        print(f"  {'OK  ' if ok else 'FAIL'}  {'config.project':22} "
              f"[projects] reported as wrong shape, no crash")
        if not ok:
            for f in wrong:
                print(f"        {f['check']} / {f['severity']}: {f['title']}")
            failures += 1

        (home / ".cc-connect" / "config.toml").write_text(
            BROKEN_CONFIG, encoding="utf-8")

        # ---- version skew --------------------------------------------
        # The desktop ships and upgrades its own CLI on a different schedule
        # from the npm one, so this gap opens on its own. A fake desktop at a
        # deliberately incompatible version is the only honest way to prove
        # the check is more than a quiet pass.
        local = tmp / "localappdata"
        res = local / "Programs" / "@opencodedesktop" / "resources"
        res.mkdir(parents=True)
        (res / "opencode-cli.version").write_text("1.19.0\n", encoding="utf-8")

        env_backup = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(local)
        try:
            skew = run_audit(home, None, "skew")
        finally:
            if env_backup is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = env_backup

        sk = [f for f in skew if f["check"] == "skew.desktop_cli"]
        # Assert on the wording, not just the severity: "no npm CLI to pair
        # with" is also a FAIL and would have satisfied a looser test while
        # proving nothing about version comparison.
        ok = bool(sk) and sk[0]["severity"] == "FAIL" and "disagree" in sk[0]["title"]
        detail = sk[0]["title"] if sk else "nothing reported"
        print(f"  {'OK  ' if ok else 'FAIL'}  {'skew.desktop_cli':22} "
              f"1.19.0 desktop vs 2.x CLI -> FAIL ({detail})")
        if not ok:
            for f in skew:
                print(f"        {f['check']} / {f['severity']}: {f['title']}")
            failures += 1

        # A patch-only gap is a warning, not a failure. Asserted separately
        # because collapsing both into FAIL would train people to ignore it.
        (res / "opencode-cli.version").write_text("2.0.27\n", encoding="utf-8")
        os.environ["LOCALAPPDATA"] = str(local)
        try:
            patch_skew = run_audit(home, None, "skew")
        finally:
            if env_backup is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = env_backup
        pk = [f for f in patch_skew if f["check"] == "skew.desktop_cli"]
        ok = bool(pk) and pk[0]["severity"] == "WARN" and "disagree" in pk[0]["title"]
        print(f"  {'OK  ' if ok else 'FAIL'}  {'skew (patch only)':22} "
              f"a patch gap is a WARN, not a FAIL")
        if not ok:
            for f in patch_skew:
                print(f"        {f['check']} / {f['severity']}: {f['title']}")
            failures += 1

        # ---- shim group ---------------------------------------------
        shim = run_audit(home, appdata, "shim")
        unpatched = [f for f in shim if f["check"] == "shim.unpatched"]
        ok = len(unpatched) == 2
        print(f"  {'OK  ' if ok else 'FAIL'}  {'shim.unpatched':22} "
              f"both npm launchers reported ({len(unpatched)}/2)")
        if not ok:
            failures += 1

        # ---- healthy environment must stay quiet ---------------------
        # The negative test only means something if the positive one is clean.
        quiet = run_audit(pathlib.Path(os.environ.get("USERPROFILE", "")),
                          pathlib.Path(os.environ.get("APPDATA", "")), "config")
        noisy = [f for f in quiet if f["severity"] == "FAIL"]
        ok = not noisy
        print(f"  {'OK  ' if ok else 'FAIL'}  {'no false positives':22} "
              f"the real install reports {len(noisy)} failures")
        if not ok:
            for f in noisy:
                print(f"        {f['check']}: {f['title']}")
            failures += 1

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())