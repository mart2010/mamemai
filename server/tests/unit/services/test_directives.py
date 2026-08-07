from datetime import datetime, UTC
from uuid import uuid4

from mamemai_server.domain.model import GENERAL_ASSISTANT_ID, AssistantPersona, Directive, Language
from mamemai_server.services.directives import (
    ACTION_END_SESSION,
    ACTION_SET_RESPONSE_LANGUAGE,
    PersonaDirectiveSync,
    build_trigger,
    matches_trigger,
)

from tests.fakes.fakes import FakeDirectiveRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _persona(name: str = "Tutor", languages: list[Language] | None = None) -> AssistantPersona:
    return AssistantPersona(
        id=uuid4(), name=name, system_prompt="You teach.", languages=languages or [],
        response_language=Language("en"), voices={"default": "af_heart"},
        is_system=False, created_at=_now(), updated_at=_now(),
    )


class TestSyncCreated:
    def test_creates_switch_directive(self):
        """Spec: FR-207"""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        persona = _persona("Tutor Italiano")

        sync.sync_created(persona)

        directives = directive_repo.list_all()
        assert directives
        assert all(d.target_persona_id == persona.id for d in directives)
        assert all(d.action == "switch_persona" for d in directives)
        # Deterministic keyword rule, derived from the persona's own name.
        trigger = directives[0].trigger
        assert "tutor" in trigger["keywords"]
        assert "italiano" in trigger["keywords"]
        assert matches_trigger(trigger, "Switch to Tutor Italiano")
        assert not matches_trigger(trigger, "I'd like to talk about my tutor sometime, italiano style")

    def test_falls_back_to_response_language_when_persona_languages_empty(self):
        """Spec: FR-207 — a live-created persona (CreatePersona) may leave `languages`
        empty; the directive must still be generated, in the persona's own
        response_language."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        persona = _persona(languages=[])

        sync.sync_created(persona)

        directives = directive_repo.list_all()
        assert len(directives) == 1
        assert directives[0].language == persona.response_language

    def test_generates_one_trigger_per_persona_language(self):
        """Spec: FR-207 — additional persona languages get their own translated
        trigger, so the switch also works while the user is mid-conversation in one of
        the persona's other languages."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        persona = _persona("Tutor", languages=[Language("en"), Language("it")])

        sync.sync_created(persona)

        directives = directive_repo.list_all()
        assert {d.language for d in directives} == {Language("en"), Language("it")}
        assert all(d.target_persona_id == persona.id for d in directives)
        it_trigger = next(d.trigger for d in directives if d.language == Language("it"))
        assert matches_trigger(it_trigger, "Passa a Tutor")

    def test_idempotent_on_repeated_call(self):
        """Spec: FR-207 — a bundle reinstall onto an existing persona must not
        duplicate its directives."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        persona = _persona()

        sync.sync_created(persona)
        first_count = len(directive_repo.list_all())
        sync.sync_created(persona)

        assert len(directive_repo.list_all()) == first_count

    def test_different_personas_get_independent_directives(self):
        """Spec: FR-207"""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        a, b = _persona("Coach"), _persona("Tutor")

        sync.sync_created(a)
        sync.sync_created(b)

        directives = directive_repo.list_all()
        targets = {d.target_persona_id for d in directives}
        assert targets == {a.id, b.id}


class TestEnsureReturnToGeneralAssistant:
    def test_creates_return_directive(self):
        """Spec: FR-207"""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_return_to_general_assistant([Language("en")])

        directives = directive_repo.list_all()
        assert directives
        assert all(d.target_persona_id == GENERAL_ASSISTANT_ID for d in directives)
        assert matches_trigger(directives[0].trigger, "Switch back to the general assistant")

    def test_generates_one_trigger_per_installed_language(self):
        """Spec: FR-207, FR-705 — covers every language a user could plausibly be
        mid-conversation in when asking to come back to GA."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_return_to_general_assistant([Language("en"), Language("it")])

        directives = directive_repo.list_all()
        assert {d.language for d in directives} == {Language("en"), Language("it")}
        it_trigger = next(d.trigger for d in directives if d.language == Language("it"))
        assert matches_trigger(it_trigger, "Passa all'assistente generale")

    def test_idempotent_across_repeated_startups(self):
        """Spec: FR-207 — safe to call on every server startup."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_return_to_general_assistant([Language("en")])
        first_count = len(directive_repo.list_all())
        sync.ensure_return_to_general_assistant([Language("en")])

        assert len(directive_repo.list_all()) == first_count


class TestEnsureLanguageDirectives:
    def test_one_directive_per_installed_language_worded_in_that_language(self):
        """Spec: FR-708, FR-701, FR-705 — since 2026-08-02 the request must be made in
        the language being asked for: the utterance becomes the last user turn the model
        sees, so asking in the old language hands it a fresh example of the language it
        was just told to stop using."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_language_directives([Language("en"), Language("fr")])

        directives = directive_repo.list_all()
        assert len(directives) == 2
        assert all(d.action == ACTION_SET_RESPONSE_LANGUAGE for d in directives)
        assert all(d.target_persona_id is None for d in directives)  # global, INV-9 cascades nothing
        assert all(d.language == d.target_language for d in directives)
        by_target = {d.target_language.code: d for d in directives}
        assert matches_trigger(by_target["fr"].trigger, "Parle-moi en français")
        assert matches_trigger(by_target["en"].trigger, "Speak to me in english")

    def test_request_in_another_language_no_longer_matches(self):
        """Spec: FR-708 — the dropped phrasing, stated as a test so the removal is
        deliberate rather than an accident of table lookups: a French speaker asking for
        English in French is not a directive any more."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_language_directives([Language("en"), Language("fr")])

        assert not any(matches_trigger(d.trigger, "Parle-moi en anglais") for d in directive_repo.list_all())

    def test_prunes_an_off_diagonal_row_left_by_the_old_rule(self):
        """Spec: FR-708 — a directive row outlives the config that produced it, so the
        pre-2026-08-02 phrasings must be deleted, not merely left ungenerated."""
        directive_repo = FakeDirectiveRepository()
        directive_repo.upsert(Directive(
            id=None, target_persona_id=None, action=ACTION_SET_RESPONSE_LANGUAGE,
            language=Language("fr"), trigger=build_trigger("parle", ["anglais"]),
            description="Parle anglais.", target_language=Language("en"),
        ))
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_language_directives([Language("en"), Language("fr")])

        assert not any(matches_trigger(d.trigger, "Parle-moi en anglais") for d in directive_repo.list_all())
        assert len(directive_repo.list_all()) == 2

    def test_prunes_a_language_no_longer_installed(self):
        """Spec: FR-705, FR-708 — a language dropped from the wizard's selection loses
        its directive, rather than leaving a trigger that switches the persona to a voice
        this machine can no longer synthesise."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        sync.ensure_language_directives([Language("en"), Language("it")])

        sync.ensure_language_directives([Language("en")])

        directives = directive_repo.list_all()
        assert [d.target_language.code for d in directives] == ["en"]

    def test_idempotent_across_repeated_startups(self):
        """Spec: FR-708 — called on every server start."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_language_directives([Language("en"), Language("fr")])
        first = [(d.id, d.trigger) for d in directive_repo.list_all()]
        sync.ensure_language_directives([Language("en"), Language("fr")])

        assert [(d.id, d.trigger) for d in directive_repo.list_all()] == first


class TestEnsureEndSessionDirective:
    def test_one_directive_per_installed_language_targets_no_persona(self):
        """Spec: FR-115 — deterministic voice-triggered session end, one row per
        installed language, global like set_response_language (ending the session
        isn't a persona's call to make)."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_end_session_directive([Language("en"), Language("fr")])

        directives = directive_repo.list_all()
        assert len(directives) == 2
        assert all(d.action == ACTION_END_SESSION for d in directives)
        assert all(d.target_persona_id is None for d in directives)
        by_lang = {d.language.code: d for d in directives}
        assert matches_trigger(by_lang["en"].trigger, "Ok bye")
        assert matches_trigger(by_lang["fr"].trigger, "Au revoir")

    def test_prunes_a_language_no_longer_installed(self):
        """Spec: FR-115, FR-705 — a language dropped from the wizard's selection loses
        its end-session trigger too, same posture as ensure_language_directives."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)
        sync.ensure_end_session_directive([Language("en"), Language("it")])

        sync.ensure_end_session_directive([Language("en")])

        directives = directive_repo.list_all()
        assert [d.language.code for d in directives] == ["en"]

    def test_idempotent_across_repeated_startups(self):
        """Spec: FR-115 — called on every server start."""
        directive_repo = FakeDirectiveRepository()
        sync = PersonaDirectiveSync(directive_repo)

        sync.ensure_end_session_directive([Language("en"), Language("fr")])
        first = [(d.id, d.trigger) for d in directive_repo.list_all()]
        sync.ensure_end_session_directive([Language("en"), Language("fr")])

        assert [(d.id, d.trigger) for d in directive_repo.list_all()] == first


class TestMatchesTrigger:
    def test_requires_all_keywords_present(self):
        """Spec: FR-207"""
        trigger = {"keywords": ["switch", "tutor"], "max_words": 6}
        assert matches_trigger(trigger, "Switch to Tutor please")
        assert not matches_trigger(trigger, "Switch please")

    def test_case_insensitive(self):
        """Spec: FR-207"""
        trigger = {"keywords": ["switch", "tutor"], "max_words": 6}
        assert matches_trigger(trigger, "SWITCH TO TUTOR")

    def test_rejects_utterance_longer_than_max_words(self):
        """Spec: FR-207 — a directive phrase is a short command; a long rambling
        sentence that happens to mention both keywords is not a directive."""
        trigger = {"keywords": ["switch", "tutor"], "max_words": 4}
        long_text = "I was just wondering whether we should switch to the tutor at some point"
        assert not matches_trigger(trigger, long_text)

    def test_empty_keywords_never_matches(self):
        """Spec: FR-207 — defensive: a malformed/missing trigger payload must not
        silently match every utterance."""
        assert not matches_trigger({}, "switch to tutor")
        assert not matches_trigger({"keywords": [], "max_words": 6}, "switch to tutor")
