# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import os

# Must be set before `sentence_transformers` (via huggingface_hub) is imported.
# The live server must never touch the network for model loading, same principle
# as STT/TTS: SentenceTransformer otherwise does a HEAD request to Hugging Face
# Hub on every load to check for updates, even when the model is already fully
# cached locally — that call has no reason to succeed, or even be attempted, on
# a locked-down/offline deployment.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from sentence_transformers import SentenceTransformer  # noqa: E402

# e5's training-time prefixes, including the trailing space (TR-509). They are not
# decorative: the model learned an asymmetric query→passage relation, and omitting them
# collapses the usable range of the similarity scale for every comparison in the system.
# Changing either string invalidates every stored embedding — see the backfill note in
# scripts/reembed_memories.py.
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


class SentenceTransformerEmbeddingService:
    """Embeddings for recall (live, one per turn) and consolidation (offline, many).

    `device` is `[embedding] device` (TR-951). None keeps sentence-transformers' own
    auto-detection, which is what this adapter did before the setting existed.

    Measured 2026-08-04 on gfx1151, e5-large: a single turn is 71 ms on CPU against 25 ms
    on GPU, and a batch of 64 is 1.16 s against 0.81 s. Small against a 22.7 s turn, so
    this is not a live-latency fix; it matters for the offline pipeline and for the
    re-embedding of every stored row that the e5-prefix work requires (docs/PLAN.md).

    Note the knob is worth having explicitly even to select CPU: a torch build that can
    see the GPU makes auto-detection *choose* it, and device placement that suits one
    model does not automatically suit another — Kokoro is 7x slower on this same GPU.
    """

    def __init__(
        self,
        model_name: str = "intfloat/multilingual-e5-large",
        device: str | None = None,
    ) -> None:
        self._model = SentenceTransformer(model_name, device=device)

    def embed_query(self, text: str) -> list[float]:
        return self._encode(f"{_QUERY_PREFIX}{text}")

    def embed_passage(self, text: str) -> list[float]:
        return self._encode(f"{_PASSAGE_PREFIX}{text}")

    def _encode(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()
