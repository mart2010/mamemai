# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Local Ollama as a Reference LLM — harness smoke runs ONLY (EV-INV-10): `comparable` is
False, so Runs using it are tagged non-comparable and `compare` refuses them. Never a
fallback for `claude_cli`."""
from __future__ import annotations

import json
import urllib.request

from ..usecases.ports import ReferenceLLMError, ReferenceRequest, ReferenceResponse


class OllamaSmokeReferenceLLM:
    comparable = False

    def __init__(self, host: str = "http://localhost:11434") -> None:
        self._host = host.rstrip("/")

    def complete(self, request: ReferenceRequest) -> ReferenceResponse:
        body = {
            "model": request.model, "stream": False,
            "messages": [{"role": "system", "content": request.system}, {"role": "user", "content": request.prompt}],
        }
        if request.temperature is not None:
            body["options"] = {"temperature": request.temperature}
        req = urllib.request.Request(
            f"{self._host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.load(r)
            return ReferenceResponse(text=data["message"]["content"], model=request.model)
        except Exception as e:  # noqa: BLE001 — surfaced as one adapter error type
            raise ReferenceLLMError(f"ollama failed: {e}") from e
