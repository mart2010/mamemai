# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: TR-203, TR-204, TR-205 — the client's utterance detector.

The first tests this package has ever had. They exist because the logic they cover spent
its whole life inside a `sounddevice` callback closure, where the 2026-08-03 session
found two defects in it (75% of STT passes spent on room noise; a quarter of real turns
truncated mid-clause) that no test could have caught.

Frames are distinguishable byte strings, not audio: this module's contract is *which*
frames go out and *when* the utterance closes, and it never inspects a sample.
"""

import pytest

from mamemai_client.vad import UtteranceDetector, VadTuning


def _frame(n: int) -> bytes:
    return f"frame-{n}".encode()


def _feed(detector: UtteranceDetector, pattern: str, start: int = 0):
    """Feed one frame per character: `#` is speech-classified, `.` is silence.

    Returns the decisions in order, so a test can assert on the whole run.
    """
    return [
        detector.feed(_frame(start + i), is_speech=(char == "#"))
        for i, char in enumerate(pattern)
    ]


def _sent(decisions) -> list[bytes]:
    return [frame for decision in decisions for frame in decision.send]


# ---------------------------------------------------------------------------
# TR-203 — the onset gate
# ---------------------------------------------------------------------------


def test_burst_shorter_than_onset_opens_nothing_and_sends_nothing():
    """The measured defect: one noise-classified frame used to arm a whole turn."""
    detector = UtteranceDetector(VadTuning(onset_frames=3))

    decisions = _feed(detector, "..#..#.#..")

    assert _sent(decisions) == []
    assert not any(d.started for d in decisions)
    assert not any(d.ended for d in decisions)


def test_a_lapsed_burst_reports_its_length_so_it_can_be_tuned():
    detector = UtteranceDetector(VadTuning(onset_frames=4))

    decisions = _feed(detector, "##.")

    assert [d.ignored_burst_frames for d in decisions] == [0, 0, 2]


def test_silence_with_no_speech_at_all_reports_no_burst():
    """`ignored_burst_frames` must mean "speech was refused", not "a frame was silent" —
    otherwise every quiet frame in an idle room prints a line."""
    detector = UtteranceDetector()

    decisions = _feed(detector, "....")

    assert [d.ignored_burst_frames for d in decisions] == [0, 0, 0, 0]


def test_the_onset_run_must_be_consecutive():
    """Intermittent noise scattered across a second is what the live session saw; it must
    not accumulate into an onset."""
    detector = UtteranceDetector(VadTuning(onset_frames=3))

    decisions = _feed(detector, "#.#.#.#.#.")

    assert not any(d.started for d in decisions)


def test_sustained_speech_opens_the_utterance():
    detector = UtteranceDetector(VadTuning(onset_frames=3, preroll_frames=0))

    decisions = _feed(detector, "###")

    assert [d.started for d in decisions] == [False, False, True]


# ---------------------------------------------------------------------------
# TR-203 — pre-roll: the onset gate must not cost audio
# ---------------------------------------------------------------------------


def test_the_onset_frames_themselves_are_sent_not_swallowed():
    """The gate decides *whether* to open, never *what* is in the utterance — otherwise
    raising onset_frames would quietly clip the first word."""
    detector = UtteranceDetector(VadTuning(onset_frames=3, preroll_frames=0))

    decisions = _feed(detector, "###")

    assert _sent(decisions) == [_frame(0), _frame(1), _frame(2)]


def test_preroll_sends_the_lead_in_silence_ahead_of_the_onset():
    detector = UtteranceDetector(VadTuning(onset_frames=2, preroll_frames=3))

    decisions = _feed(detector, ".....##")

    # The 3 frames before the onset run, then the run itself — not the two older ones.
    assert _sent(decisions) == [_frame(2), _frame(3), _frame(4), _frame(5), _frame(6)]


def test_preroll_is_bounded_by_its_setting_not_by_how_long_the_room_was_quiet():
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=2))

    decisions = _feed(detector, "." * 500 + "#")

    assert len(_sent(decisions)) == 3


def test_preroll_is_not_replayed_on_the_next_utterance():
    """The ring buffer is cleared on open; a stale lead-in reappearing inside the *next*
    utterance would be audio from before the assistant's reply."""
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=2, silence_frames=1))

    first = _feed(detector, "..#..")
    second = _feed(detector, "#", start=100)

    assert any(d.ended for d in first)
    assert _sent(second) == [_frame(100)]


