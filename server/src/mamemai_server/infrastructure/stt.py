# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import re
import unicodedata

import numpy as np
from faster_whisper import WhisperModel

from ..domain.model import Language
from .config import DEFAULT_CPU_THREADS

# Whisper's own confidence that a segment isn't speech at all. Segments at or above this
# are dropped as hallucination-on-silence/noise (e.g. "Thank you for watching!" from a
# quiet room) rather than real utterances — FR-103 only guards an empty transcript, not
# a confidently-wrong non-empty one, so this filter is what actually makes that guard
# effective in practice.
_NO_SPEECH_THRESHOLD = 0.6

# Third line of defence (TR-956), for the case the second one structurally cannot catch:
# Whisper is sometimes *confident* about a hallucination, so `no_speech_prob` stays well
# under the threshold above. Observed live 2026-07-18 on a genuinely silent mic —
# "Thanks for watching, and I'll see you in the next video." — from the best-documented
# family of Whisper artifacts, the caption boilerplate its training data is saturated
# with. These are decodings of noise, not misheard speech, so no acoustic signal
# distinguishes them; only the text does.
_APOSTROPHES = re.compile(r"['’ʼ´`]")
_NON_ALPHANUMERIC = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# Matched as substrings of the normalised segment (see `_normalise`). Each entry is long
# and specific enough that a single user talking to their own assistant does not utter it
# by accident — the bar for adding one, since a false positive silently swallows real
# speech. Extend from observation, not speculation.
_STOCK_HALLUCINATIONS = (
    # English — the YouTube outro family, overwhelmingly the most common
    "thanks for watching",
    "thank you for watching",
    "see you in the next video",
    "see you next time",
    "subscribe to my channel",
    "dont forget to like and subscribe",
    # Caption-credit boilerplate. "amara org" alone covers the French/Spanish/German/
    # Portuguese variants of the same credit line, which differ only in their prose.
    "amara org",
    "subtitles by",
    "transcription by",
    # Italian — the tutor's target language, so its variants are worth naming explicitly
    "sottotitoli e revisione a cura di",
    "sottotitoli creati dalla comunita",
)


def _normalise(text: str) -> str:
    """Fold a segment to bare unaccented lowercase words, so one blocklist entry covers a
    phrase's punctuation, capitalisation and accent variants ("Thanks for watching!" /
    "thanks for watching," / "comunità" / "comunita" — Whisper is inconsistent about
    diacritics on exactly this kind of boilerplate). Apostrophes are deleted rather than
    spaced so "I'll" folds to "ill", not "i ll"; every other separator becomes a space so
    "Amara.org" and "Sous-titres" split into words. The fold is used for matching only —
    the transcript the caller receives keeps its original accents."""
    text = _APOSTROPHES.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip().lower()


def _is_stock_hallucination(text: str) -> bool:
    """True when a segment is caption boilerplate rather than something the user said."""
    normalised = _normalise(text)
    return any(phrase in normalised for phrase in _STOCK_HALLUCINATIONS)


class FasterWhisperSTTService:
    """Adapter for faster-whisper. Language is always auto-detected; language_hint is unused."""

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        compute_type: str = "float16",
        cpu_threads: int = DEFAULT_CPU_THREADS,
    ) -> None:
        self._model = WhisperModel(
            model_path, device=device, compute_type=compute_type, cpu_threads=cpu_threads,
        )

    def transcribe(self, audio: bytes) -> tuple[str, Language]:
        audio_array = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        # vad_filter (Silero VAD, bundled with faster-whisper) drops non-speech stretches
        # before decoding — the first line of defence against hallucinating text out of
        # silence. no_speech_prob is the second: VAD can still let a short noise/breath
        # blip through, and Whisper can still decode a plausible-sounding stock phrase for
        # it. The blocklist is the third, for when Whisper is confident about the
        # hallucination and the second line therefore never fires (TR-956).
        segments, info = self._model.transcribe(audio_array, beam_size=5, vad_filter=True)
        kept: list[str] = []
        for segment in segments:
            if segment.no_speech_prob >= _NO_SPEECH_THRESHOLD:
                continue
            if _is_stock_hallucination(segment.text):
                # Loud on purpose: a false positive here silently eats real speech, and
                # this is the only place it would show up.
                print(f"[stt] dropped stock hallucination (no_speech_prob={segment.no_speech_prob:.2f}): {segment.text!r}")
                continue
            kept.append(segment.text)
        text = " ".join(kept).strip()
        # Diagnostic only (no behavior change): Whisper's own confidence in the detected
        # language — a low value here on an otherwise-fluent transcript is the signature
        # of language misdetection (decoding real speech through the wrong language's
        # rules) rather than the LLM drifting on its own.
        print(f"[stt] language={info.language!r} probability={info.language_probability:.2f}")
        return text, Language(info.language)
