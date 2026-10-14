"""Dependency discovery for setup.py.

Kept separate so setup.py stays readable and so this can be exercised on its
own: every claim here is "how do I find out what is installed and what the
newest thing is", and getting a package name wrong means installing the
wrong software.

Sources are the official registries, queried live:

  npm     npm view <pkg> version
  pip     <python> -m pip index versions <pkg>
  winget  winget list --id <id> --exact

Note on npm names. `@opencode/cli` is the current package. `opencode-ai`
also exists and is version 1.19.0 - an older line whose database schema
does not match the desktop app's. Installing that one recreates exactly the
collision this project exists to avoid, so the wrong name must never appear
in an install hint.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field

IS_WIN = None  # set from os.name at call time


@dataclass
class Dep:
    key: str
    label: str
    kind: str                       # npm | pip | winget | manual
    pkg: str                        # official package or product id
    install: str                    # command that installs the newest version
    check: str | None = None        # command that prints the installed version
    required: bool = True
    why: str = ""
    safe_auto: bool = False         # may we install it without a prompt?
    current: str | None = None
    latest: str | None = None
    notes: list[str] = field(default_factory=list)


DEPS = [
    Dep(
        key="opencode",
        label="OpenCode CLI",
        kind="npm",
        pkg="@opencode/cli",
        install="npm install -g @opencode/cli",
        check="npm ls -g @opencode/cli --depth=0 --json",
        why="the agent this bridge drives",
        safe_auto=True,
    ),
    Dep(
        key="cc-connect",
        label="cc-connect bridge",
        kind="npm",
        pkg="cc-connect",
        install="npm install -g cc-connect",
        check="npm ls -g cc-connect --depth=0 --json",
        why="connects Telegram to OpenCode",
        safe_auto=True,
    ),
    Dep(
        key="edge-tts",
        label="edge-tts",
        kind="pip",
        pkg="edge-tts",
        install="pip install --upgrade edge-tts",
        check=None,
        required=False,
        why="voice replies; provider 'edge' shells out to it",
        safe_auto=True,
    ),
    Dep(
        key="ffmpeg",
        label="ffmpeg",
        kind="winget",
        pkg="Gyan.FFmpeg",
        install="winget install --id Gyan.FFmpeg --exact --source winget",
        check=None,
        why="both voice paths need it: the bridge unpacks OGG, the shim "
            "normalises to 16 kHz mono",
        safe_auto=False,   # system-wide, often needs elevation
    ),
    Dep(
        key="opencode-desktop",
        label="OpenCode desktop",
        kind="desktop",
        pkg="@opencodedesktop",
        install="https://opencode.ai/download",
        required=False,
        why="shares one database with the CLI. Its bundled CLI updates "
            "itself with the app; the npm one only when someone runs "
            "`npm i -g @opencode/cli`. They drift apart on their own.",
        safe_auto=False,
    ),
    Dep(
        key="handy",
        label="Handy",
        kind="manual",
        pkg="handy",
        install="https://github.com/handy-computer",
        required=False,
        why="local speech-to-text runtime (voice input)",
        safe_auto=False,
    ),
]


def _run(cmd: str, timeout: int = 120) -> tuple[int, str]:
    """Windows cannot CreateProcess a .cmd; route those through cmd.exe.

    The first token may arrive quoted ("C:\\Python311\\python.exe" -m ...), so
    strip the quotes before deciding how to launch it. Getting this wrong made
    an installed package report as missing.
    """
    exe = cmd.split()[0].strip('"').strip("'")
    resolved = shutil.which(exe) or (exe if exe.lower().endswith((".exe", ".cmd", ".bat")) else None)
    shell = bool(resolved and resolved.lower().endswith((".cmd", ".bat")))
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:                                   # noqa: BLE001
        return 127, str(e)


def _installed_npm(pkg: str) -> str | None:
    code, out = _run(f"npm ls -g {pkg} --depth=0 --json")
    if code != 0:
        return None
    data = _json_from(out)
    if data is None:
        return None
    for name, entry in (data.get("dependencies") or {}).items():
        if name == pkg:
            return str(entry.get("version"))
    return None


def _json_from(text: str) -> dict | None:
    """Pull the first JSON object out of a command's output, ignoring the rest.

    npm interleaves notices with its results - 'npm notice New major version
    of npm available! 11.16.0 -> 12.2.0' lands after the JSON on some runs
    and not others. Parsing the whole buffer therefore fails intermittently,
    and the failure is silent: the package reads as not installed on a machine
    where it is installed, which is worse than no check at all because it
    teaches people to ignore the report.

    raw_decode stops at the end of the first value instead of demanding the
    stream end there.
    """
    i = text.find("{")
    if i < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[i:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _latest_npm(pkg: str) -> str | None:
    code, out = _run(f"npm view {pkg} version")
    if code != 0:
        return None
    # Same trailing-notice problem as npm ls: take the first line that looks
    # like a version rather than trusting the last line of the buffer.
    for line in out.strip().splitlines():
        v = line.strip()
        if re.match(r"^\d+\.\d+\.\d+", v):
            return v
    return None


def sys_executable() -> str:
    import sys
    return sys.executable


def candidate_pythons() -> list[str]:
    r"""Every interpreter worth asking, not just the one running this file.

    Three ways this goes wrong, all hit in testing:

    - edge-tts here lives under Python310 while this script may run under a
      different 3.11. Probing only sys.executable reports "missing" for
      software that is actually present, which teaches people to ignore the
      report.
    - USERPROFILE is already an absolute path ('C:\Users\name'), not a user
      name. Building 'C:\Users\{USERPROFILE}\' yields
      'C:\Users\C:\Users\name\', which matches nothing.
    - sys.executable may be an unrelated tool's virtualenv. Those are skipped:
      installing into somebody else's venv is exactly the mistake this project
      already had to unpick once.
    """
    import glob
    import os
    import sys as _sys

    out: list[str] = []

    def add(path: str) -> None:
        if path and path not in out and os.path.exists(path) and "\\venv\\" not in path.lower():
            out.append(path)

    # the interpreter running us, unless it is someone's venv
    if "\\venv\\" not in _sys.executable.lower():
        add(_sys.executable)

    # every standard CPython install for the current user
    programs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python")
    for path in sorted(glob.glob(os.path.join(programs, "Python3*", "python.exe")), reverse=True):
        add(path)

    # system-wide installs
    for pat in (r"C:\Python3*\python.exe", r"C:\Program Files\Python3*\python.exe"):
        for path in sorted(glob.glob(pat), reverse=True):
            add(path)

    # uv-managed interpreters
    uv = os.path.join(os.environ.get("APPDATA", ""), "uv", "python")
    for path in sorted(glob.glob(os.path.join(uv, "*", "python.exe")), reverse=True):
        add(path)

    return out


def _installed_pip(pkg: str) -> str | None:
    for exe in candidate_pythons():
        code, out = _run(f'"{exe}" -m pip show {pkg}')
        if code != 0:
            continue
        m = re.search(r"^Version:\s*(\S+)", out, re.M)
        if m:
            return m.group(1)
    return None


def _latest_pip(pkg: str) -> str | None:
    for exe in candidate_pythons():
        code, out = _run(f'"{exe}" -m pip index versions {pkg}')
        if code != 0:
            continue
        m = re.search(rf"{re.escape(pkg)}\s+\(\s*(\S+?)\s*\)", out)
        if m:
            return m.group(1)
        m = re.search(r"Available versions:\s*([^\n]+)", out)
        if m:
            return m.group(1).split(",")[0].strip()
    return None


def desktop_runtime_cli() -> str | None:
    """Version of the CLI the desktop is actually running right now.

    The desktop keeps a third copy under %APPDATA%\\ai.opencode.desktop\\cli\\
    in a directory named after the version, and that is the binary the live
    `serve --service` process was started from - not the one bundled in
    resources\\. Reading only resources\\ means checking a copy that may
    never execute, which is how a skew check comes to report agreement
    between two binaries while the running one is somewhere else entirely.

    Falls back to the resources copy if this layout is not present, so a
    different install style still gets checked rather than skipped.
    """
    import glob
    import os
    base = os.path.join(os.environ.get("APPDATA", ""), "ai.opencode.desktop", "cli")
    for d in sorted(glob.glob(os.path.join(base, "*")), reverse=True):
        exe = os.path.join(d, "opencode-cli.exe")
        if not os.path.exists(exe):
            continue
        m = re.match(r"(\d+\.\d+\.\d+)", os.path.basename(d))
        if m:
            return m.group(1)
        code, out = _run(f'"{exe}" --version', timeout=60)
        v = re.search(r"v?(\d+\.\d+\.\d+)", out)
        if v:
            return v.group(1)
    return None


def opencode_copies() -> dict[str, str]:
    """Every opencode CLI on this machine, and the version each one is.

    Three copies coexist by design and none of them is optional:
      npm       what cc-connect and the shim invoke
      desktop   the binary shipped in the app's resources
      runtime   the versioned copy in %APPDATA% the live server runs from
    They are updated by three different schedules. A skew check that looks
    at only two of them is a check that can be right for the wrong reason.
    """
    out: dict[str, str] = {}
    v = _installed_npm("@opencode/cli")
    if v:
        out["npm CLI"] = v
    v = _installed_desktop()
    if v:
        out["desktop bundle"] = v
    v = desktop_runtime_cli()
    if v:
        out["desktop runtime"] = v
    return out


def _installed_handy() -> str | None:
    import os
    exe = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Handy", "handy.exe")
    return "installed" if os.path.exists(exe) else None


def desktop_root() -> str | None:
    """Locate the Electron desktop app, or None if it is not installed.

    An npm package named `opencode-desktop` also exists. It is not this, and
    reading a version out of the wrong one would report a skew that does not
    exist - so the directory name has to match exactly.
    """
    import os
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
    for cand in (os.path.join(base, "@opencodedesktop"),
                 os.path.join(base, "opencode-desktop")):
        if os.path.isdir(cand):
            return cand
    return None


def _installed_desktop() -> str | None:
    """The version of the CLI the desktop app ships, not the app's own.

    `resources/opencode-cli.version` is a one-line file written by the build.
    It is the honest number to compare against the npm CLI, because that
    binary is what opens the shared database.

    Reading the app's version instead would let 2.0.28 desktop bundle a
    2.0.29 CLI and still report agreement.
    """
    import os
    root = desktop_root()
    if not root:
        return None
    vf = os.path.join(root, "resources", "opencode-cli.version")
    try:
        with open(vf, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip().lstrip("v") or None
    except OSError:
        pass
    # Fall back to asking the binary, which is slower but authoritative.
    exe = os.path.join(root, "resources", "opencode-cli.exe")
    if os.path.exists(exe):
        code, out = _run(f'"{exe}" --version', timeout=60)
        m = re.search(r"v?(\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    return None


def _installed_winget(product_id: str) -> tuple[str | None, str | None]:
    code, out = _run(f'winget list --id {product_id} --exact --accept-source-agreements')
    if code != 0:
        return None, None
    # table row: Name Id Version Available Source
    m = re.search(rf"{re.escape(product_id)}\s+(\S+)\s+(\S+)", out)
    return (m.group(1), m.group(2)) if m else (None, None)


def probe(dep: Dep) -> Dep:
    """Fill in current/latest for one dependency. Never raises."""
    try:
        if dep.kind == "npm":
            dep.current = _installed_npm(dep.pkg)
            dep.latest = _latest_npm(dep.pkg)
        elif dep.kind == "pip":
            dep.current = _installed_pip(dep.pkg)
            dep.latest = _latest_pip(dep.pkg)
        elif dep.kind == "winget":
            dep.current, dep.latest = _installed_winget(dep.pkg)
        elif dep.kind == "manual" and dep.key == "handy":
            dep.current = _installed_handy()
            dep.latest = dep.current   # no registry to query; presence is all there is
        elif dep.kind == "desktop":
            dep.current = _installed_desktop()
            # The desktop ships its own CLI and updates it to whatever the app
            # shipped with, so "newest" is not the registry's number - it is
            # whatever this host's app was built with. The skew check compares
            # the two against each other instead.
            dep.latest = dep.current
    except Exception as e:                                   # noqa: BLE001
        dep.notes.append(f"probe failed: {type(e).__name__}")
    return dep


def probe_all(deps=None) -> list[Dep]:
    return [probe(d) for d in (deps if deps is not None else DEPS)]


def install(dep: Dep, timeout: int = 900) -> tuple[bool, str]:
    """Run the official install command. Returns (ok, output tail)."""
    code, out = _run(dep.install, timeout=timeout)
    tail = "\n".join(out.strip().splitlines()[-12:])
    dep.current = None
    return code == 0, tail


def version_tuple(v: str | None) -> tuple:
    if not v:
        return ()
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:4])