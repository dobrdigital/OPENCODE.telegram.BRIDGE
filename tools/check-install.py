#!/usr/bin/env python3
"""Dry-run the install: build a throwaway cc-connect home from the templates.

Does NOT touch ~/.cc-connect. Verifies that the shipped config.example.toml
parses, that its model registry matches what `opencode models` actually
offers, and that the OpenCode config template parses as JSONC.

This is the difference between "the file looks right" and "someone can
install from it".
"""
import json
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
failures = 0


def report(ok: bool, name: str, detail: str = "") -> None:
    global failures
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures += 1


def jsonc_load(path: pathlib.Path):
    """Minimal JSONC: drop the string-keyed comment block and // comments."""
    s = path.read_text(encoding="utf-8")
    i = s.find('"//": [')
    if i >= 0:
        j = s.find("]", i)
        s = s[:i] + s[j + 2:]
    s = re.sub(r"^\s*//.*$", "", s, flags=re.M)
    return json.loads(s)


def main() -> int:
    print("== config.example.toml ==")
    cfg_path = ROOT / "config.example.toml"
    try:
        cfg = tomllib.load(cfg_path.open("rb"))
        report(True, "parses as TOML")
    except Exception as e:                                   # noqa: BLE001
        report(False, "parses as TOML", str(e))
        return 1

    proj = cfg["projects"][0]
    report(proj["agent"]["type"] == "opencode", "agent type is opencode")

    # The single most important line in the whole file.
    cmd = proj["agent"]["options"]["cmd"]
    report(cmd == ["opencode"],
           "cmd is a bare binary",
           f"got {cmd!r} - a multi-element cmd breaks /list (opencode.go:654)")

    work_dir = proj["agent"]["options"]["cmd"] and proj["agent"]["options"].get("work_dir", "")
    report("<PROJECT_DIR>" in work_dir or "E:/" in work_dir,
           "work_dir present", work_dir)

    report(proj.get("reset_on_idle_mins") == 0,
           "reset_on_idle_mins is 0",
           "non-zero rotates the session away from the desktop and CLI")

    report(proj.get("admin_from") == "*" or proj.get("admin_from"),
           "admin_from documented")

    tok = proj["platforms"][0]["options"]["token"]
    report("CHANGE-ME" in tok, "telegram token is a placeholder", tok[:16] + "...")

    mgmt = cfg.get("management", {})
    report(mgmt.get("enabled") is False,
           "management API off by default",
           "it exposes an HTTP control panel; opt in deliberately")

    print("== models ==")
    models = cfg["projects"][0]["agent"]["providers"][0]["models"]
    aliases = [m["alias"] for m in models]
    report(len(aliases) == len(set(aliases)), "aliases are unique")
    report(all("-free" in m["model"] or m["model"] == "opencode/big-pickle" for m in models),
           "registry is free models only")

    try:
        # Windows cannot CreateProcess a .cmd directly, and `opencode` on PATH
        # is a .cmd launcher. shell=True routes through cmd.exe; the command
        # string is a fixed literal, no user input reaches it.
        r = subprocess.run("opencode models", shell=True, capture_output=True, timeout=120)
        live = {l.strip() for l in r.stdout.decode("utf-8", "replace").splitlines()
                if l.strip().startswith("opencode/")}
        declared = {m["model"] for m in models}
        report(declared <= live, "declared models exist upstream",
               f"missing: {sorted(declared - live)}" if declared - live else "")
        report(live <= declared, "no upstream model is unregistered",
               f"unregistered: {sorted(live - declared)}" if live - declared else "")
    except subprocess.TimeoutExpired:
        report(False, "opencode models reachable", "timed out")

    print("== opencode.jsonc.example.json ==")
    oc = ROOT / "opencode.jsonc.example.json"
    try:
        d = jsonc_load(oc)
        report(True, "parses as JSONC")
        report(d.get("model", "").endswith("-free"),
               "default model is free", d.get("model", ""))
        agents = {k: v.get("model") for k, v in d.get("agent", {}).items()}
        report(set(agents.values()) == {d.get("model")},
               "every agent inherits the default", str(agents))
    except Exception as e:                                   # noqa: BLE001
        report(False, "parses as JSONC", str(e))

    print("== config.toml must NOT be committed ==")
    report(not (ROOT / "config.toml").exists(), "no config.toml in the repo")
    report(not (ROOT / "service.json").exists(), "no service.json in the repo")

    print()
    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())