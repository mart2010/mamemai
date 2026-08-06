# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import re

from py3langid.langid import LanguageIdentifier, MODEL_FILE

# Below this many alphabetic characters, a language guess isn't trusted regardless of
# what the classifier says — short interjections ("Ciao!", "No.", "Sì.") are genuinely
# ambiguous to statistical language ID (some are borrowed/international words in the
# first place). Placeholder pending real tuning against live turns, same posture as the
# 0.93/0.75 upsert thresholds and the prefix-scan window.
_MIN_CONFIDENT_CHARS = 8

_NON_WORD = re.compile(r"[^\w]", re.UNICODE)

# Mamemai speaks IETF tags (`Language.code`); py3langid's model is keyed by bare ISO 639-1.
# The two coincide for every supported language except Chinese, and `zh-cn` handed
# straight to `set_languages` raises `ValueError: Unknown language code zh-cn` — which,
# from inside the synthesis loop, kills the turn. Translated in on the way down and back
# out on the way up, so callers only ever see their own vocabulary: `_session_voice`
# looks up `voices["zh-cn"]` and `KOKORO_DEFAULT_VOICES["zh-cn"]`, and a bare "zh" would
# silently miss both and fall through to an English voice.
_TO_ISO_639_1 = {"zh-cn": "zh"}


class Py3LangidLanguageDetector:
    """Restricts each call to the given `candidates` (an instance-scoped identifier,
    not the module-level global — safe under this project's single-connection,
    single-turn-at-a-time model, see INV-3). No network/model download: py3langid
    ships its compact model inline, unlike the STT/TTS/embedding adapters."""

    def __init__(self) -> None:
        self._identifier = LanguageIdentifier.from_pickled_model(MODEL_FILE)
        # Captured before any set_languages call trims it.
        self._known = frozenset(self._identifier.nb_classes)

    def detect(self, text: str, candidates: tuple[str, ...]) -> str | None:
        if len(_NON_WORD.sub("", text)) < _MIN_CONFIDENT_CHARS:
            return None
        # A candidate the model has never seen is dropped, not fatal: the live path must
        # not die over a language code. Losing one candidate costs at worst a segment
        # spoken in the voice already active — the same outcome as a low-confidence call.
        by_iso = {_TO_ISO_639_1.get(code, code): code for code in candidates}
        usable = {iso: code for iso, code in by_iso.items() if iso in self._known}
        # Nothing to tell apart — a one-candidate classification is a foregone conclusion.
        if len(usable) < 2:
            return None
        self._identifier.set_languages(sorted(usable))
        lang, _score = self._identifier.classify(text)
        return usable[lang]
