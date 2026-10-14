#!/usr/bin/env python3
"""Report every mismatch between this repository, the live install and the
machine - not just the first one.

A setup wizard that stops at the first problem makes people fix things one
round trip at a time, and each round trip costs a restart of the bridge. Worse,
several of the failures this project exists to prevent are silent: OpenCode
prints its help text and exits 1 on an unknown flag, so a wrong argument looks
exactly like a missing feature. Nothing reports itself. This module is the
place where all of it gets reported at once.

Design rules, all of them learned the hard way:

  - Read-only. It never writes, installs, stops or repairs. The repair commands
    are printed, so the person decides.
  - Never stops early. Every check runs even after one fails. A partial report
    is the thing this file exists to eliminate.
  - A check that crashes reports itself instead of taking the run down. An audit
    that vanishes when the machine is unusual is useless exactly when it is
    needed.
  - ASCII only. The Windows console here decodes as cp1252, and printing a
    non-ASCII character raises UnicodeEncodeError and kills the report midway.

    python tools/audit.py             # human report
    python tools/audit.py --json      # machine readable, for CI
    python tools/audit.py --only config,shim
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path(os.environ.get("USERPROFILE", str(pathlib.Path.home())))
BRIDGE_DIR = HOME / ".cc-connect"
SHIM_DIR = BRIDGE_DIR / "gigaam-stt"
NPM_SHIM_DIR = BRIDGE_DIR / "opencode-shim"
OC_CONFIG = HOME / ".config" / "opencode" / "opencode.jsonc"
NPM_BIN = pathlib.Path(os.environ.get("APPDATA", "")) / "npm"

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"
_SEV_ORDER = {FAIL: 0, WARN: 1, INFO: 2}


class Finding:
    __slots__ = ("check", "severity", "title", "detail", "fix")

    def __init__(self, check: str, severity: str, title: str,
                 detail: str = "", fix: str = ""):
        self.check = check
        self.severity = severity
        self.title = title
        self.detail = detail
        self.fix = fix

    def as_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity,
                "title": self.title, "detail": self.detail, "fix": self.fix}

    def __str__(self) -> str:
        head = f"[{self.severity}] {self.title}"
        if self.detail:
            head += f"\n         {self.detail}"
        if self.fix:
            head += f"\n         fix: {self.fix}"
        return head


def _run(cmd: str, timeout: int = 90, cwd: str | None = None) -> tuple[int, str]:
    exe = cmd.split()[0].strip('"').strip("'")
    resolved = shutil.which(exe) or (exe if exe.lower().endswith((".exe", ".cmd", ".bat")) else None)
    shell = bool(resolved and resolved.lower().endswith((".cmd", ".bat")))
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, timeout=timeout,
                           cwd=cwd)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:                                       # noqa: BLE001
        return 127, f"{type(e).__name__}: {e}"


def _json_from(text: str):
    """First JSON value in a buffer, ignoring whatever follows it.

    npm and opencode both interleave notices with their output. Demanding
    that the buffer end where the JSON ends turns a version banner into a
    silent "not installed", so stop at the end of the first value instead.

    Whichever bracket comes first decides, and it must be whichever comes
    first: searching for '{' first and falling back to '[' looks harmless but
    silently truncates '[{...}, {...}]' to its first element, so a list of
    sessions reads as a length of 1.
    """
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    except json.JSONDecodeError:
        return None
    return value


# ── 1. repository hygiene ────────────────────────────────────────────────

JUNK_DIRS = ("pip", "Microsoft", "__pycache__", "node_modules", ".pytest_cache")
BIG_FILE = 2 * 1024 * 1024
SECRET_HINTS = re.compile(r"\d{8,10}:AA[A-Za-z0-9_-]{30,}")

# The installer tests commit deliberately fake tokens. Flagging those teaches
# people to ignore the secret check, which is worse than not having it. A real
# bot token is 35 characters of base64url noise; it does not spell a word.
FIXTURE_MARKERS = ("fake", "example", "placeholder", "dummy", "test",
                   "changeme", "yourtoken", "xxxxx", "000000", "123456789")


def _is_fixture_token(token: str) -> bool:
    low = token.lower()
    return any(m in low for m in FIXTURE_MARKERS)


def check_repo_hygiene() -> list[Finding]:
    """Files that should never be in the repository.

    This is not tidiness. `pip/cache/http-v2/...` and
    `Microsoft/Windows/PowerShell/ModuleAnalysisCache` were committed in the
    very first commit: a tool was run with the repository as its working
    directory and wrote its cache here. A published repository that carries
    somebody's package cache tells every reader that this project does not
    notice what it puts in version control.
    """
    out: list[Finding] = []
    code, listing = _run("git ls-files")
    if code != 0:
        return out
    files = [f for f in listing.splitlines() if f.strip()]

    for junk in JUNK_DIRS:
        hits = [f for f in files if f.split("/")[0] == junk]
        if hits:
            out.append(Finding(
                "repo.hygiene", FAIL,
                f"{len(hits)} committed file(s) under {junk}/",
                f"first: {hits[0]}",
                f"git rm -r --cached {junk} && "
                f"rm -rf {junk}    # then add it to .gitignore"))

    big = []
    for f in files:
        p = ROOT / f
        try:
            if p.is_file() and p.stat().st_size > BIG_FILE:
                big.append((f, p.stat().st_size))
        except OSError:
            continue
    for f, size in big:
        out.append(Finding(
            "repo.hygiene", FAIL,
            f"committed file is {size // 1024 // 1024} MB",
            f,
            "large binaries must not be versioned; host them outside"))

    for f in files:
        p = ROOT / f
        if not p.is_file() or p.suffix.lower() in (".exe", ".dll", ".gguf", ".png", ".jpg"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = SECRET_HINTS.search(text)
        if m and f != "tools/audit.py" and not _is_fixture_token(m.group(0)):
            out.append(Finding(
                "repo.hygiene", FAIL,
                f"looks like a real Telegram bot token in {f}",
                "a token-shaped string with no fixture marker matched; "
                "if this is a real token, rotate it in BotFather",
                "remove the literal and use an environment variable"))

    return out


# ── 2. dependency versions ───────────────────────────────────────────────

def check_dependencies() -> list[Finding]:
    """Installed vs newest, straight from the official registries."""
    out: list[Finding] = []
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import deps                                              # noqa: PLC0415
    except Exception as e:                                       # noqa: BLE001
        return [Finding("dep.probe", WARN, "dependency checker unavailable", str(e))]

    try:
        for d in deps.probe_all():
            if not d.current:
                sev = FAIL if d.required and d.kind != "manual" else INFO
                out.append(Finding(
                    "dep.version", sev, f"{d.label} is not installed", d.why,
                    d.install))
                continue
            if d.latest and deps.version_tuple(d.current) < deps.version_tuple(d.latest):
                out.append(Finding(
                    "dep.version", WARN,
                    f"{d.label} {d.current} is behind {d.latest}",
                    d.why,
                    d.install))
    except Exception as e:                                       # noqa: BLE001
        out.append(Finding("dep.probe", WARN, "dependency probe failed", str(e)))
    return out


# ── 3. configuration coherence ───────────────────────────────────────────

def _load_toml(path: pathlib.Path):
    try:
        import tomllib                                          # noqa: PLC0415
    except ModuleNotFoundError:
        return None, "tomllib needs Python 3.11+"
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh), ""
    except Exception as e:                                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _load_jsonc(path: pathlib.Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def check_config() -> list[Finding]:
    """The live config against the rules that were each learned by breakage."""
    out: list[Finding] = []
    cfg_path = BRIDGE_DIR / "config.toml"

    if not cfg_path.exists():
        return [Finding("config.missing", INFO, "no live config yet",
                        "setup.py has not written one on this machine",
                        "python setup.py")]

    cfg, err = _load_toml(cfg_path)
    if cfg is None:
        return [Finding("config.parse", FAIL, "config.toml cannot be parsed",
                        err,
                        "cc-connect will refuse to start; fix or delete the file")]

    projects = cfg.get("projects")
    if projects is None:
        out.append(Finding("config.project", FAIL,
                           "config.toml declares no [[projects]]",
                           "there is no agent for the bridge to run"))
    elif isinstance(projects, dict):
        # [projects] rather than [[projects]]. TOML reads both happily, so
        # this is a silent mismatch rather than a parse error - and indexing
        # it as a list would have taken this whole check down with it.
        out.append(Finding(
            "config.project", FAIL,
            "config.toml uses [projects] where [[projects]] is required",
            "cc-connect expects an array of projects; a plain table is read as "
            "one unnamed project and the agent never starts",
            'change the header to [[projects]]'))
        proj = projects
    elif not isinstance(projects, list) or not projects:
        out.append(Finding("config.project", FAIL,
                           "config.toml has an unreadable [[projects]]",
                           f"got {type(projects).__name__}"))
        proj = None
    else:
        proj = projects[0]

    if proj:
        opts = ((proj.get("agent") or {}).get("options") or {})

        # `model` pinned here silently breaks /model switch.
        if opts.get("model"):
            out.append(Finding(
                "config.model", FAIL,
                "a model is pinned in the bridge config",
                f"model = {opts['model']!r} here overrides /model switch in chat; "
                f"the default belongs in {OC_CONFIG.name}",
                "delete the model line, then set it in "
                "~/.config/opencode/opencode.jsonc"))

        # cmd must be the bare binary: opencode.go:654 passes only cmd[0].
        cmd = opts.get("cmd")
        if isinstance(cmd, list) and len(cmd) != 1:
            out.append(Finding(
                "config.cmd", FAIL,
                f"cmd has {len(cmd)} element(s), not 1",
                "cc-connect splits cmd into binary + extraArgs but drops "
                "extraArgs when listing sessions, so /list runs the wrong "
                "program and exits 1",
                'set cmd = ["opencode"] and route through the npm shim'))

        # A nonzero idle reset makes Telegram and the desktop diverge.
        idle = proj.get("reset_on_idle_mins")
        if idle not in (0, None):
            out.append(Finding(
                "config.idle", WARN,
                f"reset_on_idle_mins = {idle}",
                "after that much silence Telegram starts a new session and "
                "loses context the desktop still has",
                "set reset_on_idle_mins = 0"))

        work_dir = opts.get("work_dir")
        if work_dir and not pathlib.Path(work_dir).exists():
            out.append(Finding(
                "config.work_dir", FAIL,
                "work_dir does not exist",
                work_dir,
                "point it at an existing folder"))

        # TTS switched on but nothing to synthesise with.
        tts = cfg.get("tts") or {}
        if tts.get("enabled") and (tts.get("provider") or "edge") == "edge":
            sys.path.insert(0, str(ROOT / "tools"))
            try:
                import deps                                      # noqa: PLC0415
                if not deps._installed_pip("edge-tts"):
                    out.append(Finding(
                        "config.tts", FAIL,
                        "tts is enabled but edge-tts is not installed",
                        "the provider shells out to the edge-tts binary",
                        "pip install --upgrade edge-tts"))
            except Exception:                                   # noqa: BLE001
                pass

        speech = cfg.get("speech") or {}
        base = ((speech.get("openai") or {}).get("base_url")) or ""
        if speech.get("enabled") and base:
            m = re.search(r":(\d+)", base)
            if m and not _port_open(int(m.group(1))):
                out.append(Finding(
                    "config.stt", FAIL,
                    f"speech is enabled but nothing is listening on port {m.group(1)}",
                    base,
                    "start the shim: "
                    f'powershell -File "{SHIM_DIR / "start-gigaam-stt.ps1"}"'))

    # Placeholders shipped in the template must not survive into the live file.
    text = cfg_path.read_text(encoding="utf-8", errors="replace")
    if "CHANGE-ME" in text:
        out.append(Finding(
            "config.placeholder", FAIL,
            "config.toml still contains a CHANGE-ME placeholder",
            "the bridge will start and then fail to reach Telegram",
            "run setup.py again and give a real token"))

    # The default model should agree across clients.
    if OC_CONFIG.exists():
        try:
            oc = _load_jsonc(OC_CONFIG)
            mdl = (oc.get("model") or "")
            if not mdl:
                out.append(Finding(
                    "config.default_model", WARN,
                    "opencode.jsonc pins no default model",
                    "Telegram and the desktop can then start on different models",
                    'add  "model": "opencode/space-bunny-free"'))
        except Exception as e:                                   # noqa: BLE001
            out.append(Finding(
                "config.default_model", WARN,
                "opencode.jsonc cannot be parsed", f"{type(e).__name__}: {e}"))

    return out


# ── 4. npm shim wiring ───────────────────────────────────────────────────

def check_shim() -> list[Finding]:
    """Is the routing actually in place, in every launcher npm installs?

    npm creates two launchers on Windows - `opencode.cmd` for CreateProcess
    and `opencode` for bash - and patching only one leaves a path where /list
    still breaks with no explanation.
    """
    out: list[Finding] = []
    launchers = [("opencode.cmd", "cmd.exe / CreateProcess"),
                 ("opencode", "bash / sh")]
    patched_any = False

    for name, used_by in launchers:
        p = NPM_BIN / name
        if not p.exists():
            out.append(Finding(
                "shim.launcher", WARN,
                f"npm launcher {name} not found", used_by,
                "npm install -g @opencode/cli"))
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:                                     # noqa: BLE001
            out.append(Finding("shim.launcher", WARN, f"cannot read {name}", str(e)))
            continue
        if "shim" in body.lower():
            patched_any = True
        else:
            out.append(Finding(
                "shim.unpatched", FAIL,
                f"{name} is not routed through the shim",
                f"{used_by} would call opencode directly, so /list breaks",
                'powershell -File '
                f'"{NPM_SHIM_DIR / "install-npm-shim.ps1"}"'))

    backups = list(NPM_BIN.glob("opencode*.orig-backup"))
    if backups and not patched_any:
        out.append(Finding(
            "shim.stale_backup", WARN,
            "shim backups exist but no launcher is patched",
            "a later `npm i -g @opencode/cli` regenerated them",
            'powershell -File '
            f'"{NPM_SHIM_DIR / "install-npm-shim.ps1"}"'))

    if NPM_SHIM_DIR.exists():
        for f in ("opencode-shim.js", "install-npm-shim.ps1"):
            if not (NPM_SHIM_DIR / f).exists():
                out.append(Finding(
                    "shim.missing", FAIL,
                    f"{f} is not in the live install",
                    str(NPM_SHIM_DIR / f),
                    "python setup.py"))
    elif patched_any:
        out.append(Finding(
            "shim.missing", FAIL,
            "npm launchers point at a shim that is not installed",
            str(NPM_SHIM_DIR / "opencode-shim.js"),
            "python setup.py"))

    return out


# ── 5. scheduled tasks ───────────────────────────────────────────────────

TASKS = [
    # name, working directory suffix, required trigger delay
    ("gigaam-stt-autostart", "gigaam-stt", "PT15S"),
    ("cc-connect-autostart", "", "PT20S"),
]


def check_tasks() -> list[Finding]:
    out: list[Finding] = []
    if os.name != "nt":
        return out

    seen = {}
    for name, wd_suffix, delay in TASKS:
        code, out_ps = _run(
            "powershell -NoProfile -Command "
            f"\"(Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue) "
            "| ForEach-Object { $_.Actions[0].WorkingDirectory + '|' + "
            "$_.Triggers[0].Delay }\"", timeout=90)
        if code != 0 or not out_ps.strip():
            out.append(Finding(
                "task.missing", INFO,
                f"scheduled task {name} is not registered",
                "the bridge will not come back after a reboot",
                'powershell -File '
                f'"{ROOT / "install-autostart.ps1"}"'))
            continue
        line = out_ps.strip().splitlines()[-1]
        wd, _, got_delay = line.partition("|")
        seen[name] = (wd.strip(), (got_delay or "").strip().upper())

        want_wd = str(BRIDGE_DIR / wd_suffix) if wd_suffix else str(BRIDGE_DIR)
        if wd.strip().lower() != want_wd.lower():
            out.append(Finding(
                "task.working_dir", FAIL,
                f"{name} runs in the wrong directory",
                f"is {wd.strip()!r}, want {want_wd!r}; the task then silently "
                f"fails to find its script",
                'powershell -File '
                f'"{ROOT / "install-autostart.ps1"}"'))

        if seen[name][1] != delay:
            out.append(Finding(
                "task.delay", WARN,
                f"{name} triggers {seen[name][1] or 'immediately'}, "
                f"expected {delay}",
                "the bridge must not start before the speech shim is listening",
                'powershell -File '
                f'"{ROOT / "install-autostart.ps1"}"'))

    # Ordering matters and neither delay alone proves it.
    if "gigaam-stt-autostart" in seen and "cc-connect-autostart" in seen:
        def seconds(v: str) -> int:
            m = re.match(r"PT(\d+)S", v)
            return int(m.group(1)) if m else 0
        if seconds(seen["gigaam-stt-autostart"][1]) >= \
           seconds(seen["cc-connect-autostart"][1]):
            out.append(Finding(
                "task.order", FAIL,
                "the bridge can start before the speech shim is listening",
                f"shim {seen['gigaam-stt-autostart'][1]} >= "
                f"bridge {seen['cc-connect-autostart'][1]}; the first voice "
                f"message after a reboot is then lost",
                'powershell -File '
                f'"{ROOT / "install-autostart.ps1"}'))

    return out


# ── 6. runtime state ─────────────────────────────────────────────────────

def _port_open(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_runtime() -> list[Finding]:
    out: list[Finding] = []

    for port, who in ((8300, "speech shim"), (9820, "management api")):
        if _port_open(port):
            out.append(Finding(
                "runtime.port", INFO,
                f"port {port} is in use", f"{who} appears to be running"))
        else:
            out.append(Finding(
                "runtime.port", INFO,
                f"port {port} is free", f"{who} is not running"))

    # Servers, not orphans.
    #
    # An earlier version of this check looked for a `serve --service` process
    # whose parent had died and called it an orphan to be killed. That is
    # wrong, and it was wrong in the dangerous direction: the desktop app
    # starts the shared server once and deliberately lets it outlive the app,
    # so a dead parent is its normal state. SKILL.md says of this process
    # "never kill it" - and the audit was telling people to `taskkill` it,
    # which on this machine was the process serving the live conversation.
    #
    # What actually matters is how many there are, because that is what
    # decides whether clients share a session:
    #   0 - nothing to talk to
    #   1 - healthy, whatever the parent situation looks like
    #   2+ - clients have split, which is the documented failure
    if os.name == "nt":
        code, out_ps = _run(
            "powershell -NoProfile -Command "
            "\"Get-CimInstance Win32_Process -Filter \\\"Name='opencode-cli.exe'\\\" "
            "| Where-Object { $_.CommandLine -match 'serve' } "
            "| ForEach-Object { $_.ProcessId.ToString() + '|' + $_.ParentProcessId }\"",
            timeout=90)
        servers = []
        for line in out_ps.splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                servers.append((int(parts[0]), int(parts[1])))

        if not servers:
            out.append(Finding(
                "runtime.server", FAIL,
                "no OpenCode server is running",
                "the bridge has nothing to attach to; /list will fail and "
                "every message will time out",
                "start the desktop app once, or run "
                f'"{(NPM_BIN / "opencode.cmd")}" serve'))
        elif len(servers) == 1:
            pid, ppid = servers[0]
            out.append(Finding(
                "runtime.server", INFO,
                f"one shared OpenCode server (pid {pid})",
                f"parent {ppid}"
                + (" - already gone, which is normal: the desktop starts this "
                   "server once and lets it outlive the app. Do not kill it."
                   if not _pid_alive(ppid) else "")))
        else:
            listed = ", ".join(str(p) for p, _ in servers)
            out.append(Finding(
                "runtime.server", FAIL,
                f"{len(servers)} OpenCode servers are running ({listed})",
                "clients have split: each server has its own session view, so "
                "the desktop and Telegram stop seeing each other's work",
                "close every OpenCode window, then start the desktop app once"))

    # How many sessions the server sees, compared with how many exist.
    #
    # Counting servers is necessary but not sufficient. One healthy server and
    # one stale one look identical from the outside, and the difference is
    # exactly the reported symptom: `/list` returns nothing while the database
    # is full. `--standalone` reads the database directly, so it is the
    # control. Measured here: the service answered [] while standalone
    # returned every session, including the one the Telegram bridge uses.
    #
    # Detecting this precisely matters because the usual advice has been "reboot
    # the machine". Now it can say which half of the comparison is wrong.
    counts = _session_counts()
    if counts:
        via_service, standalone = counts
        if standalone > 0 and via_service < standalone:
            out.append(Finding(
                "runtime.stale_server", FAIL,
                f"the shared server sees {via_service} of "
                f"{standalone} session(s)",
                "it is running but its view of the database is behind. "
                "`/list` in the chat comes back empty and looks like a "
                "misconfigured bridge rather than a stale process",
                f"restart the OpenCode server - not the bridge, and not the "
                f"machine:\n         powershell -File \"{ROOT / 'restart-server.ps1'}\""))
        elif standalone == 0 and via_service == 0:
            out.append(Finding(
                "runtime.sessions", INFO,
                "no sessions yet", "both views are empty"))

    return out


def _bridge_work_dir() -> str:
    """Where the bridge actually runs - sessions are grouped by directory.

    Asking from the repository directory answers a question nobody was
    holding: that folder legitimately has no sessions, so both views come
    back empty and the check reports nothing while the real project is
    unreadable.
    """
    cfg, err = _load_toml(BRIDGE_DIR / "config.toml")
    if cfg:
        projects = cfg.get("projects")
        proj = projects[0] if isinstance(projects, list) and projects else (
            projects if isinstance(projects, dict) else None)
        if proj:
            wd = ((proj.get("agent") or {}).get("options") or {}).get("work_dir")
            if wd and pathlib.Path(wd).is_dir():
                return str(wd)
    return str(ROOT)


def _session_counts() -> tuple[int, int] | None:
    """(via the shared server, via --standalone), or None if unmeasurable."""
    cwd = _bridge_work_dir()

    def count(extra: list[str]) -> int | None:
        code, out = _run(
            "opencode session list --format json " + " ".join(extra),
            timeout=180, cwd=cwd)
        data = _json_from(out)
        if not isinstance(data, list):
            return None
        return len(data)

    service = count([])
    standalone = count(["--standalone"])
    if service is None or standalone is None:
        return None
    return service, standalone


def _pid_alive(pid: int) -> bool:
    code, out = _run(f'tasklist /FI "PID eq {pid}"', timeout=30)
    return code == 0 and str(pid) in out and "no tasks" not in out.lower()


# ── 6b. version skew between the desktop and the CLI ──────────────────────

def check_version_skew() -> list[Finding]:
    """The desktop app and the npm CLI open the same database.

    The desktop ships its own opencode-cli.exe and upgrades it on its own
    schedule. The npm one only moves when a person runs `npm install -g`. So
    they drift without anybody deciding to let them, and the symptom is
    silent: a version that does not understand another's arguments prints
    its help text and exits 1, and a database written by one line of the
    2.x series may not be readable by the other.

    Severity follows the shape of the gap. A major or minor difference means
    different code lines, which is the case that destroyed sessions before -
    that is a FAIL. A patch difference is probably harmless, so it is a WARN
    and the fix is to catch up rather than to panic.
    """
    out: list[Finding] = []
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import deps                                              # noqa: PLC0415
    except Exception as e:                                       # noqa: BLE001
        return [Finding("skew.check", WARN, "skew checker unavailable", str(e))]

    copies = deps.opencode_copies()

    if len(copies) < 2:
        return out        # nothing to compare: CLI-only host

    versions = sorted(set(copies.values()))
    if len(versions) == 1:
        return out

    where = ", ".join(f"{k} {v}" for k, v in sorted(copies.items()))
    tuples = [deps.version_tuple(v) for v in versions]
    line_differs = len({t[:2] for t in tuples}) > 1
    newest = max(tuples)

    names = [k for k, v in copies.items() if deps.version_tuple(v) == newest]
    behind = [f"{k} {v}" for k, v in copies.items()
              if deps.version_tuple(v) != newest]

    out.append(Finding(
        "skew.desktop_cli",
        FAIL if line_differs else WARN,
        "opencode copies disagree: " + where,
        f"the newest is {names[0]}; behind it: {', '.join(behind) or 'none'}. "
        f"They all open {HOME / '.local' / 'share' / 'opencode' / 'opencode.db'}. "
        f"A version that does not understand another's arguments prints its "
        f"help and exits 1, which reads as a missing feature rather than as "
        f"a version problem",
        f"npm install -g @opencode/cli   # brings the bridge up to "
        f"{newest[0]}.{newest[1]}.{newest[2]}"
        if any("npm" in b for b in behind) else
        f"update the desktop app, or pin the npm CLI to "
        f"{versions[-1]} with `npm install -g @opencode/cli@{versions[-1]}`"))
    return out


# ── 7. repository vs live install ────────────────────────────────────────

def check_drift() -> list[Finding]:
    out: list[Finding] = []
    try:
        # The filename contains a hyphen, so it cannot be imported by name.
        # Loading it by path is the only way to reuse it without renaming a
        # file that other tools and CI already refer to.
        import importlib.util                                    # noqa: PLC0415
        spec = importlib.util.spec_from_file_location(
            "check_drift", ROOT / "tools" / "check-drift.py")
        if spec is None or spec.loader is None:
            raise ImportError("cannot load check-drift.py")
        check_drift = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_drift)
    except Exception as e:                                       # noqa: BLE001
        return [Finding("drift.check", WARN, "drift checker unavailable", str(e))]
    if not check_drift.LIVE.exists():
        return out
    for repo_rel, live_rel in check_drift.PAIRS:
        r, l = ROOT / repo_rel, check_drift.LIVE / live_rel
        if not r.exists():
            continue
        if not l.exists():
            out.append(Finding(
                "drift.missing", FAIL,
                f"{live_rel} is not installed",
                "the repo has it, the running copy does not",
                "python setup.py"))
        elif not check_drift.same(r, l):
            out.append(Finding(
                "drift.file", FAIL,
                f"{live_rel} differs from the repo",
                "editing the repo changes nothing until this is synced",
                "python tools/check-drift.py --sync"))
    return out


# ── 6c. session storage ──────────────────────────────────────────────────

# A session with almost no turns is not automatically rubbish, and this file
# deletes nothing on that basis. The threshold below is only used to sort the
# list so the likely noise is visible first; the decision is the reader's.
TINY_SESSION_MSGS = 5


def check_sessions() -> list[Finding]:
    """Summarise the session table; never delete anything.

    Opened read-only via URI mode, so a bug here cannot modify the database.
    Nothing is removed: a three-turn session may be a throwaway from a
    contract test, or the only record of a real question. Deciding that from
    a row count is not this tool's call to make.
    """
    out: list[Finding] = []
    db = HOME / ".local" / "share" / "opencode" / "opencode.db"
    if not db.exists():
        return out

    try:
        import sqlite3                                        # noqa: PLC0415
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "select coalesce(s.title, '(untitled)'),"
            "       (select count(*) from session_message m"
            "         where m.session_id = s.id)"
            "  from session_v2 s order by 2 asc limit 400"
        ).fetchall()
        total = con.execute("select count(*) from session_v2").fetchone()[0]
    except Exception as e:                                   # noqa: BLE001
        return [Finding("sessions.read", WARN,
                        "could not read the session database",
                        f"{type(e).__name__}: {e}")]

    tiny = [r for r in rows if r[1] < TINY_SESSION_MSGS]
    out.append(Finding(
        "sessions.summary", INFO,
        f"{total} session(s) stored, {len(tiny)} with fewer than "
        f"{TINY_SESSION_MSGS} messages",
        f"database: {db}"))

    if tiny:
        shown = ", ".join(f"{t[:28]} ({n})" for t, n in tiny[:8])
        more = f" and {len(tiny) - 8} more" if len(tiny) > 8 else ""
        out.append(Finding(
            "sessions.noise", WARN if len(tiny) > 10 else INFO,
            f"{len(tiny)} very short session(s), probably test leftovers",
            f"{shown}{more}",
            f"remove them in the desktop app, or "
            f"opencode session delete <id> - this tool deletes nothing"))

    out.extend(_check_projects(con))
    return out


def _check_projects(con) -> list[Finding]:
    """Report directories registered more than once, and test-run leftovers.

    A duplicate is worth knowing about because sessions are counted per
    project, so two rows for one directory mean a query that resolves to the
    wrong row sees none of them.

    It is NOT claimed to be the cause of the empty `/list`: the server log
    points at the populated row. It is a real anomaly on its own, and saying
    so plainly is better than a tidy story that turns out to be wrong.
    """
    out: list[Finding] = []
    try:
        dupes = con.execute(
            "select p.worktree, count(*) rows, sum("
            "  (select count(*) from session_v2 s where s.project_id = p.id)"
            ") as total"
            "  from project p group by p.worktree having count(*) > 1"
        ).fetchall()
        leftovers = con.execute(
            "select count(*) from project p where p.worktree like '%cc-shim-contract%'"
            "   or p.worktree like '%\\tmp.%' or p.worktree like '%\\opencode'"
        ).fetchone()[0]
    except Exception:                                          # noqa: BLE001
        return out

    for worktree, _rows, sess in dupes:
        out.append(Finding(
            "sessions.duplicate_project", WARN,
            f"{worktree} is registered {_rows} times",
            f"{sess} session(s) in total across those rows; a lookup that "
            f"resolves to the other one sees none of them",
            f"this tool changes nothing. The rows are visible with: "
            f"sqlite3 \"{HOME / '.local' / 'share' / 'opencode' / 'opencode.db'}\" "
            f"\"select id, worktree from project\""))

    if leftovers:
        out.append(Finding(
            "sessions.test_projects", INFO,
            f"{leftovers} project row(s) left by test runs",
            "each contract-test run registered its temporary directory as a "
            "project. They accumulate even though the sessions are deleted",
            f"remove them in the desktop app if the list is noisy"))
    return out


# ── registry ─────────────────────────────────────────────────────────────

CHECKS = [
    ("repo", check_repo_hygiene),
    ("dep", check_dependencies),
    ("config", check_config),
    ("shim", check_shim),
    ("task", check_tasks),
    ("runtime", check_runtime),
    ("skew", check_version_skew),
    ("sessions", check_sessions),
    ("drift", check_drift),
]


def collect(only: str | None = None) -> list[Finding]:
    wanted = {s.strip() for s in only.split(",")} if only else None
    findings: list[Finding] = []
    for name, fn in CHECKS:
        if wanted and name not in wanted:
            continue
        try:
            findings.extend(fn())
        except Exception as e:                                   # noqa: BLE001
            findings.append(Finding(
                f"{name}.crashed", WARN,
                f"check '{name}' could not finish",
                f"{type(e).__name__}: {e}",
                "this is a bug in tools/audit.py, not in your install"))
    findings.sort(key=lambda f: (_SEV_ORDER.get(f.severity, 9), f.check))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="report every mismatch")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--only", default="", help="comma separated check groups")
    args = ap.parse_args()

    findings = collect(args.only or None)

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        if not findings:
            print("no mismatches found")
        else:
            for f in findings:
                print(f)
                print()
        counts = {s: sum(1 for f in findings if f.severity == s)
                  for s in (FAIL, WARN, INFO)}
        print(f"{counts[FAIL]} failed, {counts[WARN]} warnings, "
              f"{counts[INFO]} notes")

    return 1 if any(f.severity == FAIL for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())