# ---------------------------------------------------------------------------
# TR-203 — what is sent inside an open utterance
# ---------------------------------------------------------------------------


def test_speech_frames_stream_while_the_utterance_is_open():
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=0))

    decisions = _feed(detector, "###")

    assert _sent(decisions) == [_frame(0), _frame(1), _frame(2)]


def test_silence_inside_an_utterance_is_still_excised():
    """TR-203 unchanged: only the pre-roll lead-in reaches Whisper as silence. Whether
    inter-word pauses should be sent too is open and unmeasured (docs/PLAN.md) — pinned
    here so that change is deliberate rather than accidental."""
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=0, silence_frames=10))

    decisions = _feed(detector, "#...#")

    assert _sent(decisions) == [_frame(0), _frame(4)]


# ---------------------------------------------------------------------------
# TR-204 — the hangover
# ---------------------------------------------------------------------------


def test_utterance_ends_after_more_than_silence_frames_of_quiet():
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=0, silence_frames=4))

    decisions = _feed(detector, "#" + "." * 5)

    assert [d.ended for d in decisions] == [False, False, False, False, False, True]


def test_a_pause_shorter_than_the_hangover_does_not_split_the_utterance():
    """The slow-talker defect: at silence_frames=25 a thinking pause ended the turn
    mid-clause. One utterance in, one utterance out."""
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=0, silence_frames=50))

    decisions = _feed(detector, "#" + "." * 40 + "#" + "." * 40 + "#")

    assert not any(d.ended for d in decisions)


def test_silence_before_any_speech_never_ends_an_utterance():
    """TR-204: the countdown only runs once speech has been active."""
    detector = UtteranceDetector(VadTuning(silence_frames=2))

    decisions = _feed(detector, "." * 20)

    assert not any(d.ended for d in decisions)


def test_the_end_decision_reports_speech_and_sent_frames_separately():
    """Speech content and wall-clock audio are different questions, and the ratio between
    them is what says whether an `end_utterance` was worth its STT pass."""
    detector = UtteranceDetector(VadTuning(onset_frames=2, preroll_frames=2, silence_frames=3))

    decisions = _feed(detector, "..##.##" + "." * 4)
    ending = next(d for d in decisions if d.ended)

    assert ending.speech_frames == 4
    assert ending.sent_frames == 6  # 2 pre-roll + 4 speech
    assert ending.send == ()


def test_a_second_utterance_starts_from_clean_counters():
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=0, silence_frames=2))

    _feed(detector, "###...")
    decisions = _feed(detector, "#...", start=100)

    ending = next(d for d in decisions if d.ended)
    assert ending.speech_frames == 1


# ---------------------------------------------------------------------------
# TR-205 — mute
# ---------------------------------------------------------------------------


def test_reset_discards_a_half_armed_onset_run():
    """Playback is not a pause in the user's speech. Without this, the first noise frame
    after `speaking_end` inherits the onset credit built up before the reply."""
    detector = UtteranceDetector(VadTuning(onset_frames=3, preroll_frames=0))

    _feed(detector, "##")
    detector.reset()
    decisions = _feed(detector, "#", start=100)

    assert not decisions[0].started


def test_reset_closes_an_open_utterance_without_emitting_an_end():
    """The server already ran the turn that muted the mic; a late `end_utterance` from
    the client would flush a second buffer against it."""
    detector = UtteranceDetector(VadTuning(onset_frames=1, preroll_frames=0))

    opened = _feed(detector, "##")
    detector.reset()
    after = _feed(detector, "." * 200, start=100)

    assert opened[0].started
    assert not any(d.ended for d in after)


# ---------------------------------------------------------------------------
# Tuning validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"onset_frames": 0},
        {"onset_frames": -1},
        {"silence_frames": 0},
        {"preroll_frames": -1},
    ],
)
def test_unusable_tuning_is_rejected_at_load_rather_than_mid_session(kwargs):
    """These come from a hand-edited mamemai.toml. `onset_frames = 0` would open an
    utterance on a frame that was never speech; `silence_frames = 0` would end one on the
    first quiet frame. Both are better as a startup error than as a mystery in a live
    conversation."""
    with pytest.raises(ValueError):
        VadTuning(**kwargs)
