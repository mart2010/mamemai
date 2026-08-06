# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from uuid import UUID

from ..domain.model import GENERAL_ASSISTANT_ID, AssistantPersona, Directive, Language
from .ports import DirectiveRepository

# Deterministic Directive trigger vocabulary (FR-207) — replaces embedding-similarity
# matching with a plain "these keywords, in a short utterance" rule, so a directive
# match no longer costs an embedding call on the per-turn hot path (that unconditional
# extra embed() per turn was the dominant avoidable CPU cost on CPU-only hardware,
# 2026-07-30). Bounded to TR-505's supported languages (`domain.model.
# SUPPORTED_LANGUAGES`); an unlisted language code falls back to the English wording
# rather than producing a broken/empty trigger. Translations are a first pass, not
# reviewed by a native speaker of each language — expect to revise once there's live
# usage to tune against, same posture as the thresholds this replaces.
_SWITCH_VERB: dict[str, str] = {
    "en": "switch",
    "fr": "basculer",
    "es": "cambiar",
    "it": "passa",
    "pt": "mudar",
    "ja": "切り替え",
    "zh-cn": "切换",
}
_GENERAL_ASSISTANT_NAME: dict[str, str] = {
    "en": "general assistant",
    "fr": "assistant général",
    "es": "asistente general",
    "it": "assistente generale",
    "pt": "assistente geral",
    "ja": "汎用アシスタント",
    "zh-cn": "通用助手",
}
# Asking mamemai to answer in a given language (FR-701, 2026-08-02). Same first-pass
# translation posture as the tables above — not reviewed by native speakers, expect to
# revise against live usage.
_SPEAK_VERB: dict[str, str] = {
    "en": "speak",
    "fr": "parle",
    "es": "habla",
    "it": "parla",
    "pt": "fala",
    "ja": "話して",
    "zh-cn": "说",
}

# Language names, indexed [wording language][target language]. Only the diagonal is used
# for `set_response_language` since 2026-08-02 (see `ensure_language_directives`) — the
# off-diagonal entries stay because the persona-switch tables share the same shape and a
# translated language name is worth keeping around.
_LANGUAGE_NAME: dict[str, dict[str, str]] = {
    "en": {"en": "english", "fr": "french", "es": "spanish", "it": "italian",
           "pt": "portuguese", "ja": "japanese", "zh-cn": "chinese"},
    "fr": {"en": "anglais", "fr": "français", "es": "espagnol", "it": "italien",
           "pt": "portugais", "ja": "japonais", "zh-cn": "chinois"},
    "es": {"en": "inglés", "fr": "francés", "es": "español", "it": "italiano",
           "pt": "portugués", "ja": "japonés", "zh-cn": "chino"},
    "it": {"en": "inglese", "fr": "francese", "es": "spagnolo", "it": "italiano",
           "pt": "portoghese", "ja": "giapponese", "zh-cn": "cinese"},
    "pt": {"en": "inglês", "fr": "francês", "es": "espanhol", "it": "italiano",
           "pt": "português", "ja": "japonês", "zh-cn": "chinês"},
    "ja": {"en": "英語", "fr": "フランス語", "es": "スペイン語", "it": "イタリア語",
           "pt": "ポルトガル語", "ja": "日本語", "zh-cn": "中国語"},
    "zh-cn": {"en": "英语", "fr": "法语", "es": "西班牙语", "it": "意大利语",
              "pt": "葡萄牙语", "ja": "日语", "zh-cn": "中文"},
}

ACTION_SWITCH_PERSONA = "switch_persona"
ACTION_SET_RESPONSE_LANGUAGE = "set_response_language"

_FALLBACK_LANGUAGE_CODE = "en"

# `max_words` scales with how many keywords a trigger requires — a longer persona name
# means more required keywords, so the surrounding "switch me to ..." words need more
# headroom too. Floor of 6 matches the shortest real case (a one-word persona name: verb
# + name = 2 keywords) with room for a natural-sounding phrase around them. Both values
# are placeholders pending real tuning against live turns, same posture as the
# similarity thresholds elsewhere in this codebase.
_MIN_MAX_WORDS = 6
_MAX_WORDS_HEADROOM = 3


def build_trigger(action_word: str, name_words: list[str]) -> dict:
    keywords = [action_word, *name_words]
    return {"keywords": keywords, "max_words": max(_MIN_MAX_WORDS, len(keywords) + _MAX_WORDS_HEADROOM)}


def matches_trigger(trigger: dict, text: str) -> bool:
    """True if every one of `trigger["keywords"]` occurs (case-insensitive substring
    match) in `text`, and `text` is no longer than `trigger["max_words"]` words
    (`str.split()`, same measure `DefaultRecallGate` already uses, TR-314). That word
    count under-counts languages with no whitespace word boundaries (ja/zh-cn) — for
    those the ceiling becomes a no-op rather than wrong; the keyword conjunction alone
    still keeps false positives rare. An empty/missing `keywords` list never matches."""
    keywords = trigger.get("keywords")
    if not keywords:
        return False
    max_words = trigger.get("max_words")
    if max_words is not None and len(text.split()) > max_words:
        return False
    lowered = text.lower()
    return all(keyword.lower() in lowered for keyword in keywords)


