---
name: setup-cc-connect-voice-bridge
description: Set up a Windows host so OpenCode is driven from Telegram through the cc-connect bridge, sharing one session with the OpenCode desktop app and the CLI TUI, with local GigaAM speech-to-text and Edge text-to-speech, and autostart that survives reboots. Use when installing or repairing a Telegram-to-OpenCode bridge, when the desktop app does not show the Telegram session, when adding voice messages to the bridge, or when the bridge must come back automatically after a reboot.
---

# Diagnosis and version-skew reference

**Not an install guide.** Run `python setup.py`, or follow [README.md](README.md) for the manual
procedure. This document answers the questions an installer cannot: *why* is this so fragile,
*which* incompatibility am I hitting, and *what* the failure actually looks like.

Read it when something breaks, or before touching `config.toml` by hand.

Every command here was executed and verified on a live host.

## Start here when something is broken

| Symptom | Section |
|---|---|
| `/list` says `exit status 1` | [The `--dir` incompatibility](#the---dir-incompatibility-and-a-second-bug-hiding-behind-it) |
| Desktop does not show the Telegram session | [Confirm one server](#step-1--confirm-desktop-and-cli-already-share-one-server) |
| `/list` empty, but the database has sessions | orphaned service — see [Pitfalls](#a-shim-in-cmd-does-not-reach-session-list) |
| Voice messages arrive as nothing | [Voice input](#step-6--voice-input-gigaam) |
| Bot silent after a reboot | [Autostart](#step-4--autostart) |
| Telegram and CLI disagree on the conversation | `reset_on_idle_mins` — [config](#configtoml) |
| A command fails with a bare exit code | [Version skew](#the---dir-incompatibility-and-a-second-bug-hiding-behind-it) |

## What `setup.py` does for you

The steps below are what the installer performs, and why each one exists. Reading them in order is
useful when the installer is not available or when you need to deviate deliberately.

| Step | What | Why it is not optional |
|---|---|---|
| 1 | one shared server | two servers = two isolated sessions that never sync |
| 2 | session storage | sessions are grouped by directory |
| 3 | bridge config | `cmd` shape and idle rotation are load-bearing |
| 4 | autostart | shim before bridge, or a voice message can arrive first |
| 5 | voice output | `edge-tts` is an external program, not built in |
| 6 | voice input | OpenAI-shaped endpoint required; the shim provides one |
| 7 | apply and verify | config is read at startup only |
| 8 | acceptance | only a real reboot proves the trigger fires |

---

---

## Target end state

```
  Telegram  ──getUpdates─┐
                        │  cc-connect.exe (Go)
  OpenCode Desktop ─────┤   spawns: opencode run --session <PINNED_ID>
                        │   target: 127.0.0.1:49374
  CLI TUI ──────────────┘
                                        │
                    ┌───────────────────▼──────────────────────────┐
                    │  opencode-cli.exe serve --service  :49374   │
                    │  ONE server · ONE opencode.db · ONE session  │
                    └───────────────────┬──────────────────────────┘
                                        │
                       ~/.local/share/opencode/opencode.db (SQLite)

  voice in  ──▶ cc-connect ──MP3──▶ gigaam-stt shim :8300 ──▶ handy.exe ──▶ GigaAM GGUF (Vulkan)
  voice out ◀── cc-connect ◀────────────── Edge TTS (built in, no key)
```

**The rule that makes the rest work: exactly one OpenCode server process.**
Two servers means two isolated sessions that never share live output, even though both write to
the same SQLite file. Event buses are per-process.

---

## Prerequisites

| Requirement | Verify |
|---|---|
| Node.js + `opencode` CLI | `opencode --version` |
| OpenCode desktop app | installed; it ships its own CLI copy |
| cc-connect 1.5.0 | `cc-connect --version` |
| Handy (local STT) | `& "$env:LOCALAPPDATA\Handy\handy.exe" --list-models` |
| GigaAM GGUF downloaded | `--list-models --json` → `"is_downloaded": true` |
| ffmpeg | `ffmpeg -version` |
| Python 3.10+ | `python -c "import sys;print(sys.version_info[:2])"` |
| `edge-tts` on PATH | `where edge-tts && edge-tts --version` — **only for voice replies** |

**ffmpeg is mandatory for both halves of voice**: cc-connect needs it to unpack Telegram's OGG,
and the shim needs it to normalise to 16 kHz mono.

A `ru-RU` SAPI voice is needed only for the autotest (`Microsoft Irina Desktop` on this host).

Two dependencies are easy to mistake for built-ins and are not:

- **`ffmpeg`** is an external binary, not an OpenCode or cc-connect feature. Without it neither
  voice path works, and the failure is easy to misread as a model problem.
- **`edge-tts`** is a separate Python package that `provider = "edge"` shells out to. Installing
  it is Step 5 and is only needed if you want spoken replies at all.

Voice **input** (Step 6) needs neither a key nor any cloud account. Voice **output** (Step 5)
needs no key but does need `edge-tts`. Keep them independent — most setups want one without the
other.

---

## Step 1 — Confirm desktop and CLI already share one server

Do this **before** touching cc-connect. If it fails, nothing downstream will behave.

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'serve|opencode' } |
  Select-Object ProcessId, ParentProcessId, Name, CommandLine
```

Expect exactly two shapes:

| Process | Command line | Identity |
|---|---|---|
| `opencode-cli.exe` | `serve --service` | **the shared server — never kill it** |
| `opencode.exe` | *(no arguments)* | an interactive TUI, its own session |

Confirm which server is live from OpenCode's own log:

```powershell
Select-String -Path "$env:USERPROFILE\.local\share\opencode\log\opencode.log" -Pattern 'cli starting' |
  Select-Object -Last 3
```

```
message="cli starting" version=2.0.28 args="[\"serve\",\"--service\"]"
```

The `run=` id on that line must **stay the same** across the whole session. If it changes, a new
server took over and clients have split.

---

## Step 2 — Verify session storage

Sessions live in SQLite and survive reboots.

```powershell
opencode session list                      # scoped to the CURRENT directory
opencode session export <session-id>       # works across processes, shared DB
```

The `list` command is scoped per folder. A session created in `E:\projects\foo` is invisible from
`E:\projects\bar`, and the desktop app will not show it unless that folder is the open project.
This is the single most common "the session disappeared" cause.

**Choose one real project folder now** and use it everywhere below. A throwaway empty directory
becomes a phantom project that the desktop app never lists.

---

## Step 3 — Install and configure cc-connect

### The `--dir` incompatibility, and a second bug hiding behind it

cc-connect 1.5.0 calls OpenCode in two incompatible ways:

```
opencode run ... --dir <workDir>        OpenCode >= 2.0.12 rejects: "Unrecognized flag: --dir"
opencode session list --json            OpenCode 2.0.28 wants --format json
```

Upstream fixed the second one in `main` (`opencode.go:654` now reads `--format json`) but shipped
neither to 1.5.0. Both are unpatched in the version npm serves as `latest`.

**The trap: a shim configured through `cmd` does not cover `session list`.**

```go
// opencode.go
35: cmd         string   // CLI binary name, default "opencode"
36: cliExtraArgs []string // extra args from cmd after the binary name
654: c := exec.Command(cmd, "session", "list", "--format", "json")
```

`ParseCmdOpts` splits a multi-element `cmd` into a binary plus `extraArgs`, but
`listOpencodeSessions` passes **only the binary** and drops the rest. So this config:

```toml
cmd = ["<NODE>", ".../opencode-shim.js"]
```

makes `/list` execute `node.exe session list --json`. Node looks for a file named `session`, fails,
and exits 1 — surfacing as:

```
❌ Failed to list sessions: opencode: session list: exit status 1
```

`/run` keeps working because `session.go:160` *does* splice `extraArgs` in. That asymmetry is why
42 commands behave and only `/list` breaks — and it makes the shim look broken when it was never
reached.

**Correct wiring — one bare binary, routing moved into the npm shim:**

```toml
cmd = ["opencode"]
```

```
C:\Users\<user>\AppData\Roaming\npm\opencode.cmd     (and the extensionless opencode)
  └─> node.exe + opencode-shim.js
        └─> real opencode.exe     strips --dir, rewrites --json -> --format json
```

Patch **both** entry points: Go on Windows resolves `opencode` through `PATHEXT` and picks
`opencode.cmd`; a bash shell picks the extensionless script instead. Keep the originals as
`opencode.cmd.orig-backup` / `opencode.orig-backup` — an `npm update` of `@opencode/cli` will
overwrite them.

### config.toml

`~/.cc-connect/config.toml`:

```toml
language = "en"
shell    = "powershell.exe"
attachment_send = "on"

[log]
level = "info"

[[projects]]
name = "cc-connect"
admin_from = "*"              # your Telegram numeric ID
reset_on_idle_mins = 0        # CRITICAL: see below
max_turn_time_mins = 45

[projects.agent]
type = "opencode"

[projects.agent.options]
cmd      = ["opencode"]                              # bare binary, see above
work_dir = "<PROJECT_DIR>"   # your real project
mode     = "yolo"

[[projects.platforms]]
type = "telegram"

[projects.platforms.options]
token      = "<bot-token>"
allow_from = "*"
```

> **`reset_on_idle_mins = 0` is not optional.** The default (30) rotates to a fresh session after
> a lull, which silently desynchronises Telegram from the desktop/CLI context.

### Pin the session

Start the bridge, send any message, then read which session it bound to:

```powershell
Get-Content "$env:USERPROFILE\.cc-connect\run.log" -Tail 40 |
  Select-String 'agent_session='
```

```
msg="turn complete" session=s2 agent_session=ses_ec80b7010ffeUtBJlcoVcenJUX
```

That id is now pinned. Telegram, desktop and CLI all resolve to it.

---

## Step 4 — Autostart

There is no service manager on this host. Check all four locations before assuming anything runs:

```powershell
Get-ScheduledTask | Where-Object TaskName -match 'opencode|cc-connect'
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
```

### Launcher scripts

Put the logic in `.ps1` files, **not** in the task action. Nested quoting inside
`-Command "Start-Process …"` is where silent breakage lives.

`~/.cc-connect/start-cc-connect.ps1` must:

- exit cleanly if the bridge is already running (idempotency guard)
- launch `cc-connect.cmd` detached with stdout/stderr redirected to `run.log` / `run.err.log`
- **not** set `XDG_CONFIG_HOME` / `XDG_DATA_HOME` — sharing the desktop database is the whole point

```powershell
$proc = Start-Process -FilePath $Bin `
                      -RedirectStandardOutput $StdOut `
                      -RedirectStandardError  $StdErr `
                      -WindowStyle Hidden -PassThru
```

### Register the tasks

```powershell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<USERPROFILE>\.cc-connect\start-cc-connect.ps1"'

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT20S'                  # let Wi-Fi/VPN settle

$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName 'cc-connect-autostart' -Action $action `
  -Trigger $trigger -Principal $principal -Settings $settings -Force
```

`Interactive` + `Limited` means no stored password and no elevation. The second task (voice shim)
must have a **smaller** delay so it is listening before the bridge can receive a voice message.

Rollback: `schtasks /delete /tn cc-connect-autostart /f`

---

## Step 5 — Voice output (Edge TTS)

Built into the binary as `core.EdgeTTS`. **No API key, no extra install.**

Default is **off**, because unsolicited voice on every reply is noise. Turn it on explicitly from
chat with `/tts always` or `/tts voice_only`; turn it back off with `enabled = false` plus
`/restart`.

```toml
[tts]
enabled  = false
provider = "edge"
voice    = "ru-RU-DmitryNeural"
tts_mode = "voice_only"      # reply with voice only when the user sent voice
```

`tts_mode` only has `always` and `voice_only` — there is no "never" mode, so "voice only when
asked for it" must be achieved with `enabled = false` plus the runtime `/tts` command.

Providers needing keys: `qwen`, `openai`, `minimax`, `mimo`. Local binaries: `espeak`, `pico`.
cc-connect validates key presence for the keyed providers at startup
(`tts: qwen provider enabled but api_key is empty`).

### `edge` needs no key — but it still needs a program

Despite `core.EdgeTTS` appearing in the binary's symbols, the provider is a **wrapper that
shells out to an `edge-tts` executable**. Without it, every synthesis fails:

```
tts: voice reply failed  error="synthesize: edge-tts: ...:
                          exec: \"edge-tts\": executable file not found in %PATH%"
```

Install it into a Python whose `Scripts` directory is already on `PATH`:

```powershell
& "<USERPROFILE>\AppData\Local\Programs\Python\Python310\python.exe" -m pip install edge-tts
where edge-tts          # must resolve, or synthesis cannot work at all
edge-tts --version
```

Two traps here:

- The failure is **not** a text-length limit. Every synthesis fails identically regardless of
  input size, so do not diagnose it from the length of the failed reply.
- `Start-Process` inside a scheduled task may not inherit the same `PATH` the interactive shell
  has. Verify with `where edge-tts` from the *same* context the bridge runs in, or put the
  absolute directory on the task's `PATH` explicitly.

### One-off voice without touching the agent reply

`cc-connect send --tts` synthesises arbitrary text straight into the chat, bypassing the agent
turn and the `[tts] enabled` setting entirely:

```powershell
cc-connect send --tts "Да пребудет с нами сила 5 января" -s "telegram:2852149:2852149"
```

Useful when the agent's own reply is too long to speak well, or when only a specific phrase
should be spoken. Verify it worked via the bridge log:

```
tts: starting synthesis
tts: synthesis successful
tts: audio sent successfully
```

---

## Step 6 — Voice input (GigaAM)

### Why a shim is unavoidable

cc-connect transcribes **only** through an OpenAI-compatible `/v1/audio/transcriptions`
endpoint. `provider` accepts `openai` | `groq` | `qwen`; all three hit that same endpoint and
differ only in auth.

`provider = "openai"` does **not** mean OpenAI — it means "any OpenAI-shaped endpoint".

GigaAM has no HTTP interface. Handy ships a headless batch mode:

```
handy.exe -f <wav 16k mono> --json --model <model-id> [--device-index N]
```

stdout receives exactly one JSON line; all logging goes to stderr. Observed shape:

```json
{"audio_secs":7.958,"best_ms":5527,"bound_backend":"Vulkan0","load_ms":377,
 "model":"handy-computer/gigaam-v3-e2e-rnnt-gguf/gigaam-v3-e2e-rnnt-Q8_0.gguf",
 "requested_device":"index 0","rtf":1.44,"text":"…","transcribe_ms":[5527]}
```

### Discover the model and device

```powershell
& "$env:LOCALAPPDATA\Handy\handy.exe" --list-models --json > "$env:TEMP\hm.json"
& "$env:LOCALAPPDATA\Handy\handy.exe" --list-devices
```

```
index=0 kind=vulkan name=NVIDIA GeForce RTX 3060 vram=12113MB
index=1 kind=vulkan name=Intel(R) UHD Graphics 770 vram=16232MB
index=2 kind=cpu      name=13th Gen Intel(R) Core(TM) i5-13600K
```

Only entries reporting `"is_downloaded": true` are usable — the catalogue lists many GigaAM
variants and usually only one is present locally.

### The shim

`~/.cc-connect/gigaam-stt/server.py` — Python stdlib only, no pip installs. It must:

- `POST /v1/audio/transcriptions` → multipart in, `{"text": "…"}` out
- `GET /v1/models` → advertise `whisper-1` so the bridge's default model name resolves
- `GET /health` → report whether Handy and ffmpeg are reachable
- normalise audio via ffmpeg to **16 kHz mono** — GigaAM degrades or returns nothing otherwise
- serialise requests with a process-wide lock; Handy owns the GPU

Chain:

```
Telegram OGG/Opus → cc-connect (ffmpeg → MP3) → shim → ffmpeg → WAV 16 kHz mono
                  → handy.exe → GigaAM → {"text": "…"}
```

### Shim launcher

`~/.cc-connect/gigaam-stt/start-gigaam-stt.ps1`:

- pick an interpreter from an explicit list, **skipping any path containing `\venv\`**
- guard by **probing the port**, not by process name
- launch with an **absolute** script path so `-Stop` can find it later

Register a second task with `Delay = 'PT15S'`.

### Bridge config

```toml
[speech]
enabled  = true
provider = "openai"
language = "ru"

[speech.openai]
base_url = "http://127.0.0.1:8300/v1"
api_key  = "local"
model    = "whisper-1"
```

---

## Step 7 — Apply and verify

Config changes require a bridge restart. From the Telegram chat, `/restart` — this severs the very
chat you are reading, but the session survives in the database.

Then run the autotest that ships alongside this skill:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\autostart-test.ps1
```

Expected: `failures: 0`, `VERDICT: autostart chain checks out`.

The autotest covers launcher integrity, task definition, runtime state, a fired simulation, the
destructive stop/start cycle (opt-in via `-Live -Force`), and a **live round trip** that synthesises
Russian speech, transcribes it through the shim, and matches keywords.

Two limitations it reports honestly rather than hiding:

- a real logon cannot be synthesised without a VM — only a reboot proves Windows fires the trigger
- if the service is already running, the idempotency guard returns in ~1s and the **cold-start path
  stays uncovered** (surfaced as a `WARN`)

---

## Step 8 — Acceptance test

Reboot. Then, from Telegram, send one voice message. It should be transcribed by GigaAM and
answered **in text**. With `enabled = false` there is no spoken reply — that is the intended
default, not a failure.

Confirm both directions separately, because they fail for different reasons:

```powershell
# input: this is the one that must work unattended
opencode session list
Get-Content "$env:USERPROFILE\.cc-connect\run.log" -Tail 20 |
  Select-String 'transcribing voice|voice transcribed'
```

```
transcribing voice message  format=ogg size=157454
voice transcribed            text_len=190
```

```powershell
# output: only when explicitly asked for
cc-connect send --tts "проверка" -s "telegram:<chat>:<thread>"
```

Confirm the same session is alive everywhere:

```powershell
opencode session list
```

Note that a voice message produces **two** `message received` lines with the same `msg_id`: the
first with `has_audio=true content_len=0` (raw arrival), the second with the transcript's
character count. Only the second proves transcription happened.

---

## Pitfalls that will bite you

**PowerShell 5.1 mangles non-ASCII in scripts.** `powershell.exe` is 5.1 and decodes BOM-less files
as ANSI, so a UTF-8 script with Cyrillic literals produces a **parser error** and refuses to run.
Keep scripts ASCII; put non-ASCII in a BOM'd data file read with `Get-Content -Encoding UTF8`.

**`curl` output piped into PowerShell is mangled.** Native stdout is decoded with the console code
page: a valid 41-character Cyrillic transcript arrived as 78 characters of garbage and `-match`
silently failed. Capture to a file and decode explicitly:

```powershell
& curl.exe -s -X POST $url -F "file=@$wav" -o raw.json
$text = (Get-Content raw.json -Raw -Encoding UTF8 | ConvertFrom-Json).text
```

**`Invoke-RestMethod -Form` is PowerShell 7 only.** Use `curl.exe` under 5.1.

**Never trust bare `python`.** Here it resolved to an unrelated tool's virtualenv. Enumerate real
interpreters, reject any path containing `\venv\`, and verify by executing
`-c "import sys; print(sys.version_info[:2])"`. A hard-coded path that does not exist makes the
task fail **silently** at next logon.

**Guard by port, not process name.** A service is a `python.exe` like any other.

**Absolute paths in service command lines.** A relative `server.py` leaves the directory out of the
command line, and a later `Stop-Process` filter keyed on the full path silently misses it.

**Startup lines live in stderr.** cc-connect writes `acquired instance lock` and `config loaded` to
`run.err.log`, not `run.log`. Grepping stdout yields a false failure.

**A log check is not a wiring check.** Verify the endpoint with `curl /health`, not by reading logs.

**`SKIP` must not render as `FAIL`** in a test harness, or the summary lies.

**Rotation breaks cross-client continuity.** Any `reset_on_idle_mins` other than `0` will fork the
Telegram session away from the desktop and CLI context.

**Don't diagnose a TTS failure from the length of the text that failed.** Read the actual
`error=` field. The `edge` provider shells out to a separate `edge-tts` executable and fails
identically on a 12-character phrase and a 3600-character one — the misleading clue is the
`text_len=` value printed right before the error.

**A shim in `cmd` does not reach `session list`.** Symptom: `/list` fails with `exit status 1`
while every other command works. Root cause is `opencode.go:654` passing only the binary element of
`cmd` and discarding `extraArgs` — so a `node.exe + shim.js` pair degenerates into
`node.exe session list`, which cannot succeed. Do not try to fix this by improving the shim; the
shim is never invoked. Set `cmd = ["opencode"]` and put the routing in the npm shim instead.

**`Failed to list sessions` never appears in `run.log`.** The bridge surfaces this one error to the
chat but does not log it server-side. Don't conclude a command is untested because the log is
silent — confirm by looking at what the chat actually returned.

**`/doctor` bypasses the shim too.** `doctor.go:122,146` uses `exec.LookPath("opencode")` rather
than the configured `cmd`. Harmless for `--version`, but remember it if you extend the shim.

**Slash commands queue while the agent is working.** A `/list` sent during a long turn sits in the
queue and fires afterwards. Testing commands mid-turn produces stale-looking results that look
exactly like the command being broken. Test while the agent is idle.

**`cc-connect send -m "/list"` does not execute a command.** It injects the literal text as a
message through the outbound API, bypassing the command dispatcher. To test a bot command you have
to send it from the real chat.

**On Windows, Go resolves `opencode` via `PATHEXT` to `opencode.cmd`; bash picks the
extensionless script.** Patch both or your verification will not match production.

**Voice input and voice output fail for entirely unrelated reasons.** "I sent voice and got
nothing back" is almost always the *output* side, not a recognition failure. Check
`voice transcribed` in the log first. If it is there, recognition worked and the only problem is
that TTS is off, disabled by design, or missing `edge-tts`.

**A voice message logs `has_audio=true content_len=0`.** That is the raw arrival, not a failure.
The transcript appears as a second line with the same `msg_id` and a non-zero `content_len`.

**Restarting the bridge cuts the chat you are talking in.** `/restart` in Telegram severs the
very connection the request came through. The session survives in the database, but any reply in
flight is lost — finish the current turn, then restart.

---

## File layout produced by this setup

```
~/.cc-connect/
├── config.toml                       bridge config (+ [tts], [speech])
├── start-cc-connect.ps1              bridge launcher (idempotent)
├── run.log                           turns
├── run.err.log                       lifecycle lines
├── opencode-shim/opencode-shim.js    strips the unsupported --dir flag
├── opencode-shim/test-opencode-shim.js
└── gigaam-stt/
    ├── server.py                     OpenAI-compatible STT endpoint
    ├── start-gigaam-stt.ps1          shim launcher (port-guarded)
    ├── shim.log                      HTTP access log (needs --verbose)
    └── shim.err.log                  startup banner + tracebacks

<project>/
├── SKILL.md                          this document
├── autostart-test.ps1                autotest, sections A–F
└── stt-selftest.txt                  test phrase (UTF-8 with BOM)
```

Scheduled tasks:

| Task | Delay | Runs |
|---|---|---|
| `gigaam-stt-autostart` | `PT15S` | `start-gigaam-stt.ps1` |
| `cc-connect-autostart` | `PT20S` | `start-cc-connect.ps1` |

Do not kill `opencode-cli.exe serve --service` — it carries Telegram *and* the desktop app.