# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Speech style: measuring user turns (StyleProfile), and the two Speech Archetypes
(hesitant, fluent) whose Style Targets the Simulated User must hit. Pure.

The targets are DESIGN TARGETS chosen from general knowledge of spontaneous speech, not
measurements of any real person (EV-INV-9). They describe the *transcribed* text the assistant
receives, where STT tends to clean up some fillers; they are revisited against the Calibration
Set and, later, a TTS→STT round trip."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from .model import EvalLanguage, SpeechArchetype

# Disfluency lexicons: short, closed lists of tokens that signal spoken, unplanned speech.
# Only UNAMBIGUOUS fillers are counted. Discourse words that are also ordinary vocabulary
# ("alors", "bon", "du coup" / "so", "well", "like") are deliberately excluded: a fluent speaker
# starts sentences with them, and counting them would make every archetype look hesitant.
_FILLERS = {
    EvalLanguage.FR: {"euh", "heu", "ben", "bah", "hum", "hmm"},
    EvalLanguage.EN: {"um", "uh", "er", "erm", "hmm", "you know", "i mean"},
}
_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


@dataclass(frozen=True)
class StyleProfile:
    language: EvalLanguage
    n_turns: int
    words_p10: float
    words_p50: float
    words_p90: float
    words_mean: float
    fragment_rate: float  # share of turns of <= 3 words
    question_rate: float  # share ending in '?'
    no_final_punct_rate: float
    filler_per_100_words: float
    ellipsis_rate: float  # share containing '…' or '...'
    repeated_word_rate: float  # share with an immediate repetition ("je je pense") — STT/stutter signal
    lowercase_start_rate: float



def _quantile(sorted_vals: list[int], q: float) -> float:
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _has_repetition(words: list[str]) -> bool:
    return any(a.lower() == b.lower() for a, b in zip(words, words[1:]))


def compute_style(turns: list[str], language: EvalLanguage) -> StyleProfile:
    turns = [t.strip() for t in turns if t.strip()]
    if not turns:
        raise ValueError("no turns to profile")
    words = [_WORD.findall(t) for t in turns]
    counts = sorted(len(w) for w in words)
    total_words = sum(counts) or 1
    fillers = _FILLERS[language]
    filler_hits = sum(
        sum(1 for f in fillers if re.search(rf"(?<!\w){re.escape(f)}(?!\w)", t.lower())) for t in turns)
    n = len(turns)

    def share(pred) -> float:
        return sum(1 for t, w in zip(turns, words) if pred(t, w)) / n

    return StyleProfile(
        language=language, n_turns=n,
        words_p10=_quantile(counts, 0.1), words_p50=_quantile(counts, 0.5), words_p90=_quantile(counts, 0.9),
        words_mean=statistics.fmean(counts),
        fragment_rate=share(lambda t, w: len(w) <= 3),
        question_rate=share(lambda t, w: t.endswith("?")),
        no_final_punct_rate=share(lambda t, w: t[-1] not in ".?!…"),
        filler_per_100_words=100 * filler_hits / total_words,
        ellipsis_rate=share(lambda t, w: "…" in t or "..." in t),
        repeated_word_rate=share(lambda t, w: _has_repetition(w)),
        lowercase_start_rate=share(lambda t, w: t[0].isalpha() and t[0].islower()),
    )


# ---------------------------------------------------------------------------
# Speech Archetypes and Style Targets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StyleTarget:
    """Inclusive (low, high) range per StyleProfile metric. A generated user side conforms when
    its measured StyleProfile falls inside every range (Style Conformance)."""
    archetype: SpeechArchetype
    description: str
    ranges: dict[str, tuple[float, float]]

    def violations(self, measured: StyleProfile) -> dict[str, tuple[float, tuple[float, float]]]:
        """Metrics outside their range: {metric: (measured, (low, high))}. Empty = conforms."""
        out = {}
        for metric, (lo, hi) in self.ranges.items():
            value = getattr(measured, metric)
            if not lo <= value <= hi:
                out[metric] = (value, (lo, hi))
        return out

    def to_prompt_block(self) -> str:
        """Instruction text for the Simulated User / Dialog writer (numbers and behaviours only)."""
        rng = ", ".join(f"{m} {lo:g}–{hi:g}" for m, (lo, hi) in self.ranges.items())
        return f"{self.description}\nMeasurable targets over the user's turns: {rng}."


STYLE_TARGETS: dict[SpeechArchetype, StyleTarget] = {
    SpeechArchetype.HESITANT: StyleTarget(
        SpeechArchetype.HESITANT,
        "A hesitant speaker: thinks while talking, so turns are short and broken; frequent filled pauses "
        "(euh / um), trailing pauses written as '…', false starts and repeated words ('je je pense'), "
        "trailing off without a final full stop, sometimes seeking reassurance with a question. Rarely "
        "produces a long, fully-formed paragraph.",
        {"words_p50": (6, 12), "words_p90": (14, 32), "fragment_rate": (0.20, 0.40), "question_rate": (0.15, 0.35),
         "no_final_punct_rate": (0.30, 0.60), "filler_per_100_words": (4.0, 9.0), "ellipsis_rate": (0.25, 0.50),
         "repeated_word_rate": (0.10, 0.30), "lowercase_start_rate": (0.15, 0.40)},
    ),
    SpeechArchetype.FLUENT: StyleTarget(
        SpeechArchetype.FLUENT,
        "A fluent speaker: steady, well-organised flow in complete sentences, few or no fillers, no "
        "false starts, ends turns with proper punctuation, occasionally gives a longer explanatory turn.",
        {"words_p50": (12, 22), "words_p90": (30, 70), "fragment_rate": (0.0, 0.08), "question_rate": (0.15, 0.35),
         "no_final_punct_rate": (0.0, 0.10), "filler_per_100_words": (0.0, 1.0), "ellipsis_rate": (0.0, 0.04),
         "repeated_word_rate": (0.0, 0.02), "lowercase_start_rate": (0.0, 0.05)},
    ),
}
