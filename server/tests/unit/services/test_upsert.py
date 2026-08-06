"""MemoryUpserter — merge/insert returns and the exact-duplicate short-circuit.

The threshold/merge semantics themselves are covered end-to-end through the
ConsolidateMemory tests; here we pin the upserter's own contract: the merged-flag
return (feeds the installer's provenance counts) and the short-circuit that makes
bundle reinstalls near-free.
"""
from uuid import uuid4

from mamemai_server.domain.model import Concept, EngagementLevel, Language, Procedure
from mamemai_server.services.upsert import DEFAULT_MERGE_THRESHOLD, MemoryUpserter

# Derived, never a literal: these tests assert *which band* a similarity falls in,
# not any particular number. Hard-coded 0.95s silently stopped meaning "auto-merge"
# when the threshold moved 0.93 -> 0.97 (2026-08-04) and eight tests failed for a
# reason that had nothing to do with what they were checking.
_MERGE = DEFAULT_MERGE_THRESHOLD + 0.01          # comfortably in the auto-merge band
_MERGE_LOWER = DEFAULT_MERGE_THRESHOLD + 0.005   # also in band, but ranked below _MERGE

from tests.fakes.fakes import (
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakeMemorySynthesizer,
)

PERSONA_ID = uuid4()


class _CannedSearchMemoryRepository(FakeMemoryRepository):
    """Returns a fixed similarity-search result so merge paths can be exercised."""

    def __init__(self, results) -> None:
        super().__init__()
        self._results = results

    def search(self, embedding, memory_types, top_n, persona_id=None):
        return self._results


def _concept(**overrides) -> Concept:
    defaults = dict(
        id=None, persona_id=PERSONA_ID, name="hola",
        description="The standard Spanish greeting.", language=Language("es"),
    )
    defaults.update(overrides)
    return Concept(**defaults)


def _procedure(**overrides) -> Procedure:
    defaults = dict(
        id=None, persona_id=PERSONA_ID, name="greeting politely",
        description="How to greet politely.", language=Language("es"),
        steps=["hola", "¿cómo está?"],
    )
    defaults.update(overrides)
    return Procedure(**defaults)


def _upserter(memory_repo=None, synthesizer=None):
    return MemoryUpserter(
        memory_repo if memory_repo is not None else FakeMemoryRepository(),
        FakeEmbeddingService(),
        FakeDisambiguationEvaluator(),
        synthesizer if synthesizer is not None else FakeMemorySynthesizer(),
    )


class TestMergedFlagReturn:
    def test_insert_returns_false_and_assigns_id(self):
        """Spec: TR-607"""
        concept = _concept()
        merged = _upserter().upsert_concept(concept, PERSONA_ID)
        assert merged is False
        assert concept.id is not None

    def test_merge_returns_true_and_reuses_id(self):
        """Spec: TR-607, TR-603"""
        existing = _concept(id=42, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])  # auto-merge band
        concept = _concept()
        merged = _upserter(memory_repo=repo).upsert_concept(concept, PERSONA_ID)
        assert merged is True
        assert concept.id == 42


class TestExactDuplicateShortCircuit:
    def test_identical_concept_skips_synthesis(self):
        """Spec: TR-604, FR-603 — Reinstalling a bundle re-upserts byte-identical items — no LLM call."""
        existing = _concept(id=42, engagement_level=EngagementLevel.EXPLORED)
        repo = _CannedSearchMemoryRepository([(1.0, existing)])
        synthesizer = FakeMemorySynthesizer()
        concept = _concept(engagement_level=EngagementLevel.UNSEEN)

        merged = _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_concept(concept, PERSONA_ID)

        assert merged is True
        assert synthesizer.concept_calls == []
        assert concept.id == 42
        # Max-engagement rule still applies: the duplicate cannot downgrade knowledge.
        assert concept.engagement_level == EngagementLevel.EXPLORED

    def test_differing_concept_description_still_synthesizes(self):
        """Spec: TR-603, INV-8"""
        existing = _concept(id=42, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])
        synthesizer = FakeMemorySynthesizer()

        _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_concept(_concept(), PERSONA_ID)

        assert len(synthesizer.concept_calls) == 1

    def test_identical_procedure_skips_synthesis(self):
        """Spec: TR-604"""
        existing = _procedure(id=7)
        repo = _CannedSearchMemoryRepository([(1.0, existing)])
        synthesizer = FakeMemorySynthesizer()

        merged = _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_procedure(_procedure(), PERSONA_ID)

        assert merged is True
        assert synthesizer.procedure_calls == []

    def test_procedure_with_differing_steps_still_synthesizes(self):
        """Spec: TR-604, TR-603 — Same name + description but new steps is new evidence, not a duplicate."""
        existing = _procedure(id=7)
        repo = _CannedSearchMemoryRepository([(1.0, existing)])
        synthesizer = FakeMemorySynthesizer()

        _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_procedure(
            _procedure(steps=["hola", "¿cómo está?", "mucho gusto"]), PERSONA_ID
        )

        assert len(synthesizer.procedure_calls) == 1