class PersonaDirectiveSync:
    """Keeps Directive rows (FR-207, first-class — `domain.model.Directive`, its own
    table, not a Concept) in sync with persona lifecycle. A persona's removal needs no
    explicit cleanup call here: `target_persona_id` is a real FK with
    `ON DELETE CASCADE` (INV-9), unlike the old Concept-backed scheme, which had to
    hand-roll `sync_removed` because a GA-owned Concept row had no FK relationship to
    the persona it actually targeted."""

    def __init__(self, directive_repo: DirectiveRepository) -> None:
        self._directive_repo = directive_repo

    def _write_directive(
        self, target_persona_id: UUID, action: str, description_and_words_by_lang: dict[str, tuple[str, list[str]]],
    ) -> None:
        for lang, (description_prefix, name_words) in description_and_words_by_lang.items():
            verb = _SWITCH_VERB.get(lang, _SWITCH_VERB[_FALLBACK_LANGUAGE_CODE])
            self._directive_repo.upsert(Directive(
                id=None,
                target_persona_id=target_persona_id,
                action=action,
                language=Language(lang),
                trigger=build_trigger(verb, name_words),
                description=f"{verb.capitalize()} {description_prefix}.",
            ))

    def ensure_language_directives(self, installed_languages: list[Language]) -> None:
        """One "answer me in X" directive per installed language (FR-701, FR-705, FR-708),
        worded **in X and only in X** — "speak english", "parle français", "日本語で話して".
        Idempotent — safe on every startup.

        Generated only for *installed* languages, so a matching trigger already implies
        the language is speakable on this machine; the executing side re-checks anyway,
        because a directive row outlives the config that produced it.

        Until 2026-08-02 a second phrasing was generated per target, in the language the
        user was already speaking ("parle-moi en anglais" for a French speaker wanting
        English), so that asking for a language you cannot yet produce would not require
        producing it. It was dropped because the phrasing is not neutral: the directive
        utterance becomes the last user turn in the LLM's context, so a request made in
        the *old* language leaves the model with the switch instruction in the system
        prompt and a fresh example of the old language immediately below it — and
        llama3.1:8b was observed answering the old language for a turn anyway. Asking in
        the target language puts the evidence and the instruction on the same side.
        The cost is real and accepted: a language you cannot pronounce at all must now be
        reached from the wizard rather than by voice.

        A language with no wording of its own in the tables above is skipped rather than
        given the English fallback the other directives use — an English-worded trigger
        is exactly what this rule exists to remove.

        These directives target no persona, so nothing cascades them away (INV-9): they
        are global and outlive every persona, which is right for a setting about how the
        user is spoken to rather than about who is speaking.
        """
        installed_codes = {lang.code for lang in installed_languages}
        already: set[str] = set()
        for existing in self._directive_repo.list_all():
            if existing.action != ACTION_SET_RESPONSE_LANGUAGE:
                continue
            target_code = existing.target_language.code if existing.target_language else None
            # Pruned here, not just left unwritten: a row outlives the config that made it,
            # so an off-diagonal phrasing from before 2026-08-02 (or a target since
            # uninstalled) would otherwise keep matching for the life of the database.
            if target_code is None or target_code not in installed_codes or existing.language.code != target_code:
                if existing.id is not None:
                    self._directive_repo.delete(existing.id)
                continue
            already.add(target_code)

        for target in installed_languages:
            if target.code in already:
                continue
            verb = _SPEAK_VERB.get(target.code)
            name = _LANGUAGE_NAME.get(target.code, {}).get(target.code)
            if verb is None or name is None:
                continue
            self._directive_repo.upsert(Directive(
                id=None,
                target_persona_id=None,
                action=ACTION_SET_RESPONSE_LANGUAGE,
                language=target,
                trigger=build_trigger(verb, name.split()),
                description=f"{verb.capitalize()} {name}.",
                target_language=target,
            ))

    def sync_created(self, persona: AssistantPersona) -> None:
        """Idempotent: a persona that already has a "switch to me" directive (e.g. a
        bundle reinstall onto an existing persona) is left alone. Trigger language
        coverage = `persona.languages` (bundle_install.py already folds the user's
        primary language into that list, FR-701) — falls back to the persona's own
        `response_language` if a live-created persona left `languages` empty."""
        existing = self._directive_repo.list_all()
        if any(d.target_persona_id == persona.id for d in existing):
            return
        languages = persona.languages or [persona.response_language]
        name_words = persona.name.lower().split()
        by_lang = {lang.code: (f"to {persona.name}", name_words) for lang in languages}
        self._write_directive(persona.id, "switch_persona", by_lang)

    def ensure_return_to_general_assistant(self, languages: list[Language]) -> None:
        """Idempotent — safe to call on every server startup. `languages` = this
        machine's installed languages (FR-705) — the full set a user could plausibly
        be mid-conversation in when asking to come back to GA."""
        existing = self._directive_repo.list_all()
        if any(d.target_persona_id == GENERAL_ASSISTANT_ID for d in existing):
            return
        # Unlike a user-authored persona name, "general assistant" is GA's own fixed
        # wording — translated per language rather than carried over verbatim.
        by_lang = {}
        for lang in languages:
            ga_name = _GENERAL_ASSISTANT_NAME.get(lang.code, _GENERAL_ASSISTANT_NAME[_FALLBACK_LANGUAGE_CODE])
            by_lang[lang.code] = (f"back to the {ga_name}", ga_name.lower().split())
        self._write_directive(GENERAL_ASSISTANT_ID, "switch_persona", by_lang)
