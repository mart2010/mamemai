# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import re
from collections.abc import Sequence
from enum import Enum
from uuid import UUID

from ..domain.model import Concept, EngagementLevel, Episode, MemoryType, Procedure
from .ports import (
    DisambiguationEvaluator,
    EmbeddingService,
    MemoryItem,
    MemoryRepository,
    MemorySynthesizer,
)


def _max_engagement(a: EngagementLevel, b: EngagementLevel) -> EngagementLevel:
    return a if a >= b else b


def _promote_engagement(level: EngagementLevel) -> EngagementLevel:
    """One step up the ladder, saturating at INTEGRATED.

    The significance axis, deliberately separate from the provenance gate above. A
    concept the user names again in a *later* conversation has earned a step; repetition
    inside a single sitting has not — that is conversational noise, and it was what the
    old engagement floor of 2 was mistakenly counting.

    Consolidation processes exactly one conversation per transaction, so every touch that
    reaches here is by construction a distinct conversation. That is what makes this
    deterministic and language-agnostic: recurrence is established by embedding match at
    upsert, never by matching strings across sessions, so no stemming is needed.

    Before this existed the ladder was inert: organic concepts entered at MENTIONED and
    _max_engagement(MENTIONED, MENTIONED) left them there forever, so MENTIONED meant
    both "said once" and "said in every conversation for a year", and EXPLORED /
    INTEGRATED were unreachable for anything organic.
    """
    return EngagementLevel(min(int(level) + 1, int(EngagementLevel.INTEGRATED)))


_PAREN_RE = re.compile(r"\(([^)]+)\)")


def _mention_terms(name: str) -> list[str]:
    """Literal terms to search a user turn for: the concept's full name, plus — for a
    name with a parenthetical abbreviation, e.g. "Explainable AI (XAI)" — the
    abbreviation itself and the name with the parenthetical stripped. Either form
    counts as the user naming the concept."""
    terms = [name.strip()]
    paren = _PAREN_RE.search(name)
    if paren:
        terms.append(paren.group(1).strip())
        terms.append(_PAREN_RE.sub("", name).strip())
    return [t for t in terms if t]


def _mentioned_in(text: str, name: str) -> bool:
    """Whole-word, case-insensitive: a bare substring match would let a short name
    like "AI" match inside unrelated words ("against", "explain")."""
    return any(re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) for term in _mention_terms(name))


# Real values are read from [memory] in mamemai.toml (see infrastructure/config.py, which
# re-exports these as its own defaults so the two can never disagree) and passed in by
# callers; these are the fallbacks when a caller passes nothing.
#
# 0.97, not the 0.93 that shipped until 2026-08-04. Above this bar two records are merged
# with no LLM check and the loser's text is gone, so it has to clear anything that must
# stay separate — and measured under the TR-509 prefixes, every one of the highest-scoring
# stored pairs is a must-NOT-merge, nine of them above 0.93: 'oggi'/'adesso' at 0.9529,
# 'tre'/'due' at 0.9247, 'male'/'bene' (opposites) at 0.9189. Short vocabulary items in one
# language score 0.92-0.96 on register alone, so no bar below ~0.96 is safe. Everything
# below 0.97 now falls to the disambiguator, which reads names and descriptions instead of
# a distance — more LLM calls, in exchange for an unrecoverable operation firing only on
# near-identical text.
DEFAULT_MERGE_THRESHOLD = 0.97
# Unchanged, and deliberately: the prefixes moved the *top* of the scale up but left the
# unrelated-pair median slightly lower (0.771 -> 0.762), so this band is if anything
# safer than before. There are no true duplicates in the database to derive a better
# value from, so it stays where it was rather than being moved on no evidence.
DEFAULT_DISAMBIGUATE_THRESHOLD = 0.75

