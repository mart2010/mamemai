# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""InstallPersonaBundle — one-shot use case behind `mamemai-bundle install <path>`.

Runs as its own process (needs the embedding model, DB, and mamemai.toml); the live
session loop never calls or knows about it. Documented caveat: run while the server
is idle — a concurrent consolidation run could race the same persona's upserts
(single-user reality makes this documentation, not locking).
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID, uuid4

from ..domain.model import (
    AssistantPersona,
    Concept,
    DEFAULT_VOICE_ROLE,
    EngagementLevel,
    Language,
    MemoryType,
    Procedure,
    resolve_installed_languages,
)
from .directives import PersonaDirectiveSync
from .ports import (
    BundleInstallLog,
    BundleInstallRecord,
    BundleItemSpec,
    BundlePersonaDefinition,
    PersonaBundle,
    PersonaBundleSource,
    PersonaRepository,
    UnitOfWork,
    UserRepository,
)
from .upsert import MemoryUpserter


class BundleInstallError(Exception):
    """Install cannot proceed (persona unresolvable, onboarding incomplete). Distinct
    from BundleFormatError: the bundle itself is well-formed."""


@dataclass(frozen=True)
class BundleInstallResult:
    persona_id: UUID
    persona_created: bool
    items_inserted: int
    items_merged: int
    notices: tuple[str, ...]


