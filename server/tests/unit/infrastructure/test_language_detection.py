# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""The real py3langid adapter, not the fake: every unit test of the synthesis loop goes
through FakeLanguageDetector, so the candidate vocabulary it is actually handed was
never exercised until a live turn crashed on it."""
import pytest

from mamemai_server.domain.model import SUPPORTED_LANGUAGES
from mamemai_server.infrastructure.language_detection import Py3LangidLanguageDetector


@pytest.fixture(scope="module")
def detector() -> Py3LangidLanguageDetector:
    return Py3LangidLanguageDetector()


class TestPy3LangidLanguageDetector:
    def test_every_supported_language_is_an_acceptable_candidate(self, detector):
        """Spec: TR-307 — candidates are IETF tags (Language.code) but py3langid's model
        is keyed by ISO 639-1, so `zh-cn` used to raise `ValueError: Unknown language
        code zh-cn` from inside the synthesis loop and kill the turn. Every installed
        language reaches this call (TR-305), so the whole supported set must pass."""
        candidates = tuple(lang.code for lang in SUPPORTED_LANGUAGES)
        assert detector.detect("This is an ordinary English sentence, long enough to judge.", candidates) == "en"

    def test_returns_the_caller_s_own_code_not_the_iso_one(self, detector):
        """Spec: TR-307 — the result keys `persona.voices` and KOKORO_DEFAULT_VOICES,
        both of which use "zh-cn"; a bare "zh" would miss both and fall through to the
        anchor voice, i.e. Chinese text read by an English mouth."""
        assert detector.detect("这是一个足够长的中文句子，用来判断语言。", ("en", "zh-cn")) == "zh-cn"

    def test_french_segment_after_a_language_switch(self, detector):
        """Spec: TR-305 — the case this detection exists for: the persona's configured
        language and the language it actually wrote are both candidates, and the text
        decides which voice speaks."""
        assert detector.detect("Bonjour, je vais vous expliquer cela en quelques phrases.", ("en", "fr")) == "fr"

    def test_short_segment_is_not_trusted(self, detector):
        """Spec: TR-307 — below the confident-length floor the caller keeps whatever
        voice is already active rather than flapping on a coin-toss."""
        assert detector.detect("Ciao!", ("en", "it")) is None

    def test_single_candidate_is_not_a_classification(self, detector):
        """Spec: TR-307 — with one candidate the answer is a foregone conclusion; say
        "don't know" instead, so the caller keeps the active voice."""
        assert detector.detect("This is an ordinary English sentence, long enough to judge.", ("en",)) is None

    def test_unknown_candidate_is_dropped_not_fatal(self, detector):
        """Spec: TR-307 — a code the model has never seen must never kill the live turn;
        the remaining candidates still decide."""
        assert detector.detect("Bonjour, je vais vous expliquer cela en quelques phrases.", ("en", "fr", "xx")) == "fr"