class TestExcludeIds:
    """Lets the bundle installer keep items it just inserted, earlier in the same
    install run, from being matched by later items in that same run — see
    MemoryUpserter's docstring."""

    def test_excluded_top_candidate_forces_insert(self):
        """Spec: TR-605, FR-604 — Same-run sibling above the disambiguation threshold must not be merged into."""
        sibling = _concept(id=99, name="parlare")
        repo = _CannedSearchMemoryRepository([(0.85, sibling)])  # disambiguate band

        merged = _upserter(memory_repo=repo).upsert_concept(
            _concept(name="mangiare"), PERSONA_ID, exclude_ids=frozenset({99}),
        )

        assert merged is False

    def test_excluded_candidate_does_not_hide_a_real_match_behind_it(self):
        """Spec: TR-605 — A genuine pre-existing duplicate further down the candidate list still wins."""
        sibling = _concept(id=99, name="parlare")
        real_match = _concept(id=7, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(_MERGE, sibling), (_MERGE_LOWER, real_match)])  # both auto-merge band

        merged = _upserter(memory_repo=repo).upsert_concept(
            _concept(), PERSONA_ID, exclude_ids=frozenset({99}),
        )

        assert merged is True

    def test_excluded_top_candidate_forces_insert_for_procedure(self):
        """Spec: TR-605, FR-604"""
        sibling = _procedure(id=99, name="vorrei + [nome/infinito]")
        repo = _CannedSearchMemoryRepository([(0.85, sibling)])

        merged = _upserter(memory_repo=repo).upsert_procedure(
            _procedure(name="per me, + [nome]"), PERSONA_ID, exclude_ids=frozenset({99}),
        )

        assert merged is False

    def test_empty_exclude_ids_is_a_no_op(self):
        """Spec: TR-605 — Live consolidation never passes exclude_ids — default behavior is unchanged."""
        existing = _concept(id=42, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])

        merged = _upserter(memory_repo=repo).upsert_concept(_concept(), PERSONA_ID)

        assert merged is True


class TestUpdateDescription:
    """A persona with a registered assessment strategy may only recognize a touch on
    match, never edit curated wording — see MemoryUpserter's docstring."""

    def test_match_keeps_existing_description_verbatim(self):
        """Spec: TR-606, FR-407"""
        existing = _concept(id=42, description="Curated bundle wording.")
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])
        synthesizer = FakeMemorySynthesizer()

        concept = _concept(description="Narrative-flavored extraction text.")
        merged = _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_concept(
            concept, PERSONA_ID, update_description=False,
        )

        assert merged is True
        assert concept.description == "Curated bundle wording."
        assert synthesizer.concept_calls == []

    def test_match_still_bumps_engagement_and_fills_category_gap(self):
        """Spec: TR-606, TR-603, FR-305"""
        existing = _concept(id=42, description="Curated bundle wording.", category="verb")
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])

        concept = _concept(engagement_level=EngagementLevel.INTEGRATED, category=None)
        _upserter(memory_repo=repo).upsert_concept(concept, PERSONA_ID, update_description=False)

        assert concept.engagement_level == EngagementLevel.INTEGRATED
        assert concept.category == "verb"

    def test_match_keeps_existing_procedure_description_and_steps_verbatim(self):
        """Spec: TR-606"""
        existing = _procedure(id=7, description="Curated steps.", steps=["a", "b"])
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])
        synthesizer = FakeMemorySynthesizer()

        procedure = _procedure(description="Invented from a roleplay.", steps=["x", "y", "z"])
        merged = _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_procedure(
            procedure, PERSONA_ID, update_description=False,
        )

        assert merged is True
        assert procedure.description == "Curated steps."
        assert procedure.steps == ["a", "b"]
        assert synthesizer.procedure_calls == []

    def test_default_update_description_true_is_a_no_op(self):
        """Spec: TR-606 — Bundle installer and GA-style consolidation never pass this — unchanged."""
        existing = _concept(id=42, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(_MERGE, existing)])
        synthesizer = FakeMemorySynthesizer()

        _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_concept(_concept(), PERSONA_ID)

        assert len(synthesizer.concept_calls) == 1


