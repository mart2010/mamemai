# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Ask the Reference LLM for a JSON object, validate it, and send the exact errors back for
repair (bounded). The same loop GenerateProfileAndLedger uses, factored for the probe steps."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar

from .ports import ReferenceLLM, ReferenceRequest

T = TypeVar("T")


class ReferenceJsonError(Exception):
    pass


def extract_json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in the answer")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("the JSON answer must be an object")
    return data


def ask_validated(llm: ReferenceLLM, model: str, system: str, prompt: str,
                  validate: Callable[[dict], T], max_attempts: int = 3) -> T:
    """`validate` gets the parsed object and returns the typed result, or raises ValueError
    listing every problem it found."""
    current, last = prompt, ""
    for _ in range(max_attempts):
        answer = llm.complete(ReferenceRequest(system, current, model)).text
        try:
            return validate(extract_json(answer))
        except ValueError as e:
            last = str(e)
            current = (f"{prompt}\n\nYour previous answer was REJECTED for these reasons:\n{last}\n\n"
                       f"Previous answer:\n{answer}\n\nReturn the complete corrected JSON object.")
    raise ReferenceJsonError(f"no valid answer after {max_attempts} attempts; last errors: {last}")
