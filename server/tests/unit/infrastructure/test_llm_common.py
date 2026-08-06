# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Shared judgment plumbing for both LLM providers: how a one-word YES/NO answer is read.

The parse used to be `"yes" in reply.lower()`. A French transcript drew a bare "OUI" from
llama3.1:8b, which that read as NO — silently, and indistinguishably from a real verdict.
Every case here is a reply actually worth surviving, not an invented edge case.
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mamemai_server.domain.model import Concept, Conversation, Episode, Language, Speaker, Turn
from mamemai_server.services.ports import ExtractionResult
from mamemai_server.infrastructure.llm._common import (
    EXTRACTION_WINDOW_TURNS,
    WORTHINESS_SYSTEM_PROMPT,
    extraction_transcripts,
    merge_extractions,
    WORTHINESS_WINDOW_TURNS,
    is_affirmative,
    worthiness_transcripts,
)


class TestIsAffirmative:
    @pytest.mark.parametrize("reply", ["YES", "yes", " YES\n", "**YES**", "Yes."])
    def test_english_yes(self, reply):
        """Spec: FR-307 — the answer the prompt asks for, however it is decorated."""
        assert is_affirmative(reply) is True

    @pytest.mark.parametrize("reply", ["NO", "no", " NO\n", "**NO**", "No."])
    def test_english_no(self, reply):
        """Spec: FR-307"""
        assert is_affirmative(reply) is False

    @pytest.mark.parametrize("reply", ["OUI", "Oui.", "はい", "SÍ", "sim"])
    def test_non_english_yes(self, reply):
        """Spec: FR-307 — observed live: reading a French conversation, this model
        answers OUI despite being asked for English. Reading that as NO discarded the
        conversation with no trace."""
        assert is_affirmative(reply) is True

    @pytest.mark.parametrize("reply", ["NON", "Non.", "いいえ", "não"])
    def test_non_english_no(self, reply):
        """Spec: FR-307"""
        assert is_affirmative(reply) is False

    def test_verdict_is_the_first_word_not_any_word(self):
        """Spec: FR-307 — a model that explains itself must be read by its verdict. The
        old substring parse called this YES."""
        assert is_affirmative("No - this is only small talk, yes it is trivial") is False

    def test_substrings_are_not_words(self):
        """Spec: FR-307 — "si" is an affirmative in Spanish and a substring of ordinary
        English words; matching on substrings would call this YES."""
        assert is_affirmative("Consider it stored") is False

    @pytest.mark.parametrize("reply", ["", "   ", None, "¯\\_(ツ)_/¯"])
    def test_unreadable_answer_is_not_a_yes(self, reply):
        """Spec: FR-307 — a gate that cannot read the answer must not invent a yes;
        the conservative direction is the one that does not fabricate memory."""
        assert is_affirmative(reply) is False


class TestWorthinessTranscripts:
    def _conversation(self, n_turns: int) -> Conversation:
        now = datetime.now(UTC)
        return Conversation(
            id=1, started_at=now, persona_id=uuid4(),
            turns=[
                Turn(timestamp=now, speaker=Speaker.USER if i % 2 == 0 else Speaker.ASSISTANT,
                     content=f"turn {i}")
                for i in range(n_turns)
            ],
        )

    def test_short_conversation_is_one_window(self):
        """Spec: FR-307 — unchanged behaviour and exactly one LLM call for the common case."""
        transcripts = worthiness_transcripts(self._conversation(10))
        assert len(transcripts) == 1
        assert "turn 0" in transcripts[0] and "turn 9" in transcripts[0]

    def test_long_conversation_is_judged_at_both_ends(self):
        """Spec: FR-307 — measured on llama3.1:8b: a 72-turn transcript is judged
        unworthy 0/5 while its own first 24 turns are worthy 5/5 and its last 24 are 4/5.
        The judgment degrades with length, not with content — even the 18 turns of pure
        STT-repair dialogue score 5/5 — so a long conversation is sampled at both ends
        instead of being handed over whole."""
        transcripts = worthiness_transcripts(self._conversation(72))
        assert len(transcripts) == 2
        assert "turn 0" in transcripts[0]
        assert "turn 71" in transcripts[1]
        assert "turn 71" not in transcripts[0]

    def test_window_boundary(self):
        """Spec: FR-307 — at exactly the window size it is still a single call."""
        assert len(worthiness_transcripts(self._conversation(WORTHINESS_WINDOW_TURNS))) == 1
        assert len(worthiness_transcripts(self._conversation(WORTHINESS_WINDOW_TURNS + 1))) == 2

    def test_speakers_are_labelled(self):
        """Spec: FR-307 — the judge needs to see who said what; a transcript of
        unattributed lines is a different question."""
        transcript = worthiness_transcripts(self._conversation(2))[0]
        assert "User: turn 0" in transcript
        assert "Assistant: turn 1" in transcript


