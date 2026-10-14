#!/usr/bin/env python3
"""Run setup.py for real into a throwaway HOME and verify what it wrote.

check-install.py validates the templates. This validates the installer: that
the wizard, driven non-interactively, produces a config that actually matches
the answers it was given. Template and wizard can each be fine on their own
and still disagree - a placeholder renamed in one place and not the other is
exactly that.

Redirecting USERPROFILE keeps the real installation untouched. Scheduled
tasks are machine-global, so --no-autostart is mandatory here; registering
them from a test would rewrite the developer's own autostart setup.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_TOKEN = "123456789:AAhFAKEtokenForTheInstallerTestOnly0000000"
FAKE_PROJECT = "installer-selftest-project"

failures = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global failures
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures += 1


def run_wizard(home: pathlib.Path) -> tuple[int, str]:
    env = dict(os.environ, USERPROFILE=str(home))
    r = subprocess.run(
        [sys.executable, "setup.py", "--yes", "--no-autostart",
         "--project-dir", str(home / FAKE_PROJECT),
         "--name", "selftest-bridge",
         "--token", FAKE_TOKEN],
        cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=300,
    )
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def main() -> int:
    home = pathlib.Path(tempfile.mkdtemp(prefix="cc-setup-test-"))
    try:
        (home / FAKE_PROJECT).mkdir(parents=True, exist_ok=True)

        code, out = run_wizard(home)
        check(code == 0, "setup.py exits 0", f"code={code}")
        if code != 0:
            print(out[-800:])
            return 1

        cfg_path = home / ".cc-connect" / "config.toml"
        check(cfg_path.exists(), "config.toml written", str(cfg_path))
        if not cfg_path.exists():
            print(out[-800:])
            return 1

        cfg = tomllib.load(cfg_path.open("rb"))
        proj = cfg["projects"][0]
        opts = proj["agent"]["options"]

        check(proj["name"] == "selftest-bridge",
              "session name applied", proj["name"])
        check(pathlib.Path(opts["work_dir"]).name == FAKE_PROJECT,
              "work_dir applied", opts["work_dir"])
        check(opts["cmd"] == ["opencode"],
              "cmd is a bare binary", str(opts["cmd"]))
        check(proj["reset_on_idle_mins"] == 0, "reset_on_idle_mins is 0")
        check(proj["platforms"][0]["options"]["token"] == FAKE_TOKEN,
              "telegram token applied")
        check("CHANGE-ME" not in cfg_path.read_text(encoding="utf-8"),
              "no placeholder left in the config")

        oc = home / ".config" / "opencode" / "opencode.jsonc"
        check(oc.exists(), "opencode.jsonc written")
        if oc.exists():
            raw = oc.read_text(encoding="utf-8")
            raw = re.sub(r",(\s*[}\]])", r"\1", raw)
            data = json.loads(raw)
            check(data.get("model", "").endswith("-free"),
                  "default model is free", data.get("model", ""))
            agents = {k: v.get("model") for k, v in data.get("agent", {}).items()}
            check(set(agents.values()) == {data.get("model")},
                  "every agent inherits the default")

        for rel in (".cc-connect/start-cc-connect.ps1",
                    ".cc-connect/gigaam-stt/server.py",
                    ".cc-connect/gigaam-stt/start-gigaam-stt.ps1"):
            check((home / rel).exists(), f"installed {rel}")

        # The wizard verifies its own output; make sure it did not quietly
        # claim success it did not have.
        check("verified against your answers" in out,
              "wizard reported post-write verification")
        check("does NOT match your answers" not in out,
              "wizard did not report a mismatch")

        # Nothing may have escaped the throwaway home.
        real = pathlib.Path(os.environ.get("USERPROFILE", "")) / ".cc-connect"
        if real.exists():
            check(True, "real install left alone",
                  f"{len(list(real.glob('*.toml')))} config(s) present")

    finally:
        shutil.rmtree(home, ignore_errors=True)

    print()
    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())