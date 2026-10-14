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


def notice(name: str, detail: str = "") -> None:
    """Report something worth acting on that must not fail the build.

    The gateway adds free models on its own schedule. Treating each arrival
    as a failure means every one of them turns the repository red until
    somebody edits the template by hand - a check that cries wolf on a
    routine event trains people to ignore the whole file.
    """
    print(f"  note  {name}" + (f"  {detail}" if detail else ""))


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
        if live - declared:
            # Upstream added models. Not an error - the template is not
            # required to list every free model the gateway happens to offer,
            # and failing here would turn every routine upstream release into
            # a red build. It is a suggestion, so it stays a suggestion.
            notice("upstream models not in the template",
                   f"add to config.example.toml if you want them in /model: "
                   f"{', '.join(sorted(live - declared))}")
        else:
            report(True, "no upstream model is unregistered")
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

    check_state_keys(report)

    print()
    print("failures:", failures)
    return 1 if failures else 0


def check_state_keys(report) -> None:
    """Every state key setup.py reads must be one it was actually given.

    step_dependencies builds `state` from Dep.key, and the later steps look
    values up by hand-written string. Nothing checks the two spellings
    agree, and nothing would complain if they did not: dict.get on a missing
    key returns None, and None reads as "not installed".

    That is exactly what happened. deps.py calls the package `edge-tts` while
    step_tts asked for `edge_tts`, so voice output could never be enabled and
    the installer reported edge-tts as missing on a machine where it was
    installed. Step 1 said OK, step 7 said it was not there, and neither
    looked like a bug.
    """
    import ast

    print("== installer state keys ==")
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import deps                                              # noqa: PLC0415
        dep_keys = {d.key for d in deps.DEPS}
    except Exception as e:                                       # noqa: BLE001
        report(False, "dependency table loads", str(e))
        return

    src = (ROOT / "setup.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    read: set[str] = set()
    written: set[str] = set()
    for node in ast.walk(tree):
        # state.get("x") and state["x"]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant):
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "state":
                read.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id == "state" and isinstance(node.slice, ast.Constant):
            key = node.slice.value
            ctx = node.ctx
            if isinstance(ctx, ast.Store):
                written.add(key)
            elif isinstance(ctx, ast.Load):
                read.add(key)

    known = dep_keys | written
    unknown = sorted(read - known)
    report(not unknown,
           "every state key the installer reads exists",
           f"unknown: {unknown}" if unknown else
           f"reads {len(read)}, table declares {len(dep_keys)}")

    for key in sorted(read & dep_keys):
        report(True, f"  {key} comes from the dependency table")


if __name__ == "__main__":
    sys.exit(main())