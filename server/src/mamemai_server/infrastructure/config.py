# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

# Threshold policy lives with the service that applies it; imported so config's
# defaults and MemoryUpserter's fallbacks cannot drift apart.
from ..services.upsert import (
    DEFAULT_DISAMBIGUATE_THRESHOLD as _DEFAULT_DISAMBIGUATE_THRESHOLD,
    DEFAULT_MERGE_THRESHOLD as _DEFAULT_MERGE_THRESHOLD,
)

CONFIG_PATH = Path(user_config_dir("mamemai", appauthor=False)) / "mamemai.toml"
# Session logs are persistent app data, not settings (INV-5, kept forever) — the data
# dir, not the config dir. Absolute and OS-independent, unlike the old "logs/sessions"
# literal default, which silently resolved against whatever directory the server
# process happened to be launched from (a real bug: it scattered logs across
# directories depending on cwd, and broke session-tail continuity/crash-recovery replay
# across launches from different locations).
_DEFAULT_LOG_DIR = Path(user_data_dir("mamemai", appauthor=False)) / "sessions"


# CTranslate2 decides its own intra-op thread count when given 0, and on this machine that
# is 4 — measured 2026-08-02: `cpu_threads=0` and `cpu_threads=4` time identically, on a
# 24-core box. Raising it is free speed with byte-identical transcripts, but it does NOT
# scale with cores; on medium/int8, over 28.7s of audio:
#
#   threads   4 (default)     8         16        24
#   total       20.0s       13.8s     14.4s     15.3s
#
# 8 is the peak and 16/24 are worse — the int8 GEMM saturates memory bandwidth well before
# it runs out of cores, so `nproc` would be a slower default than this constant. Small
# benefits too, if less (1.30s -> 1.11s per utterance). Overridable via [stt] cpu_threads
# for machines whose sweet spot differs; re-run scripts/ or the numbers above to retune.
DEFAULT_CPU_THREADS = 8


# Ollama's own default is 4096 tokens and it truncates anything longer in silence — see
# OLLAMA_DEFAULT_NUM_CTX in infrastructure/llm/_common.py for the measurements, and TR-951.
# 16384 is the value measured working 2026-08-03 where 4096 failed: the same 72-turn
# conversation extracted 0 concepts at 4096 and 6 concepts + 3 episodes at 16384, with
# nothing else changed. It is not the model's ceiling (llama3.1:8b reports 131072) and is
# not meant to be: the KV cache scales with num_ctx, so the useful value is
# min(model ceiling, what VRAM allows, what the workload needs), and this is sized to the
# workload — comfortably past the ~4 400 tokens a 37-minute French session produces, with
# room for the windowing above it. Cost, measured: a single full-context extraction call
# takes 29-33s against 4.7-7.1s truncated, but it replaces the 3-6 windowed calls it was
# split into, so it is roughly cost-neutral. Overridable via [llm] num_ctx; the wizard
# will eventually derive it per model/VRAM (see docs/PLAN.md), which is why the value
# lives here rather than being hardcoded at the call sites.
DEFAULT_NUM_CTX = 16384


# Below this much buffered audio, `end_utterance` is dropped without calling STT (TR-958).
# The client's onset gate (TR-203) is the primary fix for the 2026-08-03 finding that 49
# of 65 `end_utterance` events transcribed to empty — three quarters of this machine's STT
# budget spent on room noise. This is the server-side backstop: client and server are
# independently-installed packages and can be out of step, and an older client, a
# different VAD aggressiveness or a noisier room all arrive here the same way.
#
# 300 ms is set to catch what is unambiguously not an utterance rather than to
# discriminate finely. A one-syllable "oui" is ~200-300 ms of *voiced* audio and TR-203
# strips inter-word silence, so a floor much above this would start eating real one-word
# turns — the same posture as TR-956's blocklist, where a false positive silently
# swallows a real turn and is therefore the expensive direction to err in.
DEFAULT_MIN_UTTERANCE_MS = 300


# Re-exported from the service that owns the policy rather than restated here: these are
# the same numbers `MemoryUpserter` falls back to, and two copies would eventually
# disagree. The derivation (measured 2026-08-04 under the TR-509 prefixes, and why 0.93
# was unsafe) is documented at the definitions.
DEFAULT_MERGE_THRESHOLD = _DEFAULT_MERGE_THRESHOLD
DEFAULT_DISAMBIGUATE_THRESHOLD = _DEFAULT_DISAMBIGUATE_THRESHOLD


