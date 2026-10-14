#!/usr/bin/env python3
"""Interactive setup for the cc-connect + OpenCode bridge.

A step-by-step installer in the usual sense: it checks what is present,
asks only for what it cannot determine, writes the config, installs the
wiring, registers autostart, and verifies.

    python setup.py

Non-interactive use (CI, throwaway boxes):

    python setup.py --yes --project-dir C:\\src\\my-app --token 123:ABC --name demo

Everything is optional except the project directory and the bot token.
Voice input and voice output are opt-in and degrade gracefully: without
them the bridge still works as a text bot.

Standard library only, on purpose: this runs before anything else is set up.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# ── hard prerequisite, checked before anything else ────────────────────────
# tomllib is 3.11+. Importing it unguarded turns an old Python into a bare
# ImportError traceback, which tells a first-time user nothing. Check before
# the import so this is the message they actually see.
if sys.version_info < (3, 11):
    sys.stderr.write(
        "\n"
        "  setup.py needs Python 3.11 or newer.\n"
        f"  You have {sys.version_info.major}.{sys.version_info.minor}.\n"
        "\n"
        "  Install a newer one, then run this again:\n"
        "      winget install Python.Python.3.12\n"
        "\n"
        "  The bridge itself does not need 3.11 - only this installer does,\n"
        "  because it reads back the TOML it wrote to verify it.\n\n"
    )
    raise SystemExit(1)

import tomllib                                                    # noqa: E402
from pathlib import Path                                          # noqa: E402

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("USERPROFILE", str(Path.home())))

BRIDGE_DIR = HOME / ".cc-connect"
SHIM_DIR = BRIDGE_DIR / "gigaam-stt"
OC_CONFIG = HOME / ".config" / "opencode" / "opencode.jsonc"
NPM_BIN = Path(os.environ.get("APPDATA", "")) / "npm"
HANDY = Path(os.environ.get("LOCALAPPDATA", "")) / "Handy" / "handy.exe"

IS_WIN = os.name == "nt"

# ── console ────────────────────────────────────────────────────────────────
# Force UTF-8 up front. The Windows console defaults to a legacy code page
# here and printing anything non-ASCII raises UnicodeEncodeError mid-install,
# which is a miserable way to fail.

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001
        pass

OK = "\033[92m"
NO = "\033[91m"
WARN = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
OFF = "\033[0m"


def _c(text: str, colour: str) -> str:
    return text if not sys.stdout.isatty() else f"{colour}{text}{OFF}"


# ── helpers ────────────────────────────────────────────────────────────────

def run(cmd: str, timeout: int = 120, shell: bool | None = None) -> tuple[int, str]:
    """Run a command, return (exit_code, combined_output). Never raises.

    Windows cannot CreateProcess a .cmd batch file, and npm-installed tools
    on PATH are exactly that. Resolve the executable first, and go through
    cmd.exe when we land on a batch launcher.
    """
    if shell is None:
        exe = cmd.split()[0].strip('"')
        resolved = shutil.which(exe)
        shell = bool(IS_WIN and resolved and
                     resolved.lower().endswith((".cmd", ".bat")))
    try:
        r = subprocess.run(
            cmd, shell=shell, capture_output=True, timeout=timeout,
            cwd=str(ROOT),
        )
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:                                   # noqa: BLE001
        return 127, str(e)


def heading(text: str) -> None:
    print()
    print(_c(text, BOLD))


def ok(text: str) -> None:
    print(f"  {_c('OK', OK)}    {text}")


def no(text: str) -> None:
    print(f"  {_c('--', NO)}    {text}")


def warn(text: str) -> None:
    print(f"  {_c('!!', WARN)}    {text}")


def info(text: str) -> None:
    print(f"        {_c(text, DIM)}")


def rule(char: str = "-", width: int = 64) -> str:
    return char * width


def ask(prompt: str, default: str = "") -> str:
    suffix = f" {_c('[' + default + ']', DIM)}" if default else ""
    try:
        raw = input(f"  {prompt}{suffix}> ").strip()
    except EOFError:
        return default
    return raw or default


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = ask(f"{prompt} ({hint})", "").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "д", "да"):
            return True
        if raw in ("n", "no", "н", "нет"):
            return False


def ask_choice(title: str, options: list[str], default_index: int = 0) -> int:
    print()
    print(f"  {title}")
    width = len(str(len(options)))
    for i, opt in enumerate(options):
        mark = _c("  << default", DIM) if i == default_index else ""
        print(f"   {_c(str(i + 1).rjust(width) + ')', DIM)}  {opt}{mark}")
    while True:
        raw = ask("choose", str(default_index + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        warn(f"enter a number from 1 to {len(options)}")


# ── steps ──────────────────────────────────────────────────────────────────

def step_dependencies() -> dict:
    """Check every dependency against its official registry, offer to update.

    Every version comes from the registry itself (npm view, pip index, winget
    list), never from a number typed into this file. And nothing is installed
    without asking: a machine-wide ffmpeg install may want elevation, and
    silently running an installer is how people lose trust in a setup wizard.
    """
    heading(f"[1/9] {_c('Checking dependencies', BOLD)}")
    print()

    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import deps                                            # noqa: PLC0415
    except Exception as e:                                       # noqa: BLE001
        no(f"dependency checker unavailable: {e}")
        return {"opencode": False, "cc": False, "ffmpeg": False,
                "edge_tts": False, "handy": False, "stt_model": False}

    print(f"  {_c('querying official registries', DIM)}")
    results = deps.probe_all()

    state = {}
    updates = []

    for d in results:
        state[d.key] = bool(d.current)
        if not d.current:
            if d.kind == "manual":
                warn(f"{d.label} not found - {d.why}")
                info(f"    install from {d.install}")
            else:
                no(f"{d.label} not installed - {d.why}")
        elif d.latest and deps.version_tuple(d.current) < deps.version_tuple(d.latest):
            warn(f"{d.label} {d.current} -> {d.latest} available")
            updates.append(d)
        else:
            ok(f"{d.label} {d.current}")

    # ---- offer updates -------------------------------------------------
    if updates:
        print()
        for d in updates:
            if d.safe_auto and ask_yes_no(f"update {d.label} to {d.latest}?", default=True):
                info(f"    running: {d.install}")
                good, tail = deps.install(d, timeout=900)
                if good:
                    d.current = deps.probe(d).current or d.latest
                    ok(f"{d.label} now {d.current}")
                else:
                    no(f"{d.label} update failed")
                    for line in tail.splitlines()[-4:]:
                        info("    " + line)
        for d in updates:
            if not d.safe_auto:
                # Already reported above; repeat only the command, so the
                # warning is not printed twice for the same thing.
                print(f"    {d.label}: {d.install}")

    # ---- offer missing installs ---------------------------------------
    print()
    for d in results:
        if d.current or d.kind == "manual":
            continue
        if not d.safe_auto:
            no(f"{d.label} is required and is missing")
            info(f"    {d.install}")
            if not args_noninteractive_default():
                input("")
            continue
        if ask_yes_no(f"install {d.label} now? ({d.install})", default=True):
            good, tail = deps.install(d, timeout=900)
            if good:
                d.current = deps.probe(d).current
                ok(f"{d.label} installed")
                state[d.key] = True
            else:
                no(f"{d.label} install failed")
                for line in tail.splitlines()[-4:]:
                    info("    " + line)

    # ---- speech model --------------------------------------------------
    if state.get("handy"):
        code, out = run(f'"{HANDY}" --list-models --json', timeout=240)
        count = len(re.findall(r'"is_downloaded":\s*true', out))
        state["stt_model"] = count > 0
        if count:
            ok(f"{count} speech model(s) downloaded")
        else:
            warn("Handy is installed but no speech model is downloaded yet")
            info("    open Handy once and download a model - GigaAM handles Russian well")

    return state


def args_noninteractive_default() -> bool:
    return False


def acquire_lock():
    """Refuse a second concurrent run.

    Two installers writing ~/.cc-connect at once produce a config that is
    half of each, and the symptom appears later in a file nobody is looking at.
    """
    lock = BRIDGE_DIR / ".setup.lock"
    try:
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(lock.read_text(encoding="utf-8").strip() or "0")
            age = time.time() - lock.stat().st_mtime
        except (ValueError, OSError):
            pid, age = 0, 0
        if pid and _pid_alive(pid):
            print()
            no(f"another setup.py is already running (PID {pid})")
            info("wait for it to finish, or delete this file if it crashed:")
            info(f"  {lock}")
            raise SystemExit(1)
        if age < 600:
            print()
            no("a previous setup.py crashed less than 10 minutes ago")
            info("its lock file may still be there:")
            info(f"  {lock}")
            if not ask_yes_no("continue anyway", default=False):
                raise SystemExit(1)
        try:
            lock.unlink()
        except OSError:
            pass
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def release_lock(lock) -> None:
    try:
        if lock and lock.exists():
            lock.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if not IS_WIN:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{'y'}}"],
        capture_output=True,
    )
    return b"y" in out.stdout


def step_preflight() -> bool:
    heading(_c("Preflight", BOLD))
    print()

    # ports the install depends on
    busy = []
    for port, who in ((8300, "GigaAM STT shim"), (9820, "cc-connect management")):
        code, out = run(f"powershell -NoProfile -Command \""
                        f"(Get-NetTCPConnection -State Listen -LocalPort {port} "
                        f"-ErrorAction SilentlyContinue).Count\"", timeout=30)
        count = out.strip().splitlines()[-1].strip() if out.strip() else "0"
        if count.isdigit() and int(count) > 0:
            warn(f"port {port} already in use ({who})")
            busy.append(port)
        else:
            ok(f"port {port} free")

    # disk: the speech model is a few hundred MB
    try:
        free = shutil.disk_usage(str(BRIDGE_DIR)).free
        if free > 2 * 1024 ** 3:
            ok(f"{free // 1024**3} GB free")
        else:
            warn(f"only {free // 1024**2} MB free - the speech model may not fit")
    except OSError as e:
        warn(f"could not check free space: {e}")

    # a bridge that is already running will keep the old config until restarted
    code, out = run("tasklist /FI \"IMAGENAME eq cc-connect.exe\"", timeout=30)
    if "cc-connect.exe" in out:
        warn("a cc-connect bridge is already running")
        info("it will keep using the old config until restarted; re-run /restart afterwards")

    if busy:
        print()
        no(f"ports in use: {', '.join(map(str, busy))}")
        if not ask_yes_no("continue anyway", default=False):
            return False
    return True


def verify_token_against_telegram(token: str) -> tuple[bool, str]:
    """Ask Telegram who this bot is. A regex cannot catch a typo; the API can.

    Returns (ok, detail). A network failure is reported as "could not check"
    rather than "bad token" - refusing to install because the machine is
    behind a firewall would be worse than the typo it guards against.
    """
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        import json as _json
        import urllib.error
        import urllib.request

        with urllib.request.urlopen(url, timeout=15) as r:
            data = _json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Telegram rejected it - the token is wrong or revoked"
        return False, f"Telegram answered HTTP {e.code}"
    except Exception as e:                                       # noqa: BLE001
        return True, f"could not verify ({type(e).__name__}) - assuming it is right"

    if not data.get("ok"):
        return False, str(data.get("description", "rejected"))
    bot = data.get("result", {}).get("username", "?")
    return True, f"valid, bot is @{bot}"


def step_telegram(noninteractive: bool, token: str) -> str | None:
    heading(f"[2/9] {_c('Telegram bot', BOLD)}")
    print()
    print("  The bridge talks to Telegram through a bot.")
    print("  Create one with @BotFather in Telegram, it will give you a token.")
    print()
    if noninteractive and token:
        ok("token supplied on the command line")
        return token
    if token:
        ok("token supplied")
        return token

    while True:
        value = ask("bot token")
        if not value:
            if ask_yes_no("skip Telegram and set the bridge up without it", default=False):
                return None
            continue
        # 1234567890:AA... - shape first, then ask the API, because a regex
        # cannot tell a valid token from one with a single wrong character.
        if not re.match(r"^\d{6,}:[A-Za-z0-9_-]{30,}$", value):
            warn("that does not look like a bot token (expected 123456789:AA...)")
            if ask_yes_no("use it anyway", default=False):
                return value
            continue

        print("        checking with Telegram...", flush=True)
        good, detail = verify_token_against_telegram(value)
        if good:
            ok(f"token accepted ({detail})")
            return value
        no(f"Telegram rejected the token: {detail}")
        if ask_yes_no("use it anyway", default=False):
            return value
        continue


def step_project(noninteractive: bool, project_dir: str) -> Path:
    heading(f"[3/9] {_c('Project directory', BOLD)}")
    print()
    print("  OpenCode runs with this as its working directory. Sessions are")
    print("  grouped by folder, so pick the real project - not a scratch dir,")
    print("  or the desktop app will not show the session.")
    print()

    default = (project_dir or str(ROOT)).strip()
    while True:
        raw = ask("project directory", default).strip()
        if not raw:
            no("a directory is required")
            continue

        p = Path(raw).expanduser()
        if not p.is_absolute():
            # A bare word like "y" or "yes" is a typo, not a path. Never
            # resolve it against the CWD and never offer to create it: that is
            # how a stray directory ends up next to the installer.
            no(f"not an absolute path: {p}")
            info("use a full path, e.g. C:\\src\\my-app")
            continue

        if p.exists() and p.is_dir():
            ok(str(p))
            return p.resolve()

        no(f"not a directory: {p}")
        if not (p.parent.exists() and p.parent.is_dir()):
            no(f"parent directory does not exist: {p.parent}")
            continue
        if ask_yes_no("create it", default=True):
            try:
                p.mkdir(parents=False, exist_ok=False)
                ok(f"created {p}")
                return p.resolve()
            except OSError as e:
                no(str(e))


def step_model(noninteractive: bool, state: dict) -> tuple[str, list[str]]:
    heading(f"[4/9] {_c('Model', BOLD)}")
    print()
    print("  Free models from the opencode gateway. No API key required.")
    print()

    code, out = run("opencode models", timeout=180)
    live = sorted({l.strip() for l in out.splitlines() if l.strip().startswith("opencode/")})

    alias_of = {}
    cfg_path = ROOT / "config.example.toml"
    if cfg_path.exists():
        try:
            data = tomllib.load(cfg_path.open("rb"))
            for m in data["projects"][0]["agent"]["providers"][0]["models"]:
                alias_of[m["model"]] = m["alias"]
        except Exception:                                    # noqa: BLE001
            pass

    if live:
        options = [f"{alias_of.get(m, '?'):<12}  {m}" for m in live]
        default_index = live.index("opencode/space-bunny-free") \
            if "opencode/space-bunny-free" in live else 0
        if noninteractive:
            chosen = live[default_index]
            ok(f"default {chosen}")
        else:
            idx = ask_choice("which model should be the default?", options, default_index)
            chosen = live[idx]
        ok(f"default model: {chosen}")
    else:
        no("could not list models - using space-bunny-free")
        chosen = "opencode/space-bunny-free"

    return chosen, live


def step_name(noninteractive: bool, project: Path, name: str) -> str:
    heading(f"[5/9] {_c('Session name', BOLD)}")
    print()
    print("  Labels this bridge's session. Used by cc-connect -p and by")
    print("  /new, so it shows up in cron and relay commands.")
    print()
    default = name or re.sub(r"[^A-Za-z0-9_-]+", "-", project.name).strip("-") or "cc-connect"
    while True:
        value = ask("session name", default)
        if re.match(r"^[A-Za-z0-9_.-]{2,64}$", value):
            ok(value)
            return value
        warn("use 2-64 characters: letters, digits, dot, dash, underscore")


def step_voice(state: dict) -> tuple[bool, bool]:
    heading(f"[6/9] {_c('Voice input', BOLD)}  {_c('(speech to text)', DIM)}")
    print()

    if not state.get("handy"):
        no("Handy not installed - cannot transcribe locally")
        info("bridge: npm i -g cc-connect; Handy: https://github.com/handy-computer")
        return False, False

    if not state.get("stt_model"):
        warn("no speech model downloaded yet")
        info("open Handy once and download a model (GigaAM works well for Russian)")

    enable = ask_yes_no("enable local voice input?", default=state.get("stt_model", False))
    if enable:
        ok("voice messages will be transcribed locally")
    return enable, bool(enable)


def step_tts(state: dict) -> bool:
    heading(f"[7/9] {_c('Voice output', BOLD)}  {_c('(text to speech)', DIM)}")
    print()
    print("  provider 'edge' is Microsoft's endpoint. No API key, no cost.")
    print()

    if not state.get("edge_tts"):
        no("edge-tts not installed - run: pip install edge-tts")
        return False

    enable = ask_yes_no("enable voice replies? (they are off until /tts always)", default=False)
    return enable


def step_autostart_question() -> bool:
    heading(f"[8/9] {_c('Autostart at logon', BOLD)}")
    print()
    print("  Two scheduled tasks so the bridge and the voice shim come back")
    print("  after a reboot. The shim starts first (15s), the bridge second")
    print("  (20s), so a voice message cannot arrive before the thing that")
    print("  transcribes it is listening.")
    print()
    return ask_yes_no("start them automatically after login?", default=True)


def step_write(args, project: Path, session_name: str, token: str | None,
               model: str, stt: bool, tts: bool) -> Path:
    heading(f"[9/9] {_c('Writing configuration', BOLD)}")
    print()

    template = ROOT / "config.example.toml"
    if not template.exists():
        no("config.example.toml is missing - cannot continue")
        sys.exit(1)

    text = template.read_text(encoding="utf-8")
    text = text.replace("<PROJECT_DIR>", project.as_posix())

    if token:
        text = re.sub(r'token\s*=\s*"CHANGE-ME-your-telegram-bot-token"', f'token = "{token}"', text)
        text = re.sub(r'token\s*=\s*"CHANGE-ME-management-token"',
                      'token = "local-only"', text)
    else:
        # no bot: leave the platform block commented so the config still loads
        text = re.sub(r'(?m)^token\s*=\s*"CHANGE-ME-your-telegram-bot-token".*$',
                      '# token = "<your bot token>"   # no bot configured by setup.py', text)

    text = re.sub(r'(?m)^name\s*=\s*"my-project"', f'name = "{session_name}"', text)

    # speech / tts toggles: first matching occurrence inside each block
    if not stt:
        text = re.sub(r'(?ms)(\[speech\]\s*\n\s*enabled\s*=\s*)true', r'\1false', text)
    if tts:
        text = re.sub(r'(?ms)(\[tts\]\s*\n\s*enabled\s*=\s*)false', r'\1true', text)

    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = BRIDGE_DIR / "config.toml"
    previous = None
    if cfg.exists():
        previous = cfg.read_text(encoding="utf-8")
        stamp = str(int(time.time()))
        backup = BRIDGE_DIR / f"config.toml.before-setup-{stamp}.bak"
        shutil.copy2(cfg, backup)
        info(f"existing config backed up to {backup.name}")
    cfg.write_text(text, encoding="utf-8", newline="")

    # Silent regex substitution is how the wizard produced a wrong work_dir and
    # a flipped speech flag without a word. Verify every value actually landed.
    #
    # The verification itself must not be able to throw. A renamed key used to
    # raise KeyError halfway through, leaving the bad file on disk with no
    # message at all. Failing to check counts as failing to verify.
    problems = []

    def rollback() -> None:
        try:
            if previous is not None:
                cfg.write_text(previous, encoding="utf-8", newline="")
                info("rolled back to the config that was there before")
            else:
                cfg.unlink()
                info("removed the config we had just written")
        except OSError as e:
            no(f"rollback failed: {e}")
            info(f"restore by hand from: {BRIDGE_DIR}")

    try:
        written = tomllib.loads(cfg.read_text(encoding="utf-8"))
        wp = written["projects"][0]
        opts = wp["agent"]["options"]

        def expect(cond: bool, msg: str) -> None:
            if not cond:
                problems.append(msg)

        expect(wp.get("name") == session_name,
               f"session name is {wp.get('name')!r}, wanted {session_name!r}")
        expect("work_dir" in opts,
               "config has no work_dir key - the template and the wizard disagree")
        if "work_dir" in opts:
            expect(Path(opts["work_dir"]) == project,
                   f"work_dir is {opts['work_dir']!r}, wanted {project.as_posix()!r}")
        expect(opts.get("cmd") == ["opencode"],
               f"cmd is {opts.get('cmd')!r}, must be a bare binary")
        expect(wp.get("reset_on_idle_mins") == 0, "reset_on_idle_mins must be 0")

        tts_on = bool(written.get("tts", {}).get("enabled"))
        expect(tts_on == tts, f"tts.enabled is {tts_on}, wanted {tts}")

        stt_on = bool(written.get("speech", {}).get("enabled"))
        expect(stt_on == stt, f"speech.enabled is {stt_on}, wanted {stt}")

        if token:
            got = wp.get("platforms", [{}])[0].get("options", {}).get("token")
            expect(got == token, f"telegram token did not land (got {str(got)[:12]}...)")
        else:
            expect("CHANGE-ME" not in cfg.read_text(encoding="utf-8"),
                   "a placeholder token was left in the config")

    except Exception as e:                                    # noqa: BLE001
        problems.append(f"could not verify what was written: {type(e).__name__}: {e}")

    if problems:
        for p in problems:
            no(p)
        print()
        # A half-written config is worse than no config: the bridge would start
        # against it and fail somewhere unrelated. Put back what was there.
        rollback()
        print()
        no(f"{cfg} did not match your answers - nothing was left in place")
        return False

    ok(f"{cfg}  (verified against your answers)")

    # shared OpenCode config: the default model for every client
    OC_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if OC_CONFIG.exists() and not args.force:
        try:
            existing = tomllib  # noqa: F841 - keep import used for clarity
            cur = json.loads(re.sub(r",(\s*[}\]])", r"\1", OC_CONFIG.read_text(encoding="utf-8")))
            if cur.get("model"):
                info(f"OpenCode config already sets model={cur['model']} - left alone")
            else:
                raise ValueError
        except Exception:                                    # noqa: BLE001
            OC_CONFIG.unlink()
    if not OC_CONFIG.exists():
        OC_CONFIG.write_text(json.dumps({
            "shell": "bash" if not IS_WIN else "bash",
            "model": model,
            "agent": {"build": {"model": model},
                      "general": {"model": model},
                      "plan": {"model": model}},
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ok(f"{OC_CONFIG}  (model = {model})")

    # launchers
    for src, dst in [(ROOT / "start-cc-connect.ps1", BRIDGE_DIR / "start-cc-connect.ps1"),
                     (ROOT / "gigaam-stt" / "server.py", SHIM_DIR / "server.py"),
                     (ROOT / "gigaam-stt" / "start-gigaam-stt.ps1", SHIM_DIR / "start-gigaam-stt.ps1")]:
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            ok(f"{dst}")

    return cfg


def step_install_shim() -> None:
    heading(_c("Installing the OpenCode compatibility shim", BOLD))
    print()
    print("  cc-connect 1.5.0 calls opencode in ways 2.0.28 rejects. Without")
    print("  this step /list fails with a bare 'exit status 1'.")
    print()
    script = ROOT / "opencode-shim" / "install-npm-shim.ps1"
    if not script.exists():
        no("install-npm-shim.ps1 missing")
        return
    if not IS_WIN:
        warn("this step is Windows-only; configure cmd = [\"opencode\"] manually")
        return
    code, out = run(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}"', timeout=180)
    if code == 0 and "Wiring intact" in out:
        ok("npm launchers now route through the shim")
    else:
        no("shim install did not verify")
        info(out.strip()[-300:])


def step_autostart(enable: bool) -> None:
    heading(_c("Autostart at logon", BOLD))
    print()
    if not enable:
        info("skipped")
        return
    if not IS_WIN:
        warn("scheduled tasks are Windows-only")
        return
    script = ROOT / "install-autostart.ps1"
    if not script.exists():
        no("install-autostart.ps1 missing")
        return
    code, out = run(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}"', timeout=180)
    if code == 0:
        for line in out.splitlines():
            if "registered" in line:
                ok(line.strip())
    else:
        no("could not register the scheduled tasks")
        info(out.strip()[-300:])


def step_verify(fresh: bool) -> None:
    heading(_c("Verifying", BOLD))
    print()
    test = ROOT / "autostart-test.ps1"
    if not test.exists():
        info("autostart-test.ps1 not present, skipping")
        return
    if not IS_WIN:
        info("autotest is Windows-only, skipping")
        return
    # Fresh mode: the services may never have been started, so "absent" is
    # expected rather than broken.
    extra = " -Fresh" if fresh else ""
    code, out = run(
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{test}" -SkipSimulation{extra}',
        timeout=600)
    tail = [l for l in out.splitlines() if any(k in l for k in
            ("passed", "warnings", "failures", "VERDICT", "[FAIL]", "[WARN]"))]
    for line in tail[-14:]:
        stripped = line.rstrip()
        if "[FAIL]" in stripped:
            print(f"  {_c('[FAIL]', NO)}   {stripped.split(']', 1)[-1].strip()}")
        elif "[WARN]" in stripped:
            print(f"  {_c('[WARN]', WARN)}   {stripped.split(']', 1)[-1].strip()}")
        else:
            print(f"        {_c(stripped.strip(), DIM)}")

    if code != 0:
        warn("autotest reported failures - see above")
        print()
        info("most common cause: run opencode-shim/install-npm-shim.ps1")
    elif fresh:
        print()
        info("start the services, then re-run autostart-test.ps1 without -Fresh")
    tail = [l for l in out.splitlines() if any(k in l for k in
            ("passed", "warnings", "failures", "VERDICT", "[FAIL]", "[WARN]"))]
    for line in tail[-14:]:
        stripped = line.rstrip()
        if "[FAIL]" in stripped:
            print(f"  {_c('[FAIL]', NO)}   {stripped.split(']', 1)[-1].strip()}")
        elif "[WARN]" in stripped:
            print(f"  {_c('[WARN]', WARN)}   {stripped.split(']', 1)[-1].strip()}")
        else:
            print(f"        {_c(stripped.strip(), DIM)}")

    if code != 0:
        warn("autotest reported failures - see above")
        print()
        info("most common cause: run opencode-shim/install-npm-shim.ps1")


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Interactive setup")
    ap.add_argument("--yes", action="store_true", help="accept all defaults")
    ap.add_argument("--project-dir", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--autostart", action="store_true", help="register logon tasks")
    ap.add_argument("--no-autostart", action="store_true",
                    help="skip the autostart question and do not register tasks "
                         "(used by the installer test, which must not touch this machine)")
    ap.add_argument("--force", action="store_true", help="overwrite opencode.jsonc")
    ap.add_argument("--dry-run", action="store_true",
                    help="ask everything and print the summary, change nothing")
    args = ap.parse_args()

    print()
    print(rule("="))
    print(f"  {_c('cc-connect + OpenCode bridge', BOLD)}")
    print(f"  {_c('interactive setup', DIM)}")
    print(rule("="))

    lock = None
    try:
        lock = acquire_lock()
        state = step_dependencies()
        if not step_preflight():
            info("stopping before anything was written")
            return 1
        token = step_telegram(args.yes, args.token)
        project = step_project(args.yes, args.project_dir)
        model, live_models = step_model(args.yes, state)
        session_name = step_name(args.yes, project, args.name)
        stt, _ = step_voice(state)
        tts = step_tts(state)
        autostart = False if args.no_autostart else (args.autostart or step_autostart_question())

        print()
        print(rule())
        print("  Summary")
        print(rule())
        print(f"    project      {project}")
        print(f"    session name {session_name}")
        print(f"    model        {model}")
        print(f"    telegram     {'configured' if token else 'skipped'}")
        print(f"    voice input  {'on' if stt else 'off'}")
        print(f"    voice output {'on' if tts else 'off (enable with /tts always)'}")
        print(rule())

        if args.dry_run:
            print()
            no("dry run - nothing was written")
            print(f"  would write {BRIDGE_DIR / 'config.toml'}")
            print(f"  would write {OC_CONFIG}")
            print(f"  would install the npm shim into {NPM_BIN}")
            if autostart:
                print("  would register 2 scheduled tasks")
            print()
            return 0

        if not args.yes and not ask_yes_no("write this configuration?", default=True):
            print()
            info("nothing was changed")
            return 0

        verified = step_write(args, project, session_name, token, model, stt, tts)
        if not verified:
            print()
            no("stopping - the config on disk does not match your answers")
            info("fix config.toml by hand, or delete it and run setup.py again")
            return 1
        step_install_shim()
        step_autostart(autostart)
        step_verify(fresh=not autostart)

        print()
        print(rule("="))
        print(f"  {_c('Done.', OK + BOLD)}")
        print(rule("="))
        print()
        bridge_ps1 = BRIDGE_DIR / "start-cc-connect.ps1"
        shim_ps1 = SHIM_DIR / "start-gigaam-stt.ps1"
        if token:
            print("  Start the bridge:")
            print(f'    powershell -NoProfile -ExecutionPolicy Bypass -File "{bridge_ps1}"')
        if stt:
            print("  Start the voice shim:")
            print(f'    powershell -NoProfile -ExecutionPolicy Bypass -File "{shim_ps1}"')
        if token:
            print()
            print(f"  Then message your bot on Telegram. Run /list inside the chat.")
        print()
        print(f"  {_c('Only a real reboot proves the logon trigger fires.', DIM)}")
        print()
        return 0

    except KeyboardInterrupt:
        print()
        print()
        warn("cancelled")
        return 130
    finally:
        # Always drop the lock. A stale one would block the next run, and the
        # next run is exactly what someone does after cancelling.
        release_lock(lock)


if __name__ == "__main__":
    sys.exit(main())