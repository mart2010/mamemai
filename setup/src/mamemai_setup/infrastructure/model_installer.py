# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from __future__ import annotations

import subprocess
from typing import Any, Callable

from huggingface_hub import hf_hub_download, snapshot_download

_PIPER_VOICES_REPO = "rhasspy/piper-voices"
# Must match SentenceTransformerEmbeddingService's default in
# server/src/mamemai_server/infrastructure/embedding.py — the embedding model is a
# hardcoded invariant of Mamémai (CLAUDE.md: not voice-configurable, not swappable via
# GA conversation), so there is no plan field to drive this from, unlike Whisper/Piper.
_EMBEDDING_MODEL_REPO = "intfloat/multilingual-e5-large"


def _cached(download_fn: Callable[..., Any], **kwargs: Any) -> bool:
    """Whether huggingface_hub already has this fully cached locally — checked with
    `local_files_only=True`, which touches no network at all. Skips a real download
    attempt (and the network exception that would otherwise follow) whenever a prior
    run, or anything else sharing this machine's HF cache, already has it: without
    this, a model already present locally would still fail here the moment the Hub
    itself is unreachable (a corporate proxy, an offline machine, ...), even though
    nothing was actually missing."""
    try:
        download_fn(local_files_only=True, **kwargs)
        return True
    except Exception:  # noqa: BLE001 — cache-miss exceptions vary by huggingface_hub version
        return False


class OllamaModelInstaller:
    """Real network/subprocess side effects — additive only (CLAUDE.md:
    engines are never removed), each method safe to re-run if a prior wizard
    run was interrupted (huggingface_hub downloads and `ollama pull` are both
    already idempotent/resumable on their own)."""

    def pull_llm(self, model_id: str) -> None:
        # `ollama list` already-installed check, same reasoning as _cached above: skips
        # the network pull entirely when Ollama already has it, so an unreachable
        # registry doesn't block re-running the wizard against an already-complete install.
        installed = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=True).stdout
        names = {line.split()[0] for line in installed.splitlines()[1:] if line.strip()}
        if model_id in names or f"{model_id}:latest" in names:
            return
        subprocess.run(["ollama", "pull", model_id], check=True)

    def download_whisper_model(self, name: str) -> None:
        # Matches faster-whisper's own internal repo mapping (Systran's
        # CTranslate2 conversions) — the same download FasterWhisperSTTService
        # would trigger lazily on first use; the wizard just does it up front
        # (see docs/INSTALLATION.md step 7).
        repo_id = f"Systran/faster-whisper-{name}"
        if _cached(snapshot_download, repo_id=repo_id):
            return
        snapshot_download(repo_id=repo_id)

    def download_piper_voice(self, voice_id: str) -> None:
        # voice_id format: "{lang}_{REGION}-{name}-{quality}", e.g.
        # "de_DE-thorsten-medium" — verified against rhasspy/piper-voices'
        # actual directory layout (lang/lang_region/name/quality/voice_id.*).
        lang_region, voice_name, quality = voice_id.split("-")
        lang = lang_region.split("_")[0]
        base_path = f"{lang}/{lang_region}/{voice_name}/{quality}/{voice_id}"
        onnx = {"repo_id": _PIPER_VOICES_REPO, "filename": f"{base_path}.onnx"}
        onnx_json = {"repo_id": _PIPER_VOICES_REPO, "filename": f"{base_path}.onnx.json"}
        if _cached(hf_hub_download, **onnx) and _cached(hf_hub_download, **onnx_json):
            return
        hf_hub_download(**onnx)
        hf_hub_download(**onnx_json)

    def download_embedding_model(self) -> None:
        # Same rationale as download_whisper_model: SentenceTransformerEmbeddingService
        # sets HF_HUB_OFFLINE=1 so the live server never touches the network for it (see
        # its docstring) — so if the wizard doesn't pre-download it, first server startup
        # fails outright (offline mode, no cached model) instead of just being slower.
        if _cached(snapshot_download, repo_id=_EMBEDDING_MODEL_REPO):
            return
        snapshot_download(repo_id=_EMBEDDING_MODEL_REPO)