class TestExtractionWindows:
    def _conversation(self, n_turns: int) -> Conversation:
        now = datetime.now(UTC)
        return Conversation(
            id=1, started_at=now, persona_id=uuid4(),
            turns=[
                Turn(timestamp=now, speaker=Speaker.USER if i % 2 == 0 else Speaker.ASSISTANT,
                     content=f"turn {i}")
                for i in range(n_turns)
            ],
        )

    def test_short_conversation_is_one_call(self):
        """Spec: FR-307 — the common case must not get more expensive."""
        assert len(extraction_transcripts(self._conversation(20))) == 1

    def test_long_conversation_is_split_and_fully_covered(self):
        """Spec: FR-307 — unlike worthiness, which samples both ends, extraction must
        cover every turn: a subject discussed only in the middle is exactly what was being
        lost. Sized off the constant rather than a literal, because the constant is a
        measured tuning value (re-measured 2026-08-03, 24 → 16) while full coverage is the
        actual contract."""
        n_turns = EXTRACTION_WINDOW_TURNS * 3
        transcripts = extraction_transcripts(self._conversation(n_turns))

        assert len(transcripts) == 3
        joined = "\n".join(transcripts) + "\n"
        for i in range(n_turns):
            assert f"turn {i}\n" in joined

    def test_trailing_remnant_is_folded_into_the_previous_window(self):
        """Spec: FR-307 — a two-turn remnant is not a conversation, and asking about it
        spends a call to be told so, so it rides along with its predecessor."""
        n_turns = EXTRACTION_WINDOW_TURNS * 2 + 2
        transcripts = extraction_transcripts(self._conversation(n_turns))

        assert len(transcripts) == 2
        assert f"turn {n_turns - 1}" in transcripts[-1]
        # Coverage, not just shape: the first version of the fold overwrote the wrong
        # window and dropped the whole first window while still producing two transcripts.
        joined = "\n".join(transcripts) + "\n"
        for i in range(n_turns):
            assert f"turn {i}\n" in joined


class TestMergeExtractions:
    def _concept(self, name: str, description: str = "d") -> Concept:
        return Concept(id=None, persona_id=uuid4(), name=name, description=description,
                       language=Language("en"), origin="organic")

    def _episode(self, summary: str, happened_at: datetime) -> Episode:
        return Episode(id=None, summary=summary, happened_at=happened_at, origin_conversation_id=1)

    def test_same_concept_from_two_windows_is_kept_once(self):
        """Spec: FR-310 — required for correctness, not tidiness. Upserted twice, the
        second call would match the row the first just wrote and promote its engagement
        level, manufacturing the "repetition inside one sitting" FR-310 rules out as
        evidence. Windowing must not reintroduce it by the back door."""
        merged = merge_extractions([
            ExtractionResult(episodes=[], concepts=[self._concept("Aurorae")]),
            ExtractionResult(episodes=[], concepts=[self._concept("aurorae ")]),
        ])

        assert [c.name for c in merged.concepts] == ["Aurorae"]

    def test_distinct_concepts_from_different_windows_all_survive(self):
        """Spec: FR-307 — the point of windowing: the middle of a conversation reaches
        memory instead of being dropped with everything past turn 24."""
        merged = merge_extractions([
            ExtractionResult(episodes=[], concepts=[self._concept("Aurorae")]),
            ExtractionResult(episodes=[], concepts=[self._concept("Noctilucent clouds")]),
        ])

        assert {c.name for c in merged.concepts} == {"Aurorae", "Noctilucent clouds"}

    def test_identical_episodes_are_deduplicated(self):
        """Spec: FR-307"""
        when = datetime.now(UTC)
        merged = merge_extractions([
            ExtractionResult(episodes=[self._episode("Hiked in the Alps.", when)], concepts=[]),
            ExtractionResult(episodes=[self._episode("Hiked in the Alps.", when)], concepts=[]),
        ])

        assert len(merged.episodes) == 1

    def test_differently_worded_episodes_are_left_to_the_upserter(self):
        """Spec: FR-307 — the honest limit of a string comparison. Two windows describing
        one event in different words are `upsert_episode`'s job, whose embedding-similarity
        merge exists for exactly this; guessing here would be worse than passing it on."""
        when = datetime.now(UTC)
        merged = merge_extractions([
            ExtractionResult(episodes=[self._episode("Hiked in the Alps.", when)], concepts=[]),
            ExtractionResult(episodes=[self._episode("Went hiking in the Alps.", when)], concepts=[]),
        ])

        assert len(merged.episodes) == 2

    def test_no_results_merges_to_empty(self):
        """Spec: FR-307 — every window unparseable is still a valid, empty answer."""
        merged = merge_extractions([])

        assert merged.episodes == [] and merged.concepts == []


class TestWorthinessPrompt:
    def test_states_positive_criteria_as_their_own_list(self):
        """Spec: FR-307 — the 2026-08-02 rebalance. The previous wording gave YES one
        clause and NO five, and the model duly said NO to almost everything: 14-15/24 on
        a labelled probe set, every negative right and the positives wrong. This asserts
        the shape the fix depends on, since the wording itself is only verifiable by the
        manual quality gate."""
        assert "Answer YES if" in WORTHINESS_SYSTEM_PROMPT
        assert "Answer NO only if it is nothing but" in WORTHINESS_SYSTEM_PROMPT

    def test_asks_for_the_answer_in_english(self):
        """Spec: FR-307 — load-bearing for `is_affirmative`, not decoration: without it
        this model answers a French transcript in French."""
        assert "in English" in WORTHINESS_SYSTEM_PROMPT

    def test_still_excludes_conversation_about_the_assistant(self):
        """Spec: FR-307 — the 2026-07-18 finding that motivated the original prompt: a
        debugging session judged worth storing became a fabricated personal-event
        episode. The rebalance must not have dropped that exclusion."""
        assert "debugging" in WORTHINESS_SYSTEM_PROMPT
        assert "capabilities" in WORTHINESS_SYSTEM_PROMPT