class InstallPersonaBundle:
    def __init__(
        self,
        bundle_source: PersonaBundleSource,
        persona_repo: PersonaRepository,
        user_repo: UserRepository,
        upserter: MemoryUpserter,
        unit_of_work: UnitOfWork,
        install_log: BundleInstallLog,
        directive_sync: PersonaDirectiveSync,
        # Language -> Kokoro voice, same derivation as onboarding (the composition root
        # wires KOKORO_DEFAULT_VOICES); used only when [persona.voices] omits "default".
        default_voice_for: Callable[[Language], str],
        # The installed languages (FR-705): a bundle whose target language has no TTS
        # voice on this machine must fail at install, not at lesson time (FR-609).
        # None (older callers, tests) → every supported language.
        installed_languages: list[Language] | None = None,
    ) -> None:
        self._bundle_source = bundle_source
        self._persona_repo = persona_repo
        self._user_repo = user_repo
        self._upserter = upserter
        self._unit_of_work = unit_of_work
        self._install_log = install_log
        self._directive_sync = directive_sync
        self._default_voice_for = default_voice_for
        self._installed_languages = (
            installed_languages if installed_languages is not None else resolve_installed_languages(())
        )

    def execute(self, path: Path) -> BundleInstallResult:
        bundle = self._bundle_source.load(path)  # raises BundleFormatError on malformation
        notices: list[str] = []

        persona = self._persona_repo.get_by_key(bundle.persona_key)
        persona_created = False
        if persona is None:
            if bundle.persona is None:
                raise BundleInstallError(
                    f"persona '{bundle.persona_key}' does not exist and the bundle carries "
                    "no [persona] definition — install its base bundle first"
                )
            persona = self._create_persona(bundle)
            persona_created = True
        elif bundle.persona is not None:
            self._update_persona(persona, bundle.persona)
            notices.append(
                f"persona '{bundle.persona_key}' already exists — updated its authored "
                "definition (prompt, settings, languages, episode declaration); its name, "
                "voices, speaking rate and response language were kept as you have them"
            )

        # Insertion order is the contract: lessons in filename-sort order (guaranteed by
        # PersonaBundleSource), items in file order. Curriculum order survives as
        # ascending SERIAL id. One transaction per lesson (mirrors per-conversation
        # consolidation atomicity); a failed run is recovered by re-running the installer
        # — already-committed items merge into themselves via the exact-duplicate
        # short-circuit.
        #
        # new_concept_ids/new_procedure_ids track items freshly inserted earlier in THIS
        # run (across all lessons) and are passed as exclude_ids to the upserter: a
        # bundle's own items must never merge into each other (the author already meant
        # them as distinct), only into content that predates this run (an earlier
        # install, an earlier bundle, or live-conversation extraction).
        inserted = merged = 0
        new_concept_ids: set[int] = set()
        new_procedure_ids: set[int] = set()
        for lesson in bundle.lessons:
            with self._unit_of_work:
                for item in lesson.items:
                    if self._upsert_item(item, persona.id, new_concept_ids, new_procedure_ids):
                        merged += 1
                    else:
                        inserted += 1

        self._install_log.append(
            BundleInstallRecord(
                persona_key=bundle.persona_key,
                bundle_name=bundle.name,
                bundle_version=bundle.version,
                bundle_author=bundle.author,
                installed_at=datetime.now(UTC),
                items_inserted=inserted,
                items_merged=merged,
                manifest=bundle.manifest,
            )
        )
        return BundleInstallResult(
            persona_id=persona.id,
            persona_created=persona_created,
            items_inserted=inserted,
            items_merged=merged,
            notices=tuple(notices),
        )

    def _upsert_item(
        self,
        item: BundleItemSpec,
        persona_id: UUID,
        new_concept_ids: set[int],
        new_procedure_ids: set[int],
    ) -> bool:
        # Always UNSEEN: a bundle cannot claim the user knows things. On merge with an
        # already-engaged item the upserter's max-engagement rule keeps the higher level.
        if item.memory_type is MemoryType.CONCEPT:
            concept = Concept(
                id=None,
                persona_id=persona_id,
                name=item.name,
                description=item.description,
                language=item.language,
                category=item.category,
                engagement_level=EngagementLevel.UNSEEN,
                origin="authored",
            )
            merged = self._upserter.upsert_concept(concept, persona_id, exclude_ids=frozenset(new_concept_ids))
            if not merged:
                new_concept_ids.add(concept.id)
            return merged

        procedure = Procedure(
            id=None,
            persona_id=persona_id,
            name=item.name,
            description=item.description,
            language=item.language,
            steps=list(item.steps),
            category=item.category,
            engagement_level=EngagementLevel.UNSEEN,
        )
        # Procedures are always authored (FR-307) — a reinstall recognizes an existing
        # one as a touch, never rewrites its content (update_description=False).
        merged = self._upserter.upsert_procedure(
            procedure, persona_id, exclude_ids=frozenset(new_procedure_ids), update_description=False,
        )
        if not merged:
            new_procedure_ids.add(procedure.id)
        return merged

    def _update_persona(self, persona: AssistantPersona, definition: BundlePersonaDefinition) -> None:
        """Reinstall onto an existing persona: the bundle refreshes what only its author
        can know; everything the user can change stays theirs.

        The split is by *who can know the value*, not by convenience. The author owns
        `system_prompt` (authored expertise — an updated prompt is the main thing an
        upgrade delivers), `settings`, `extracts_episodes` (FR-407) and the target
        language list. The user owns `name` (it is how they address the persona, and
        the Directive triggers are built from it), `voices`, `speaking_rate` and
        `response_language` — all four are voice-editable, and a content update silently
        undoing a spoken preference would be the worst kind of surprise.

        Same shape as two rules already in this codebase: `upsert_concept` keeps curated
        wording immutable while the user's engagement moves freely, and `persona_state`
        is written only by its owner. Each side owns its own fields.

        `strategy` and `persona_key` are untouched — identity set at creation, and
        `PersonaRepository.save()` does not carry them on update either. A bundle
        therefore cannot rebind its persona to different code, deliberately.

        Unconditional, not version-gated: installing a bundle applies that bundle's
        definition. Comparing versions would need a semver ordering the format does not
        specify, and "the bundle I installed is the one in effect" is easier to predict.
        """
        user = self._user_repo.get()
        languages = list(definition.languages)
        if languages and user is not None and user.primary_language is not None \
                and user.primary_language not in languages:
            languages.append(user.primary_language)

        persona.system_prompt = definition.system_prompt
        persona.settings = definition.settings
        persona.extracts_episodes = definition.extracts_episodes
        persona.reads_across_personas = definition.reads_across_personas
        persona.sees_turn_language = definition.sees_turn_language
        persona.languages = languages
        persona.updated_at = datetime.now(UTC)
        self._persona_repo.save(persona)

    def _create_persona(self, bundle: PersonaBundle) -> AssistantPersona:
        definition = bundle.persona
        user = self._user_repo.get()
        if user is None or user.primary_language is None:
            raise BundleInstallError(
                "cannot create the persona: no user with a primary language exists yet — "
                "complete onboarding (first conversation) before installing bundles"
            )

        # A persona's target languages must be installed languages (FR-609): the
        # session language pair is only speakable when the target's TTS voices were
        # actually pulled at setup — fail here with a pointer at the wizard, not at
        # lesson time with a missing-voice synthesis error. The primary language is
        # not checked: onboarding selected it from the installed set by construction.
        missing = [lang.code for lang in definition.languages if lang not in self._installed_languages]
        if missing:
            raise BundleInstallError(
                f"cannot create the persona: bundle language(s) {', '.join(missing)} are not "
                "installed on this system — re-run mamemai-setup to add them, then install again"
            )

        # Pair-independence: the bundle never embeds user-language values, so the
        # anchor ("default" role) is derived at install when omitted.
        voices = dict(definition.voices)
        if DEFAULT_VOICE_ROLE not in voices:
            voices[DEFAULT_VOICE_ROLE] = self._default_voice_for(user.primary_language)

        # languages = bundle's target list + User.primary_language (input languages the
        # persona accepts — the user must always be able to address the persona in their
        # own language). Bundle order kept; primary appended only when not already listed.
        # Bundle targets + the user's primary language, so they can always address the
        # persona in their own language (TR-904). An *empty* target list is left empty:
        # it already means "no restriction", and appending would narrow it to exactly one
        # language — the opposite of what the persona asked for.
        languages = list(definition.languages)
        if languages and user.primary_language not in languages:
            languages.append(user.primary_language)

        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(),
            name=definition.name,
            system_prompt=definition.system_prompt,
            languages=languages,
            response_language=definition.response_language or user.primary_language,
            voices=voices,
            is_system=False,
            created_at=now,
            updated_at=now,
            persona_key=bundle.persona_key,
            settings=definition.settings,  # copied verbatim — opaque to generic code
            strategy=definition.strategy,  # resolved against the registry at server startup
            extracts_episodes=definition.extracts_episodes,  # FR-407, declared not inferred
            reads_across_personas=definition.reads_across_personas,  # FR-303, read scope
            sees_turn_language=definition.sees_turn_language,  # FR-114, language tagging
        )
        self._persona_repo.save(persona)
        # A Directive (FR-207) is how the user actually reaches this persona going
        # forward — create it in the same use case that creates the persona itself.
        self._directive_sync.sync_created(persona)
        return persona