# Calibration placeholders (FR-307, FR-407) — how close a live-extraction concept
# candidate must be to an *authored* one (bundle install, persona enrichment) before
# it's treated as a touch on that curated item rather than a free-standing organic
# concept. Deliberately its own constant rather than reusing DEFAULT_DISAMBIGUATE_THRESHOLD:
# that threshold answers "is this plausibly the same entity" (LLM-arbitrated); this one
# answers "is this too close to curated content to let it become a separate organic
# item" (no arbitration — err on the side of protecting authored content), so it starts
# at the same value but is free to be tuned independently.
DEFAULT_AUTHORED_PROTECTION_THRESHOLD = 0.75

# A brand-new organic concept (no existing match, authored or organic) needs the user
# to have literally named it (see _mentioned_in) in at least this many of their own
# turns before it's worth inserting. This gate answers ONE question — provenance: did
# the *user* introduce this, or was it only ever in the assistant's mouth? That question
# is binary, which is why the floor is 1.
#
# It was 2 until 2026-08-02, as a proxy for significance as well as provenance. Two
# reasons that was wrong. First, it double-counted: triviality is already filtered twice
# before this point — the extraction floor drops thin conversations outright (FR-307) and
# worthiness explicitly rejects "small talk, greetings, trivial exchanges". Second, and
# worse, counting *turns within one conversation* is a poor significance signal in its
# own right (something important can be said once, something trivial five times) and it
# produced silent false negatives: whole-word literal matching cannot see inflection, so
# a user saying "hiking" then "hiked" registered as engaging once and the concept was
# discarded. Found on a real database 2026-08-02; it had been failing two integration
# tests since 2026-07-30, misdiagnosed then as a _mentioned_in bug.
#
# What the floor of 2 was really reaching for now lives on its own axis — see
# _promote_engagement: significance is recurrence across *distinct conversations*, not
# repetition within one.
#
# Precision is unaffected by the change. Embedding-similarity-to-turn-text was tried
# first (2026-07-20) and live-tested the same day: it couldn't tell "broadly the same
# topic" from "specifically about this sibling concept" — a live conversation about
# AI/XAI/NLP/Transfer Learning (all introduced together in one GA monologue) let two
# AI-flavored user turns satisfy the engagement bar for every one of those siblings. The
# fix was literal matching, not the count: at a floor of 1 that incident still resolves
# correctly, because the user never literally named AI, NLP or Transfer Learning — only
# XAI, the one they actually followed up on.
DEFAULT_MIN_CONCEPT_ENGAGEMENT_TURNS = 1

# Fetch a few nearest neighbors rather than just the top-1 so that, when a caller
# excludes some candidate ids (see exclude_ids below), a legitimate pre-existing match
# can still surface instead of being hidden behind an excluded one. No behavior change
# when exclude_ids is empty: candidates stay sorted by similarity, so the first entry is
# unchanged from a plain top_n=1 search.
_CANDIDATE_TOP_N = 5


def embeddable_text(name: str, description: str) -> str:
    """The exact string a named memory (concept or procedure) is embedded from.

    Public and shared rather than inlined at the six call sites below, because
    `scripts/reembed_memories.py` has to reproduce it byte for byte: a backfill that
    embeds different text than the live path writes leaves every stored vector subtly
    wrong, and nothing would fail loudly — recall would just quietly get worse. Sharing
    the function makes that class of drift unrepresentable instead of merely tested for.
    Episodes have no name and are embedded from their bare summary.
    """
    return f"{name}: {description}"


class _MergeAction(Enum):
    AUTO_MERGE = "auto_merge"
    DISAMBIGUATE = "disambiguate"
    AUTO_INSERT = "auto_insert"


def _merge_action(similarity: float, merge_threshold: float, disambiguate_threshold: float) -> _MergeAction:
    if similarity >= merge_threshold:
        return _MergeAction.AUTO_MERGE
    if similarity >= disambiguate_threshold:
        return _MergeAction.DISAMBIGUATE
    return _MergeAction.AUTO_INSERT


