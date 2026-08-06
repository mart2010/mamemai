# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: TR-961, FR-307, INV-10 — the three filters that stop episode fabrication.

Each replaces a plea in the extraction prompt with something the prompt cannot enforce:

  user_turns_only                 the fabricated summaries are summaries of *assistant*
                                  content, so on a user-only transcript they are
                                  unconstructible rather than discouraged.
  drop_chat_dated_episodes        the prompt already demands a time "distinct from
                                  during this chat"; a model that files the conversation
                                  as a life event dates it with the conversation's own
                                  timestamp.
  drop_ungrounded_episode_entries the prompt now asks for a verbatim "evidence" quote;
                                  an invented episode's quote is either missing or itself
                                  invented, and an invented quote does not literally
                                  occur in the transcript it claims to come from.

Measured on the real database: 8 of 9 stored episodes fall inside their own conversation's
span, and the ninth misses by 0.02s only because its timestamp was truncated to seconds.
`drop_ungrounded_episode_entries` was added after a live miss neither of the first two
caught (2026-08-06): a session where the user asked nothing but general astronomy
questions produced a stored episode claiming they had personally seen the northern
lights — invented purely from the topic word "aurores boréales" in their own turns, with
no assistant content involved and no in-chat timestamp to flag it.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mamemai_server.domain.model import Conversation, Episode, Speaker, Turn
from mamemai_server.infrastructure.llm._common import (
    _extraction_system_prompt,
    drop_chat_dated_episodes,
    drop_ungrounded_episode_entries,
    user_turns_only,
)

START = datetime(2026, 8, 3, 15, 0, 0, tzinfo=UTC)
END = START + timedelta(minutes=37)


def _conversation(ended: bool = True) -> Conversation:
    return Conversation(
        id=7,
        started_at=START,
        ended_at=END if ended else None,
        persona_id=uuid4(),
        turns=[
            Turn(timestamp=START, speaker=Speaker.USER, content="user one"),
            Turn(timestamp=START, speaker=Speaker.ASSISTANT, content="assistant one"),
            Turn(timestamp=START, speaker=Speaker.USER, content="user two"),
            Turn(timestamp=START, speaker=Speaker.ASSISTANT, content="assistant two"),
        ],
    )


def _episode(when: datetime | None, summary: str = "something happened") -> Episode:
    return Episode(id=None, summary=summary, happened_at=when, origin_conversation_id=7)


class TestUserTurnsOnly:
    def test_assistant_turns_are_removed(self):
        trimmed = user_turns_only(_conversation())

        assert [t.content for t in trimmed.turns] == ["user one", "user two"]

    def test_everything_but_the_turns_is_preserved(self):
        """The extractor resolves relative dates and persona scope from the conversation,
        so a trimmed copy that lost its timestamps or persona would corrupt both."""
        conv = _conversation()

        trimmed = user_turns_only(conv)

        assert (trimmed.id, trimmed.started_at, trimmed.ended_at, trimmed.persona_id) == (
            conv.id, conv.started_at, conv.ended_at, conv.persona_id
        )

    def test_the_original_is_not_mutated(self):
        """Concepts are extracted from the full conversation in the same call, so trimming
        in place would silently strip the assistant's turns from the concept pass too —
        exactly where the correct terminology lives."""
        conv = _conversation()

        user_turns_only(conv)

        assert len(conv.turns) == 4


class TestDropChatDatedEpisodes:
    def test_an_episode_dated_inside_the_conversation_is_dropped(self):
        conv = _conversation()

        kept = drop_chat_dated_episodes([_episode(START + timedelta(minutes=5))], conv)

        assert kept == []

    def test_the_observed_truncated_timestamp_case_is_dropped(self):
        """The real one that motivated the tolerance: a stored episode dated 15:43:20
        against a conversation starting 15:43:20.021288 — the same instant with the
        microseconds cut off, so a bare `>= started_at` test would keep it."""
        conv = Conversation(id=7, started_at=START.replace(microsecond=21288),
                            ended_at=END, persona_id=uuid4(), turns=[])

        kept = drop_chat_dated_episodes([_episode(START)], conv)

        assert kept == []

    def test_a_genuinely_past_event_survives(self):
        """The failure direction that matters: this filter must not eat real life story."""
        conv = _conversation()

        kept = drop_chat_dated_episodes([_episode(START - timedelta(days=3))], conv)

        assert len(kept) == 1

    def test_an_episode_with_no_date_survives(self):
        """`happened_at` is nullable "only if genuinely unknown"; undated is not evidence
        of being about the chat, and dropping it here would be a different policy than the
        one being enforced."""
        conv = _conversation()

        kept = drop_chat_dated_episodes([_episode(None)], conv)

        assert len(kept) == 1

    def test_an_unended_conversation_still_filters_on_its_start(self):
        """Consolidation normally runs on ended conversations, but a crash-recovery replay
        can present one without an end — falling back to open-ended would either drop
        everything or nothing depending on which way it was written."""
        conv = _conversation(ended=False)

        during = drop_chat_dated_episodes([_episode(START)], conv)
        before = drop_chat_dated_episodes([_episode(START - timedelta(days=1))], conv)

        assert during == []
        assert len(before) == 1


