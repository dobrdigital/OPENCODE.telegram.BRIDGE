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
import time
import tomllib
from pathlib import Path

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

def step_prereqs() -> dict:
    heading(f"[1/9] {_c('Checking prerequisites', BOLD)}")
    state = {}

    _, out = run("opencode --version")
    if "opencode" in out.lower():
        ok(f"opencode {out.strip().splitlines()[0].replace('opencode', '').strip()}")
        state["opencode"] = True
    else:
        no("opencode not found - install the CLI first: npm i -g opencode-ai")
        state["opencode"] = False

    _, out = run("cc-connect --version")
    if "cc-connect" in out.lower():
        ok("cc-connect installed")
        state["cc"] = True
    else:
        no("cc-connect not found - install it: npm i -g cc-connect")
        state["cc"] = False

    _, out = run("ffmpeg -version")
    if "ffmpeg version" in out:
        ok("ffmpeg " + out.strip().split()[2])
        state["ffmpeg"] = True
    else:
        no("ffmpeg missing - both voice paths need it: winget install Gyan.FFmpeg")
        state["ffmpeg"] = False

    code, out = run("pip show edge-tts")
    has_tts = "edge-tts" in out.lower()
    if has_tts:
        ok("edge-tts installed (voice replies available)")
    else:
        info("edge-tts missing - voice replies disabled (pip install edge-tts)")
    state["edge_tts"] = has_tts

    state["handy"] = HANDY.exists()
    if state["handy"]:
        ok(f"Handy found at {HANDY}")
        code, out = run(f'"{HANDY}" --list-models --json', timeout=180)
        downloaded = [m for m in re.findall(r'"is_downloaded":\s*true', out)]
        if downloaded:
            ok(f"{len(downloaded)} speech model(s) downloaded")
            state["stt_model"] = True
        else:
            warn("Handy installed but no speech model is downloaded yet")
            state["stt_model"] = False
    else:
        info("Handy not found - voice input disabled (STT is optional)")
        state["stt_model"] = False

    return state


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
        # 1234567890:AA... - validate the shape, then let the bot answer for itself
        if not re.match(r"^\d{6,}:[A-Za-z0-9_-]{30,}$", value):
            warn("that does not look like a bot token (expected 123456789:AA...)")
            if ask_yes_no("use it anyway", default=False):
                return value
            continue
        ok("format looks right")
        return value


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
    if cfg.exists():
        stamp = str(int(time.time()))
        backup = BRIDGE_DIR / f"config.toml.before-setup-{stamp}.bak"
        shutil.copy2(cfg, backup)
        info(f"existing config backed up to {backup.name}")
    cfg.write_text(text, encoding="utf-8", newline="")

    # Silent regex substitution is how the wizard produced a wrong work_dir and
    # a flipped speech flag without a word. Verify every value actually landed.
    written = tomllib.loads(cfg.read_text(encoding="utf-8"))
    wp = written["projects"][0]
    problems = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    expect(wp["name"] == session_name, f"session name is {wp['name']!r}, wanted {session_name!r}")
    expect(Path(wp["agent"]["options"]["work_dir"]) == project,
           f"work_dir is {wp['agent']['options']['work_dir']!r}, wanted {project.as_posix()!r}")
    expect(wp["agent"]["options"]["cmd"] == ["opencode"],
           f"cmd is {wp['agent']['options']['cmd']!r}, must be a bare binary")
    expect(wp.get("reset_on_idle_mins") == 0, "reset_on_idle_mins must be 0")

    tts_on = bool(written.get("tts", {}).get("enabled"))
    expect(tts_on == tts, f"tts.enabled is {tts_on}, wanted {tts}")

    stt_on = bool(written.get("speech", {}).get("enabled"))
    expect(stt_on == stt, f"speech.enabled is {stt_on}, wanted {stt}")

    if token:
        got = wp["platforms"][0]["options"]["token"]
        expect(got == token, f"telegram token did not land (got {got[:12]}...)")
    else:
        expect("CHANGE-ME" not in cfg.read_text(encoding="utf-8"),
               "a placeholder token was left in the config")

    if problems:
        for p in problems:
            no(p)
        print()
        no(f"{cfg} written but does NOT match your answers - review it before running")
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

    try:
        state = step_prereqs()
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
        warn("cancelled - nothing was written")
        return 130


if __name__ == "__main__":
    sys.exit(main())