# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""CachingReferenceLLM — EV-INV-8 (every call cached) and EV-INV-10 (no substitution)."""
from __future__ import annotations

import hashlib
import json

from .ports import ReferenceLLM, ReferenceLLMCache, ReferenceRequest, ReferenceResponse


def cache_key(request: ReferenceRequest) -> str:
    """Stable content hash. The pinned model id is part of the key, so a different model
    can never be served another model's cached answer."""
    payload = json.dumps(
        {"system": request.system, "prompt": request.prompt, "model": request.model, "t": request.temperature},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class CachingReferenceLLM:
    """Decorator over any ReferenceLLM. A failed call (QuotaExhausted included) writes
    nothing, so resuming a job replays cached calls for free and retries only the failure."""

    def __init__(self, inner: ReferenceLLM, cache: ReferenceLLMCache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def comparable(self) -> bool:
        return self._inner.comparable

    def complete(self, request: ReferenceRequest) -> ReferenceResponse:
        key = cache_key(request)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        response = self._inner.complete(request)
        self._cache.put(key, request, response)
        return response