class TestDropUngroundedEpisodeEntries:
    # A real transcript shape: the user asking general questions, never claiming a
    # personal event — the exact conversation shape that produced the live miss.
    TRANSCRIPT = (
        "User: Alors, j'aimerais qu'on discute de l'astronomie aujourd'hui. Est-ce que "
        "tu connais bien l'astronomie ?\n"
        "User: qu'est-ce que tu connais des aurores boréales et des halos ?"
    )

    def test_an_entry_with_no_evidence_is_dropped(self):
        entries = [{"summary": "something", "happened_at": "2026-08-05T00:00:00"}]

        kept = drop_ungrounded_episode_entries(entries, self.TRANSCRIPT)

        assert kept == []

    def test_a_fabricated_quote_is_dropped(self):
        """The real miss that motivated this filter: the topic word "aurores boréales"
        in the user's own turns was enough for the model to invent a first-person claim
        never made. The fabricated "evidence" naturally doesn't occur in the transcript
        either, since neither is real."""
        entries = [{
            "summary": "L'utilisateur a vu des aurores boréales dans le Nord de la France",
            "happened_at": "2026-08-05T00:00:00",
            "evidence": "j'ai vu des aurores boréales dans le Nord de la France",
        }]

        kept = drop_ungrounded_episode_entries(entries, self.TRANSCRIPT)

        assert kept == []

    def test_a_genuine_verbatim_quote_survives(self):
        entries = [{
            "summary": "L'utilisateur s'intéresse à l'astronomie",
            "happened_at": None,
            "evidence": "j'aimerais qu'on discute de l'astronomie aujourd'hui",
        }]

        kept = drop_ungrounded_episode_entries(entries, self.TRANSCRIPT)

        assert kept == entries

    def test_grounding_ignores_case_accents_and_punctuation(self):
        """STT casing/accent/punctuation noise between the quote and its source must not
        sink a genuine quote — only word content and order matter."""
        entries = [{
            "summary": "...",
            "happened_at": None,
            "evidence": "J'AIMERAIS QU'ON DISCUTE DE L'ASTRONOMIE AUJOURD'HUI",
        }]

        kept = drop_ungrounded_episode_entries(entries, self.TRANSCRIPT)

        assert kept == entries

    def test_a_reordered_quote_is_not_grounded(self):
        """Grounding requires an actual contiguous quote, not just the same words
        present somewhere in the transcript — word-salad from real vocabulary is exactly
        what an invented-but-plausible episode would produce."""
        entries = [{
            "summary": "...",
            "happened_at": None,
            "evidence": "aujourd'hui l'astronomie discute on qu'",
        }]

        kept = drop_ungrounded_episode_entries(entries, self.TRANSCRIPT)

        assert kept == []

    def test_a_non_string_evidence_is_dropped(self):
        entries = [{"summary": "something", "happened_at": None, "evidence": 42}]

        kept = drop_ungrounded_episode_entries(entries, self.TRANSCRIPT)

        assert kept == []


class TestPromptSections:
    def test_episode_only_prompt_asks_for_no_concepts(self):
        """The episode pass runs on a user-only transcript, where asking for concepts too
        would harvest them from the half of the conversation with the STT errors in it."""
        prompt = _extraction_system_prompt(
            _conversation(), None, extract_episodes=True, extract_concepts=False
        )

        assert '"episodes"' in prompt
        assert '"concepts"' not in prompt

    def test_concept_only_prompt_asks_for_no_episodes(self):
        prompt = _extraction_system_prompt(
            _conversation(), None, extract_episodes=False, extract_concepts=True
        )

        assert '"concepts"' in prompt
        assert '"episodes"' not in prompt
