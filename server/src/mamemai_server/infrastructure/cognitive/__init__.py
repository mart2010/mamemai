# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""The cognitive-training persona (docs/BRIEF_persona_cognitive.md) — persona #2.

Deliberately dissimilar to the language tutor: it authors no curriculum and ships no
content, working instead from whatever the user's own memory substrate already holds.
That makes it the first *consumer* persona, and the reason the persona contract became
designable — every disagreement between it and the tutor is a point where generic code
had quietly frozen one persona's choices.
"""
from .assessment import CognitiveAssessmentStrategy
from .selection import STRATEGY_NAME, CognitiveSelectionStrategy

__all__ = ["STRATEGY_NAME", "CognitiveAssessmentStrategy", "CognitiveSelectionStrategy"]
