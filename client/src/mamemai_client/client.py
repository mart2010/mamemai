# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import asyncio
import json
import os
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import questionary
import sounddevice as sd
import websockets
import webrtcvad
from platformdirs import user_config_dir

from .vad import UtteranceDetector, VadTuning

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(user_config_dir("mamemai", appauthor=False)) / "mamemai.toml"


@dataclass(frozen=True)
class ClientConfig:
    ws_port: int
    ssh_host: str | None  # None for single-host deployments
    vad: VadTuning


def _load_config() -> ClientConfig:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Client config not found at {_CONFIG_PATH}. "
            "Copy client/config/mamemai.example.toml to that location and fill in your values, "
            "or run mamemai-setup to generate it automatically."
        )
    with open(_CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    server = raw.get("server", {})
    vad = raw.get("vad", {})
    default = VadTuning()
    return ClientConfig(
        ws_port=int(server.get("ws_port", 8765)),
        ssh_host=server.get("ssh_host") or None,
        # An absent [vad] table is the pre-2026-08-04 config, and gets the defaults —
        # which are NOT the values that shipped before it existed (see vad.py).
        vad=VadTuning(
            onset_frames=int(vad.get("onset_frames", default.onset_frames)),
            silence_frames=int(vad.get("silence_frames", default.silence_frames)),
            preroll_frames=int(vad.get("preroll_frames", default.preroll_frames)),
        ),
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
FRAME_DURATION = 30  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)

# Avoid proxy settings interfering with the SSH tunnel
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

_vad = webrtcvad.Vad(2)

# Cleared while the assistant is speaking — suppresses VAD and print noise.
# Keepalive/backpressure settings, both ends kept in step (2026-08-02).
#
# The failure they fix, diagnosed from a real session: audio is sent one chunk per
# synthesised sentence, and the client plays them one at a time. On a long reply the
# client's inbound queue fills, its reader PAUSES, TCP backpressure stops the socket
# draining — and the peer's ping then sits unread behind the queued audio. No pong
# arrives, and ~20s later the connection dies with "1011 keepalive ping timeout",
# mid-answer.
#
# The earlier reading (recorded in docs/PLAN.md 2026-07-20) blamed turn *duration* —
# turns measured 19.3-19.5s against a 20s default. That was wrong: the real window is
# ping_interval + ping_timeout = 40s, and turn processing never blocks either event loop
# (every blocking call is already off-loop). What correlates is reply LENGTH, because
# length is what fills the queue — which is exactly what that note observed without
# identifying why.
#
# max_queue is therefore the primary fix: hold a whole reply's audio so the reader never
# pauses. 64 chunks is minutes of speech, far beyond any single reply, and bounded (a
# chunk is one sentence, a few hundred KB at worst). The longer ping window is defence in
# depth for a backlog beyond even that, and still detects a genuinely dead peer.
WS_MAX_QUEUE = 64
WS_PING_INTERVAL = 30
WS_PING_TIMEOUT = 60

# Set at startup and re-set on every speaking_end message.
_mic_active = threading.Event()
_mic_active.set()


# ---------------------------------------------------------------------------
# SSH tunnel (split-host only)
# ---------------------------------------------------------------------------


def _start_ssh_tunnel(ws_port: int, ssh_host: str) -> None:
    cmd = ["ssh", "-N", "-L", f"{ws_port}:localhost:{ws_port}", ssh_host]

    def _run() -> None:
        while True:
            print("Starting SSH tunnel...")
            proc = subprocess.Popen(cmd)
            proc.wait()
            print("SSH tunnel stopped. Restarting in 3s...")
            time.sleep(3)

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


async def _select_language(supported: list[str]) -> str:
    loop = asyncio.get_running_loop()
    choice = await loop.run_in_executor(
        None,
        lambda: questionary.select("Select your language:", choices=supported).ask(),
    )
    return choice or supported[0]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _seconds(frames: int) -> float:
    return frames * FRAME_DURATION / 1000


async def run() -> None:
    config = _load_config()

    if config.ssh_host:
        _start_ssh_tunnel(config.ws_port, config.ssh_host)
        time.sleep(1)  # give the tunnel a moment to establish

    loop = asyncio.get_running_loop()
    detector = UtteranceDetector(config.vad)

    async with websockets.connect(
        f"ws://localhost:{config.ws_port}", max_size=None,
        max_queue=WS_MAX_QUEUE, ping_interval=WS_PING_INTERVAL, ping_timeout=WS_PING_TIMEOUT,
    ) as ws:
        print("Connected")

        def mute_mic() -> None:
            # Reset before muting, not after: frames stop arriving immediately, so
            # whatever partial onset run was in flight must not survive to be credited
            # to the first room-noise frame after `speaking_end`.
            detector.reset()
            _mic_active.clear()

        def callback(indata, frames, time_info, status):
            if not _mic_active.is_set():
                return

            pcm16 = (indata.flatten() * 32768).astype(np.int16)
            frame = pcm16.tobytes()
            decision = detector.feed(frame, _vad.is_speech(frame, SAMPLE_RATE))

            if decision.started:
                print("Speech detected")
            if decision.ignored_burst_frames:
                # The measurement that justifies onset_frames, kept visible so it can be
                # tuned from real numbers rather than re-guessed. Frequent lines here
                # with no speech = noise being correctly refused; lines that coincide
                # with the user starting to talk = onset_frames set too high.
                print(
                    f"[vad] ignored {decision.ignored_burst_frames}-frame noise burst "
                    f"({_seconds(decision.ignored_burst_frames):.2f}s) — no STT pass"
                )
            for pending in decision.send:
                asyncio.run_coroutine_threadsafe(ws.send(pending), loop)
            if decision.ended:
                print(
                    f"End of utterance — {_seconds(decision.speech_frames):.1f}s speech, "
                    f"{_seconds(decision.sent_frames):.1f}s sent"
                )
                asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"type": "end_utterance"})), loop)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            blocksize=FRAME_SIZE,
            dtype="float32",
            callback=callback,
        ):
            async for msg in ws:
                if isinstance(msg, bytes):
                    mute_mic()
                    sd.play(np.frombuffer(msg, dtype=np.float32), SAMPLE_RATE)
                    await loop.run_in_executor(None, sd.wait)
                elif isinstance(msg, str):
                    data = json.loads(msg)
                    msg_type = data.get("type")
                    if msg_type == "select_language":
                        mute_mic()
                        lang = await _select_language(data["supported"])
                        await ws.send(json.dumps({"type": "language_selected", "language": lang}))
                        print(f"Language set: {lang}")
                        _mic_active.set()
                    elif msg_type == "speaking_end":
                        _mic_active.set()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
