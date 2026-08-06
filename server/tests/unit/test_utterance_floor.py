# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: TR-958, TR-101 — the server-side floor below which end_utterance costs no STT pass."""

from mamemai_server.infrastructure.config import DEFAULT_MIN_UTTERANCE_MS
from mamemai_server.server import STT_BYTES_PER_SAMPLE, STT_SAMPLE_RATE, utterance_duration_ms


def _audio(ms: int) -> bytes:
    return b"\x00" * (ms * STT_SAMPLE_RATE * STT_BYTES_PER_SAMPLE // 1000)


def test_duration_is_measured_against_the_wire_format_not_the_byte_count():
    """Spec: TR-101 — PCM int16 mono at 16 kHz, i.e. 32 bytes per millisecond. Reading
    this as 16-bit *samples* per ms, or as float32, would put the floor out by 2x and
    silently change which turns get dropped."""
    assert utterance_duration_ms(_audio(1000)) == 1000
    assert utterance_duration_ms(_audio(300)) == 300


def test_an_empty_buffer_measures_zero():
    assert utterance_duration_ms(b"") == 0


def test_a_single_vad_frame_lands_far_below_the_default_floor():
    """The measured failure: a 30 ms noise blip used to buy a full `medium`-model pass
    plus language detection, 49 times in a 65-utterance session."""
    one_frame_ms = 30

    assert utterance_duration_ms(_audio(one_frame_ms)) < DEFAULT_MIN_UTTERANCE_MS


def test_a_short_real_word_clears_the_default_floor():
    """The direction that must not break. TR-203 strips inter-word silence, so a one-word
    French answer arrives as only its voiced audio — "oui" is ~300 ms of it. The floor is
    set to catch what is unambiguously not speech, and a false positive here swallows a
    real turn in silence (the TR-956 posture)."""
    assert utterance_duration_ms(_audio(350)) >= DEFAULT_MIN_UTTERANCE_MS


def test_duration_never_rounds_up_past_the_floor():
    """Integer division floors, so a buffer one byte short of 300 ms reads as 299 and is
    dropped — the safe rounding direction for a cost gate, but only if it is deliberate."""
    just_under = _audio(DEFAULT_MIN_UTTERANCE_MS)[:-1]

    assert utterance_duration_ms(just_under) == DEFAULT_MIN_UTTERANCE_MS - 1
