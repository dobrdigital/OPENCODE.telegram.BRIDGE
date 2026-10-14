#!/usr/bin/env python3
"""
GigaAM STT shim — an OpenAI-compatible /v1/audio/transcriptions endpoint backed
by the local Handy runtime.

Why this exists
---------------
cc-connect can only transcribe voice messages through an OpenAI-compatible
`/v1/audio/transcriptions` endpoint. GigaAM is a local Sber model with no such
endpoint of its own. Handy ships a headless batch mode:

    handy.exe -f <wav 16k mono> --json --model <model-id>

so this process sits in front of it and speaks the protocol cc-connect expects.

Chain:  Telegram OGG/Opus -> cc-connect (ffmpeg -> MP3) -> this shim
        -> ffmpeg -> 16 kHz mono WAV -> handy.exe -> GigaAM -> {"text": ...}

No third-party dependencies. Python 3.11 stdlib only.

Run:
    python server.py
    python server.py --port 8300 --device-index 0

Health:
    curl http://127.0.0.1:8300/health
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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── configuration ────────────────────────────────────────────────────────────

HANDY_EXE = os.path.join(
    os.environ.get("LOCALAPPDATA", r"<USERPROFILE>\AppData\Local"),
    "Handy", "handy.exe",
)

DEFAULT_MODEL = (
    "handy-computer/gigaam-v3-e2e-rnnt-gguf/gigaam-v3-e2e-rnnt-Q8_0.gguf"
)

# cc-connect advertises a model name in /v1/models; map it onto the real Handy id.
MODEL_ALIASES = {
    "whisper-1": DEFAULT_MODEL,
    "gigaam": DEFAULT_MODEL,
    "gigaam-v3": DEFAULT_MODEL,
    "gigaam-v3-e2e-rnnt": DEFAULT_MODEL,
    DEFAULT_MODEL: DEFAULT_MODEL,
}

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
TRANSCRIBE_TIMEOUT = 120          # seconds, hard ceiling for one utterance
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SAMPLE_RATE = 16000

# Handy owns the GPU; never let two transcriptions race for it.
_gpu_lock = threading.Lock()


class ShimError(Exception):
    """Something went wrong in a way the caller should hear about as a 4xx/5xx."""


# ── multipart/form-data parsing (stdlib only) ─────────────────────────────────

def _parse_multipart(body: bytes, content_type: str) -> tuple[dict, dict]:
    """Return (fields, files) from a multipart/form-data body.

    fields: name -> str
    files:  name -> (filename, bytes)

    Deliberately minimal. It handles what cc-connect actually sends: a single
    audio part plus a handful of small text fields.
    """
    m = re.search(r'boundary="?([^";]+)"?', content_type or "", re.I)
    if not m:
        raise ShimError("multipart request without a boundary parameter")

    delimiter = b"--" + m.group(1).encode("ascii", "replace")
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}

    for raw in body.split(delimiter)[1:]:
        # Each chunk is preceded by CRLF and terminated by CRLF + next delimiter.
        if raw[:2] == b"\r\n":
            raw = raw[2:]
        if raw[:2] == b"--":          # closing delimiter
            break
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        if not raw:
            continue

        head, sep, data = raw.partition(b"\r\n\r\n")
        if not sep:
            continue

        headers = head.decode("utf-8", "replace")
        name_m = re.search(r'name="([^"]*)"', headers)
        if not name_m:
            continue
        name = name_m.group(1)

        file_m = re.search(r'filename="([^"]*)"', headers)
        if file_m:
            files[name] = (file_m.group(1), data)
        else:
            fields[name] = data.decode("utf-8", "replace").strip()

    return fields, files


# ── audio pipeline ───────────────────────────────────────────────────────────

def _to_wav_16k(src_path: str, dst_path: str) -> None:
    """Normalise any input container to 16 kHz mono PCM WAV.

    GigaAM is trained on 16 kHz mono; anything else produces degraded or empty
    output rather than an error, so this step is not optional.
    """
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-i", src_path,
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-acodec", "pcm_s16le",
        dst_path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise ShimError(
            "ffmpeg not found on PATH. cc-connect also needs it to unpack "
            "Telegram's OGG, so install it and restart this shim."
        )
    except subprocess.TimeoutExpired:
        raise ShimError("ffmpeg timed out while converting audio")

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise ShimError(f"ffmpeg failed to convert audio: {err[:300]}")


def _transcribe(wav_path: str, model: str, device_index: int | None) -> tuple[str, dict]:
    """Run Handy headlessly and return (text, raw_json)."""
    if not os.path.isfile(HANDY_EXE):
        raise ShimError(f"handy.exe not found at {HANDY_EXE}")

    cmd = [HANDY_EXE, "-f", wav_path, "--json", "--model", model]
    if device_index is not None:
        cmd += ["--device-index", str(device_index)]

    started = time.monotonic()
    with _gpu_lock:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=TRANSCRIBE_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise ShimError(
                f"handy.exe exceeded {TRANSCRIBE_TIMEOUT}s; the model may be stuck"
            )

    elapsed = time.monotonic() - started

    # Handy writes structured logs to stderr and exactly one JSON line to stdout.
    stdout = proc.stdout.decode("utf-8", "replace").strip()
    if not stdout:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise ShimError("handy.exe produced no output. " + " | ".join(tail))

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        raise ShimError(f"handy.exe returned unparseable output: {stdout[:200]}")

    if proc.returncode != 0 and not payload.get("text"):
        raise ShimError(f"handy.exe exited {proc.returncode}: {stdout[:200]}")

    payload["shim_elapsed_sec"] = round(elapsed, 3)
    return (payload.get("text") or "").strip(), payload


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "gigaam-stt-shim/1.0"
    protocol_version = "HTTP/1.1"

    model = DEFAULT_MODEL
    device_index: int | None = 0
    verbose = False

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        # Access log goes to stdout (shim.log); explicit flush because stdout is
        # block-buffered when redirected to a file. Errors go to stderr.
        if self.verbose:
            sys.stdout.write("[gigaam-stt] " + (fmt % args) + "\n")
            sys.stdout.flush()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, code: int, message: str) -> None:
        self._send_json(code, {"error": {"message": message, "type": "shim_error"}})

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path in ("/health", "/v1/health"):
            self._send_json(200, {
                "status": "ok",
                "model": self.model,
                "handy": os.path.isfile(HANDY_EXE),
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "device_index": self.device_index,
            })
        elif path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [{
                    "id": "whisper-1",
                    "object": "model",
                    "owned_by": "handy-gigaam",
                }],
            })
        else:
            self._fail(404, f"no such route: {path}")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/v1/audio/transcriptions":
            self._fail(404, f"no such route: {path}")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._fail(400, "bad Content-Length")
            return

        if length <= 0:
            self._fail(400, "empty request body")
            return
        if length > MAX_UPLOAD_BYTES:
            self._fail(413, f"audio upload exceeds {MAX_UPLOAD_BYTES} bytes")
            return

        body = self.rfile.read(length)
        fields, files = _parse_multipart(body, self.headers.get("Content-Type", ""))

        # cc-connect may name the part "file" or "audio"; take the first audio
        # part we can find rather than guessing.
        audio = None
        for key in ("file", "audio", "audio_file"):
            if key in files:
                audio = files[key]
                break
        if audio is None and files:
            audio = next(iter(files.values()))
        if audio is None:
            self._fail(400, "no audio part in the multipart body")
            return

        filename, payload = audio
        requested = fields.get("model", "").strip()
        model = MODEL_ALIASES.get(requested, requested) if requested else self.model

        want_text = fields.get("response_format", "json").lower() == "text"

        src = wav = None
        try:
            with tempfile.TemporaryDirectory(prefix="gigaam-stt-") as tmp:
                src = os.path.join(tmp, "in" + os.path.splitext(filename)[1])
                with open(src, "wb") as fh:
                    fh.write(payload)

                wav = os.path.join(tmp, "in16k.wav")
                _to_wav_16k(src, wav)
                text, meta = _transcribe(wav, model, self.device_index)

            if want_text:
                self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            else:
                # OpenAI transcription shape. `text` is what cc-connect reads.
                self._send_json(200, {"text": text})
        except ShimError as exc:
            self._fail(502, str(exc))
        except Exception as exc:                      # noqa: BLE001
            self._fail(500, f"{type(exc).__name__}: {exc}")
        finally:
            for path_ in (src, wav):
                if path_ and os.path.exists(path_):
                    try:
                        os.remove(path_)
                    except OSError:
                        pass


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="OpenAI-compatible GigaAM STT shim")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; keep on loopback unless you know why not")
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device-index", type=int, default=0,
                    help="Handy compute device; 0 = NVIDIA, 2 = CPU. -1 disables.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(HANDY_EXE):
        sys.stderr.write(
            f"warning: {HANDY_EXE} not found; /health will report handy=false\n"
        )
    if not shutil.which("ffmpeg"):
        sys.stderr.write("warning: ffmpeg is not on PATH; transcriptions will fail\n")

    Handler.model = args.model
    Handler.device_index = None if args.device_index < 0 else args.device_index
    Handler.verbose = args.verbose

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True

    sys.stderr.write(
        f"[gigaam-stt] listening on http://{args.host}:{args.port}\n"
        f"[gigaam-stt] model  : {args.model}\n"
        f"[gigaam-stt] device : {args.device_index}\n"
    )
    sys.stderr.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())