class TestOrganicEngagementLadder:
    """Spec: FR-310 — the significance axis, added 2026-08-02 alongside dropping the
    provenance floor to 1.

    Engagement rises one step per *distinct conversation* in which the user literally
    names an organic concept. Consolidation runs one conversation per transaction, so
    each qualifying upsert is by construction a separate conversation. Before this, the
    ladder was inert: organic concepts entered at MENTIONED and _max_engagement never
    moved them, so EXPLORED/INTEGRATED were unreachable and MENTIONED meant both "said
    once" and "said every time".
    """

    def _existing(self, level: EngagementLevel, origin: str = "organic") -> Concept:
        return _concept(id=7, description="A greeting.", engagement_level=level, origin=origin)

    def _upsert_with_mention(self, existing: Concept, candidate_origin: str = "organic") -> Concept:
        repo = _CannedSearchMemoryRepository([(0.99, existing)])
        candidate = _concept(origin=candidate_origin)
        _upserter(memory_repo=repo).upsert_concept(
            candidate, PERSONA_ID, user_turns=["I said hola to the neighbour this morning."],
        )
        return candidate

    def test_recurrence_promotes_one_step(self):
        """Spec: FR-310 — a later conversation naming it again earns exactly one step."""
        result = self._upsert_with_mention(self._existing(EngagementLevel.MENTIONED))
        assert result.engagement_level is EngagementLevel.EXPLORED

    def test_promotion_is_one_step_at_a_time(self):
        """Spec: FR-310 — no jumping the ladder; EXPLORED goes to INTEGRATED, not past."""
        result = self._upsert_with_mention(self._existing(EngagementLevel.EXPLORED))
        assert result.engagement_level is EngagementLevel.INTEGRATED

    def test_promotion_saturates_at_integrated(self):
        """Spec: FR-310 — INTEGRATED is the top; further recurrence must not overflow."""
        result = self._upsert_with_mention(self._existing(EngagementLevel.INTEGRATED))
        assert result.engagement_level is EngagementLevel.INTEGRATED

    def test_no_promotion_without_a_user_mention(self):
        """Spec: FR-310 — provenance still governs. A conversation that merges into this
        concept without the user ever naming it is not engagement, so no step."""
        repo = _CannedSearchMemoryRepository([(0.99, self._existing(EngagementLevel.MENTIONED))])
        candidate = _concept()
        _upserter(memory_repo=repo).upsert_concept(
            candidate, PERSONA_ID, user_turns=["We talked about something else entirely today."],
        )
        assert candidate.engagement_level is EngagementLevel.MENTIONED

    def test_bundle_install_and_enrichment_never_promote(self):
        """Spec: FR-310, INV-12 — user_turns=None (bundle install, persona enrichment)
        means there is no conversation to be evidence of anything. Content sources never
        claim knowledge on the user's behalf."""
        repo = _CannedSearchMemoryRepository([(0.99, self._existing(EngagementLevel.MENTIONED))])
        candidate = _concept()
        _upserter(memory_repo=repo).upsert_concept(candidate, PERSONA_ID)
        assert candidate.engagement_level is EngagementLevel.MENTIONED

    def test_authored_content_never_climbs_the_ladder(self):
        """Spec: FR-310, FR-506, INV-6 — the load-bearing exclusion. A persona whose
        activity elicits its own material (it prompts for an item, the user repeats it
        back) would otherwise manufacture the very engagement signal FR-506 ranks on and
        walk its own authored content to INTEGRATED. Authored items keep the single
        UNSEEN -> MENTIONED bump via _max_engagement and nothing more; mastery of authored
        content lives in the owning persona's persona_state. Branches on `origin`, a
        generic first-class field — never on persona, so INV-6 is untouched."""
        authored = self._existing(EngagementLevel.MENTIONED, origin="authored")
        result = self._upsert_with_mention(authored)
        assert result.origin == "authored"
        assert result.engagement_level is EngagementLevel.MENTIONED

    def test_authored_still_takes_the_first_bump(self):
        """Spec: INV-12 — authored content enters UNSEEN and must still register that the
        user has now actually met it; only the climb beyond MENTIONED is withheld."""
        authored = self._existing(EngagementLevel.UNSEEN, origin="authored")
        result = self._upsert_with_mention(authored)
        assert result.engagement_level is EngagementLevel.MENTIONED