def _existing_to_merge(
    candidates: list[tuple[float, MemoryItem]],
    candidate: MemoryItem,
    disambiguator: DisambiguationEvaluator,
    merge_threshold: float,
    disambiguate_threshold: float,
) -> MemoryItem | None:
    """Returns the existing record to merge into, or None to insert as new."""
    if not candidates:
        return None
    similarity, best = candidates[0]
    action = _merge_action(similarity, merge_threshold, disambiguate_threshold)
    if action == _MergeAction.AUTO_INSERT:
        return None
    if action == _MergeAction.AUTO_MERGE:
        return best
    return best if disambiguator.is_same(best, candidate) else None


class MemoryUpserter:
    """Shared merge-or-insert pipeline: embed → similarity search → two-tier threshold
    (auto-merge / LLM disambiguation / auto-insert) → LLM synthesis on merge → repository
    upsert. Used by both offline consolidation and the bundle installer — there is
    deliberately no separate insertion path, which is what makes bundle installs
    deduplicate against existing memory and reinstalls idempotent.

    `exclude_ids` (concept/procedure upserts only) lets a caller processing a *batch* of
    related items — currently only the bundle installer — keep items freshly inserted
    earlier in that same batch from being matched by later items in the same batch.
    Bundle authors write short, structurally similar but deliberately distinct items
    (e.g. "parlare"/"mangiare", both one-line "regular -are verb" definitions); without
    this, such siblings can cross the disambiguation threshold against each other and
    get blended, even though the author never intended them to be the same concept.
    It does NOT affect matching against genuinely pre-existing content (an earlier
    install, live-conversation extraction, or an earlier bundle) — that's the real,
    intended use of this pipeline's fuzzy matching. Live consolidation never passes
    this (default empty), so its behavior is unchanged.

    `update_description=False` (procedure upserts from the bundle installer and persona
    enrichment; concept upserts never need to pass this explicitly — see below) means
    this caller may only *recognize* a touch against an existing match, never *edit* it:
    a match never runs the synthesizer or touches description/steps/embedding, even
    when the new text differs — a single install/proposal must never drift curated
    wording. Procedures only ever come from authoring (bundles) or persona enrichment,
    never live conversation (FR-307), so this is their only protection: there is no
    live-extraction source that could otherwise drift one.

    Concepts get a more precise version of the same protection, driven by
    `Concept.origin` ("authored" — bundle install, persona enrichment — vs "organic" —
    live-conversation extraction) rather than by which caller is asking:
    `upsert_concept` first checks the candidate against *authored* matches only; a
    close-enough hit (DEFAULT_AUTHORED_PROTECTION_THRESHOLD) is treated as a touch on
    that curated item, verbatim, regardless of `update_description` — an organic
    extraction pass can never rewrite curated vocabulary, but genuinely distinct organic
    content (e.g. the user going off-curriculum mid-lesson) is free to become its own
    item. Only past that check does the normal two-tier merge-or-insert run, scoped to
    *organic* candidates. A brand-new *organic* insert (no match at all) additionally
    needs `user_turns` (raw user-turn texts from the source conversation) to literally
    name the concept (`_mentioned_in` — whole-word, case-insensitive, matching either
    the full name or a parenthetical abbreviation like "XAI" out of "Explainable AI
    (XAI)") in at least DEFAULT_MIN_CONCEPT_ENGAGEMENT_TURNS of them — a topic only the
    assistant ever mentioned, or that the user only ever gestured at as part of a
    broader topic, must not become a permanent concept. This intersects what the
    conversation as a whole introduced with what the user's own words actually named,
    rather than a looser "topically similar" union: embedding similarity between a
    candidate's own description and raw user-turn text was tried first and live-tested
    2026-07-20 — it couldn't distinguish "broadly the same topic" from "specifically
    about this sibling concept" when several related concepts came from the same
    assistant monologue (AI/XAI/NLP/Transfer Learning all introduced together; two
    AI-flavored user turns satisfied the bar for all four, not just XAI, the one
    actually followed up on). `user_turns=None` (the default; bundle install and
    persona enrichment never pass it) skips this check entirely, since only live
    conversation extraction has turns to evaluate.

    That gate is about **provenance** only — who introduced this — and is therefore
    binary (floor 1). **Significance** is a separate axis on a different timescale: an
    *organic* concept the user names again in a later conversation is promoted one
    engagement step (`_promote_engagement`), saturating at INTEGRATED. Recurrence across
    conversations is real evidence; repetition inside one sitting is not.
    Authored concepts are excluded from that ladder by design: they still take the
    single UNSEEN → MENTIONED bump via `_max_engagement`, but nothing beyond it, because
    a persona whose activity elicits its own material (it prompts for an item, the user
    repeats it back) would otherwise manufacture the very signal FR-506 ranks on.
    Mastery of authored content belongs to the owning persona's `persona_state`, not to
    generic code.

    Each upsert_* method mutates the passed item in place (description/summary,
    embedding, engagement, category, id) and returns True when it merged into an
    existing item, False when it inserted a new one (or when a candidate concept fails
    its engagement check and nothing was written — check `item.id is not None` to tell
    those two apart).
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        embedding_service: EmbeddingService,
        disambiguator: DisambiguationEvaluator,
        synthesizer: MemorySynthesizer,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        disambiguate_threshold: float = DEFAULT_DISAMBIGUATE_THRESHOLD,
        authored_protection_threshold: float = DEFAULT_AUTHORED_PROTECTION_THRESHOLD,
        min_concept_engagement_turns: int = DEFAULT_MIN_CONCEPT_ENGAGEMENT_TURNS,
    ) -> None:
        self._memory_repo = memory_repo
        self._embedding_service = embedding_service
        self._disambiguator = disambiguator
        self._synthesizer = synthesizer
        self._merge_threshold = merge_threshold
        self._disambiguate_threshold = disambiguate_threshold
        self._authored_protection_threshold = authored_protection_threshold
        self._min_concept_engagement_turns = min_concept_engagement_turns

    def upsert_episode(self, episode: Episode) -> bool:
        episode.embedding = self._embedding_service.embed_passage(episode.summary)
        candidates = self._memory_repo.search(episode.embedding, (MemoryType.EPISODE,), top_n=1)
        existing = self._find_existing(candidates, episode)
        if existing is not None:
            episode.summary = self._synthesizer.synthesize_episode(existing.summary, episode.summary)
            episode.embedding = self._embedding_service.embed_passage(episode.summary)
            episode.id = existing.id
        episode.id = self._memory_repo.upsert_episode(episode)
        return existing is not None

    def upsert_concept(
        self,
        concept: Concept,
        persona_id: UUID,
        exclude_ids: frozenset[int] = frozenset(),
        update_description: bool = True,
        user_turns: Sequence[str] | None = None,
    ) -> bool:
        concept.embedding = self._embedding_service.embed_passage(embeddable_text(concept.name, concept.description))
        candidates = self._memory_repo.search(
            concept.embedding, (MemoryType.CONCEPT,), top_n=_CANDIDATE_TOP_N, persona_id=persona_id,
        )
        candidates = [c for c in candidates if c[1].id not in exclude_ids]

        authored_candidates = [c for c in candidates if c[1].origin == "authored"]
        if authored_candidates and authored_candidates[0][0] >= self._authored_protection_threshold:
            # Close enough to curated content to be a touch on it, never a rewrite —
            # regardless of update_description or where this candidate came from.
            existing = authored_candidates[0][1]
            concept.description = existing.description
            concept.embedding = existing.embedding
            concept.engagement_level = _max_engagement(existing.engagement_level, concept.engagement_level)
            concept.category = existing.category or concept.category
            concept.origin = existing.origin
            concept.id = existing.id
            concept.id = self._memory_repo.upsert_concept(concept)
            return True

        organic_candidates = [c for c in candidates if c[1].origin != "authored"]
        existing = self._find_existing(organic_candidates, concept)
        if existing is not None:
            if update_description:
                # Exact-duplicate short-circuit: same name + description needs no LLM
                # synthesis or re-embedding — this makes bundle reinstalls near-free.
                if not (existing.name == concept.name and existing.description == concept.description):
                    concept.description = self._synthesizer.synthesize_concept(existing, concept.description)
                    concept.embedding = self._embedding_service.embed_passage(embeddable_text(concept.name, concept.description))
            else:
                # This caller may only recognize a touch, never edit curated content
                # (e.g. persona enrichment) — keep description/embedding verbatim
                # regardless of what the new text said.
                concept.description = existing.description
                concept.embedding = existing.embedding
            # Significance ladder, ORGANIC ONLY (see _promote_engagement). The user
            # naming this again in a later conversation is real recurrence, so it earns a
            # step. Authored content is deliberately excluded even here: a persona whose
            # activity *elicits* its own material (it prompts for an item, the user
            # repeats it back) would otherwise manufacture the very signal FR-506 ranks
            # on, climbing its own authored content to INTEGRATED through a feedback
            # loop. Mastery of authored content belongs to the owning persona's
            # persona_state, written by its own assessment strategy; generic code stops
            # trying to measure it. This
            # branches on `origin`, a first-class generic field — never on persona, so
            # INV-6 is untouched.
            # user_turns is None for bundle install and enrichment, so neither can
            # promote: only live conversation is evidence of engagement.
            if (
                user_turns is not None
                and existing.origin == "organic"
                and self._has_engagement(concept, user_turns)
            ):
                concept.engagement_level = _promote_engagement(existing.engagement_level)
            else:
                concept.engagement_level = _max_engagement(existing.engagement_level, concept.engagement_level)
            # An existing category (curated bundle content or an earlier
            # extraction) wins; the new extraction only fills a gap.
            concept.category = existing.category or concept.category
            concept.id = existing.id
        elif user_turns is not None and concept.origin == "organic" and not self._has_engagement(concept, user_turns):
            # A brand-new organic concept (nothing to merge into, authored or organic)
            # needs real user engagement, not just an assistant mention — see class
            # docstring. Leave concept.id as None: the sentinel for "discarded, never
            # written" callers must check before using this item further.
            return False
        concept.id = self._memory_repo.upsert_concept(concept)
        return existing is not None

    def _has_engagement(self, concept: Concept, user_turns: Sequence[str]) -> bool:
        qualifying = sum(1 for text in user_turns if _mentioned_in(text, concept.name))
        return qualifying >= self._min_concept_engagement_turns

    def upsert_procedure(
        self,
        procedure: Procedure,
        persona_id: UUID,
        exclude_ids: frozenset[int] = frozenset(),
        update_description: bool = True,
    ) -> bool:
        procedure.embedding = self._embedding_service.embed_passage(embeddable_text(procedure.name, procedure.description))
        candidates = self._memory_repo.search(
            procedure.embedding, (MemoryType.PROCEDURE,), top_n=_CANDIDATE_TOP_N, persona_id=persona_id,
        )
        candidates = [c for c in candidates if c[1].id not in exclude_ids]
        existing = self._find_existing(candidates, procedure)
        if existing is not None:
            if update_description:
                # Same exact-duplicate short-circuit as upsert_concept (steps included:
                # differing steps are new evidence and must go through synthesis).
                if not (
                    existing.name == procedure.name
                    and existing.description == procedure.description
                    and existing.steps == procedure.steps
                ):
                    procedure.description, procedure.steps = self._synthesizer.synthesize_procedure(
                        existing, procedure.description, procedure.steps
                    )
                    procedure.embedding = self._embedding_service.embed_passage(embeddable_text(procedure.name, procedure.description))
            else:
                procedure.description = existing.description
                procedure.steps = existing.steps
                procedure.embedding = existing.embedding
            procedure.engagement_level = _max_engagement(existing.engagement_level, procedure.engagement_level)
            procedure.category = existing.category or procedure.category
            procedure.id = existing.id
        procedure.id = self._memory_repo.upsert_procedure(procedure)
        return existing is not None

    def _find_existing(self, candidates: list[tuple[float, MemoryItem]], candidate: MemoryItem) -> MemoryItem | None:
        return _existing_to_merge(
            candidates, candidate, self._disambiguator,
            self._merge_threshold, self._disambiguate_threshold,
        )
