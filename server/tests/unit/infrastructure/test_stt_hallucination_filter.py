# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: FR-103, TR-956 — the stock-hallucination blocklist.

Unit-level because the interesting logic is pure text: `_is_stock_hallucination` decides
on the decoded string alone, deliberately, since a confident hallucination carries no
acoustic signal to key off. The acoustic lines of defence (VAD, `no_speech_prob`) are
covered by `tests/integration/test_stt.py` against a real model.
"""

import pytest

from mamemai_server.infrastructure.stt import _is_stock_hallucination, _normalise


class TestNormalise:
    """Spec: TR-956 — one blocklist entry must cover a phrase's surface variants."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Thanks for watching!", "thanks for watching"),
            ("THANKS, FOR   WATCHING...", "thanks for watching"),
            ("I'll see you", "ill see you"),  # apostrophe deleted, not spaced
            ("I’ll see you", "ill see you"),  # typographic apostrophe too
            ("Amara.org", "amara org"),  # other separators become spaces
            ("Sous-titres", "sous titres"),
            ("dalla comunità", "dalla comunita"),  # accents folded for matching
        ],
    )
    def test_folds_surface_variants(self, raw: str, expected: str) -> None:
        assert _normalise(raw) == expected


class TestStockHallucinationBlocklist:
    @pytest.mark.parametrize(
        "text",
        [
            # The phrase actually observed live 2026-07-18 on a silent mic — it cleared
            # the no_speech_prob filter, which is why this blocklist exists.
            "Thanks for watching, and I'll see you in the next video.",
            "Thank you for watching!",
            " Thanks for watching. ",
            "Please subscribe to my channel.",
            "Subtitles by the Amara.org community",
            "Sous-titres réalisés par la communauté d'Amara.org",
            "Sottotitoli e revisione a cura di QTSS",
            "Sottotitoli creati dalla comunità Amara.org",
        ],
    )
    def test_drops_known_artifacts(self, text: str) -> None:
        assert _is_stock_hallucination(text)

    @pytest.mark.parametrize(
        "text",
        [
            # Ordinary speech must survive — a false positive silently eats a real turn.
            "Thanks!",
            "Thank you, that was helpful.",
            "Can you watch my pronunciation on this one?",
            "I want to subscribe to a newspaper.",
            "Let's see what the next lesson is about.",
            "Grazie, ho capito.",
            "",
        ],
    )
    def test_keeps_ordinary_speech(self, text: str) -> None:
        assert not _is_stock_hallucination(text)
