# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from __future__ import annotations

import re
import unicodedata

_INVALID_RUN = re.compile(r"[^a-z0-9_]+")
_LEADING_DIGITS = re.compile(r"^[0-9]+")
DEFAULT_SCHEMA = "user_1"


def schema_slug(text: str) -> str:
    """A free-text name into a safe Postgres unquoted identifier: lowercase
    ASCII, starting with a letter, `[a-z][a-z0-9_]*` after that, every other
    run of characters collapsed to a single `_`. Accented/non-Latin input
    degrades via NFKD stripping (e.g. "Mary Ann" -> "mary_ann", "José" ->
    "jose") rather than being rejected outright — good enough for a schema
    name, not meant to preserve the original spelling. Falls back to
    DEFAULT_SCHEMA when nothing usable survives: empty input, input that
    normalises to nothing (e.g. "日本語" has no ASCII fallback), or input
    that is only digits/punctuation once normalised (a leading digit is not
    a valid identifier start)."""
    normalised = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _INVALID_RUN.sub("_", normalised.lower()).strip("_")
    slug = _LEADING_DIGITS.sub("", slug).strip("_")
    return slug or DEFAULT_SCHEMA
