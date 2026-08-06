# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Utterance detection (TR-203/TR-204): which captured frames are worth sending, and
when the user has finished speaking.

Pure by design — it sees a `bytes` frame and a boolean, never a microphone or a socket.
The logic used to live inside the `sounddevice` InputStream callback in `client.py`,
where it could not be tested at all, and both defects measured in the 2026-08-03 live
session were in it:

  * **75% of STT passes were spent on silence.** 49 of 65 `end_utterance` events
    transcribed to nothing. Room noise re-triggers the VAD the moment TR-205 restores
    the mic after playback, and a *single* noise-classified frame was enough to arm the
    end-of-utterance countdown — so each burst cost a full `medium`-model Whisper pass
    plus language detection. `onset_frames` is the fix: an utterance now has to sustain
    speech before it opens at all.
  * **Real turns were cut off mid-clause**, four of sixteen, always at a natural
    thinking pause. The ~750 ms hangover is shorter than a pause in reasoning-aloud
    speech, and how long a speaker pauses is a property of the speaker, not of the
    software — hence `silence_frames` is configurable rather than a constant.

`preroll_frames` exists because of the first fix: requiring N speech frames before
opening would otherwise clip N frames off the front of every real utterance. The ring
buffer holds them (plus a little genuine lead-in silence) and flushes on open, so the
onset gate costs no audio. That lead-in is also the only silence Whisper receives —
inter-word silence within an utterance is still excised per TR-203, which is a separate,
unmeasured question (docs/PLAN.md).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# Consecutive speech-classified frames needed to open an utterance. 3 frames is 90 ms —
# shorter than any real word, so it costs nothing on speech, while a 1-2 frame noise
# blip can no longer arm a turn. Deliberately conservative in the "never lose real
# speech" direction for a first cut: raise it if noise still gets through, using the
# `[vad] ignored ...` lines the client prints for each rejected burst.
DEFAULT_ONSET_FRAMES = 3

# Silent frames tolerated inside an utterance before it is considered finished. 50
# frames is ~1.5 s, up from the 25 (~750 ms) that truncated a quarter of the turns in
# the 2026-08-03 session. It is a per-speaker setting: a fast talker should lower it,
# and every increase is added directly to turn latency, since the server cannot start
# transcribing until this elapses.
DEFAULT_SILENCE_FRAMES = 50

# Frames of audio preceding the onset run that are sent with it. 10 frames is 300 ms of
# lead-in, so Whisper gets the attack of the first word rather than a hard cut into a
# vowel already in progress.
DEFAULT_PREROLL_FRAMES = 10


@dataclass(frozen=True)
class VadTuning:
    """`[vad]` in the client's `mamemai.toml` (TR-201)."""

    onset_frames: int = DEFAULT_ONSET_FRAMES
    silence_frames: int = DEFAULT_SILENCE_FRAMES
    preroll_frames: int = DEFAULT_PREROLL_FRAMES

    def __post_init__(self) -> None:
        if self.onset_frames < 1:
            raise ValueError("[vad] onset_frames must be at least 1")
        if self.silence_frames < 1:
            raise ValueError("[vad] silence_frames must be at least 1")
        if self.preroll_frames < 0:
            raise ValueError("[vad] preroll_frames must not be negative")


@dataclass(frozen=True)
class FrameDecision:
    """What the caller should do about the frame it just fed in.

    Counts are in frames rather than milliseconds so this module needs no opinion about
    `FRAME_DURATION`; the client converts them for its diagnostics.
    """

    send: tuple[bytes, ...] = ()
    started: bool = False
    ended: bool = False
    # Non-zero on the frame that ends a speech burst too short to have opened an
    # utterance — i.e. noise this gate just refused to spend an STT pass on. The only
    # evidence the user has that `onset_frames` is doing anything, or is set too high.
    ignored_burst_frames: int = 0
    # Set on `ended` only: speech-classified frames in the utterance, and total frames
    # actually sent (which includes the pre-roll).
    speech_frames: int = 0
    sent_frames: int = 0


class UtteranceDetector:
    """The TR-203/TR-204 state machine, one frame at a time."""

    def __init__(self, tuning: VadTuning | None = None) -> None:
        self._tuning = tuning or VadTuning()
        self._preroll: deque[bytes] = deque(
            maxlen=self._tuning.preroll_frames + self._tuning.onset_frames
        )
        self.reset()

    def reset(self) -> None:
        """Drop all in-flight state.

        Called on mic mute (TR-205) as well as at end of utterance: playback is not a
        pause in the user's speech, and carrying a half-armed onset run across it is how
        the first noise frame after `speaking_end` used to inherit credit for whatever
        preceded the assistant's reply.
        """
        self._active = False
        self._speech_run = 0
        self._silence_run = 0
        self._speech_frames = 0
        self._sent_frames = 0
        self._preroll.clear()

    def feed(self, frame: bytes, is_speech: bool) -> FrameDecision:
        return self._feed_active(frame, is_speech) if self._active else self._feed_idle(frame, is_speech)

    def _feed_idle(self, frame: bytes, is_speech: bool) -> FrameDecision:
        self._preroll.append(frame)

        if not is_speech:
            lapsed, self._speech_run = self._speech_run, 0
            return FrameDecision(ignored_burst_frames=lapsed)

        self._speech_run += 1
        if self._speech_run < self._tuning.onset_frames:
            return FrameDecision()

        # Opening. The ring buffer already holds this run *and* the lead-in before it,
        # so the whole thing goes out together and nothing is clipped.
        opening = tuple(self._preroll)
        self._preroll.clear()
        self._active = True
        self._speech_frames = self._speech_run
        self._sent_frames = len(opening)
        self._speech_run = 0
        self._silence_run = 0
        return FrameDecision(send=opening, started=True)

    def _feed_active(self, frame: bytes, is_speech: bool) -> FrameDecision:
        if is_speech:
            self._silence_run = 0
            self._speech_frames += 1
            self._sent_frames += 1
            return FrameDecision(send=(frame,))

        self._silence_run += 1
        if self._silence_run <= self._tuning.silence_frames:
            return FrameDecision()

        decision = FrameDecision(
            ended=True,
            speech_frames=self._speech_frames,
            sent_frames=self._sent_frames,
        )
        self.reset()
        return decision