@dataclass(frozen=True)
class ServerConfig:
    ws_port: int
    log_dir: Path
    database_url: str
    stt_model_path: str
    stt_device: str
    stt_compute_type: str
    stt_cpu_threads: int
    # Shortest buffered utterance worth a Whisper pass, in ms — see DEFAULT_MIN_UTTERANCE_MS.
    stt_min_utterance_ms: int
    # [tts] device / [embedding] device. None = let the library auto-detect. Auto-detect
    # is no longer a safe default on a machine whose torch build can see the GPU: it
    # picks it, and that is right for one of these two components and wrong for the
    # other (TR-960).
    tts_device: str | None
    embedding_device: str | None
    llm_model: str
    llm_ollama_host: str | None
    # Live-conversation LLM backend (FR-707/TR-955). "ollama" (default) reuses
    # llm_model/llm_ollama_host above for the live path too, exactly as before this
    # setting existed — fully backward compatible with configs that predate it.
    # "openai_compatible" swaps only the main conversational LLM (see ProcessTurn) to
    # a remote HTTP endpoint; the offline pipeline (consolidation, MemoryBrief, tutor
    # strategy helpers) always stays on llm_model/llm_ollama_host, regardless of this
    # setting — a GPU-less/CPU-only offline run is fine, per design decision, just
    # slower. Recall gating (FR-309/TR-314) doesn't read this field at all anymore —
    # it's local threshold logic now, not an LLM call.
    llm_provider: str
    llm_base_url: str | None  # required when llm_provider == "openai_compatible"
    llm_remote_model: str | None  # required when llm_provider == "openai_compatible"
    llm_api_key: str | None  # optional even when llm_provider == "openai_compatible"
    # Ollama's context window, sent explicitly on every local call (TR-951/TR-952).
    # Needed whatever llm_provider is: FR-707 moves only the live reply to a remote
    # endpoint, and the offline pipeline stays on local Ollama (TR-955) — which is exactly
    # where the silent-truncation cliff was found. A remote endpoint has a fixed context
    # of its own and this value doesn't reach it.
    llm_num_ctx: int
    memory_merge_threshold: float
    memory_disambiguate_threshold: float
    # How many of the previous session's last turns get injected as session tail
    # (FR-109), when that session ended recently enough to count as a continuation. 0
    # disables tail injection entirely — useful while testing, since a prior session's
    # content (e.g. a stray language drift) can otherwise bias a fresh session. Not
    # voice-configurable (FR-701 doesn't apply — this isn't a GA/persona setting);
    # same technical-tuning-knob posture as [memory]'s thresholds below.
    session_tail_turns: int = 10
    # Wizard-selected languages ([languages].installed, FR-705). Empty = key absent
    # (config written before it existed) — the composition root then treats every
    # SUPPORTED_LANGUAGES entry as installed.
    installed_languages: tuple[str, ...] = ()


def load_config(path: Path = CONFIG_PATH) -> ServerConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Server config not found at {path}. "
            "Copy server/config/mamemai.example.toml to that location and fill in your values, "
            "or run mamemai-setup to generate it automatically."
        )
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    server = raw.get("server", {})
    db = raw.get("database", {})
    stt = raw.get("stt", {})
    tts = raw.get("tts", {})
    llm = raw.get("llm", {})
    embedding = raw.get("embedding", {})
    memory = raw.get("memory", {})
    languages = raw.get("languages", {})

    llm_provider = llm.get("provider", "ollama")
    if llm_provider not in ("ollama", "openai_compatible"):
        raise RuntimeError(
            f"[llm].provider must be 'ollama' or 'openai_compatible', got {llm_provider!r}"
        )
    llm_base_url = llm.get("base_url") or None
    llm_remote_model = llm.get("remote_model") or None
    if llm_provider == "openai_compatible" and not (llm_base_url and llm_remote_model):
        raise RuntimeError(
            "[llm].provider = 'openai_compatible' requires both [llm].base_url and "
            "[llm].remote_model to be set"
        )

    return ServerConfig(
        ws_port=int(server.get("ws_port", 8765)),
        log_dir=Path(server.get("log_dir")) if server.get("log_dir") else _DEFAULT_LOG_DIR,
        database_url=db.get("url", "postgresql://mamemai:changeme@localhost:5432/mamemai"),
        stt_model_path=str(Path(stt.get("model_path", "~/models/faster-whisper-small")).expanduser()),
        stt_device=stt.get("device", "cpu"),
        stt_compute_type=stt.get("compute_type", "int8"),
        # Measured, not guessed, and deliberately not `nproc` — see DEFAULT_CPU_THREADS.
        stt_cpu_threads=int(stt.get("cpu_threads", DEFAULT_CPU_THREADS)),
        stt_min_utterance_ms=int(stt.get("min_utterance_ms", DEFAULT_MIN_UTTERANCE_MS)),
        # None when [tts] is absent — preserves Kokoro's own torch.cuda.is_available()
        # auto-detect for configs written before this setting existed.
        tts_device=tts.get("device"),
        embedding_device=embedding.get("device"),
        llm_model=llm.get("model", "aya-expanse"),
        llm_ollama_host=llm.get("ollama_host") or None,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_remote_model=llm_remote_model,
        llm_api_key=llm.get("api_key") or None,
        # Measured, not Ollama's default — see DEFAULT_NUM_CTX.
        llm_num_ctx=int(llm.get("num_ctx", DEFAULT_NUM_CTX)),
        memory_merge_threshold=float(memory.get("merge_threshold", DEFAULT_MERGE_THRESHOLD)),
        memory_disambiguate_threshold=float(memory.get("disambiguate_threshold", DEFAULT_DISAMBIGUATE_THRESHOLD)),
        session_tail_turns=int(server.get("session_tail_turns", 10)),
        installed_languages=tuple(languages.get("installed", [])),
    )
