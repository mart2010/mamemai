from mamemai_server.infrastructure.recall_gate import MIN_RECALL_SIMILARITY, DefaultRecallGate


class TestDefaultRecallGate:
    """Spec: FR-309, TR-314"""

    def test_statement_without_question_mark_is_skipped_regardless_of_length(self):
        """Recall answers questions ("what did I tell you about X") — a plain
        statement isn't asking to be answered from memory, however long it is."""
        gate = DefaultRecallGate(min_words=3)
        assert gate.should_embed("I just got back from a wonderful trip to Rome") is False

    def test_short_utterance_below_min_words_is_skipped_even_with_question_mark(self):
        gate = DefaultRecallGate(min_words=6)
        assert gate.should_embed("How are you?") is False
        assert gate.should_embed("What's up?") is False

    def test_real_question_at_or_above_min_words_proceeds(self):
        gate = DefaultRecallGate(min_words=6)
        assert gate.should_embed("What did I tell you about my trip to Paris?") is True

    def test_min_words_is_configurable(self):
        gate = DefaultRecallGate(min_words=1)
        assert gate.should_embed("Agua?") is True

    def test_no_previous_search_always_searches(self):
        gate = DefaultRecallGate()
        assert gate.should_search(None) is True

    def test_similarity_below_dedup_threshold_searches(self):
        gate = DefaultRecallGate(dedup_threshold=0.93)
        assert gate.should_search(0.5) is True

    def test_similarity_at_or_above_dedup_threshold_skips_search(self):
        gate = DefaultRecallGate(dedup_threshold=0.93)
        assert gate.should_search(0.93) is False
        assert gate.should_search(0.99) is False

    def test_dedup_threshold_is_configurable(self):
        gate = DefaultRecallGate(dedup_threshold=0.8)
        assert gate.should_search(0.85) is False

    def test_one_word_vocab_style_answer_is_skipped(self):
        """Spec: FR-309 — 2026-07-30: the language tutor no longer overrides this
        (LanguageTutorRecallGate removed). A one-word answer to the tutor's own
        prompt ("agua") doesn't need its own memory search — the item being tested
        is already injected via the tutor's selection strategy (a separate
        mechanism, `select_items`/persona_state), not recall."""
        gate = DefaultRecallGate()
        assert gate.should_embed("agua") is False
        assert gate.should_embed("si") is False


class TestRelevanceFloor:
    """Spec: FR-309, TR-303 — `MemoryRepository.search` is ORDER BY distance LIMIT top_n
    with no floor of its own, so against a non-empty database it always returns rows.
    Without this gate "recall found nothing" is not a representable state and the
    honesty branch that depends on it is dead code — which is how the assistant came to
    open a reply with "Oui, nous en avons déjà parlé" about a subject absent from the
    database, absent from the session, and absent from the session tail (2026-08-03)."""

    def test_a_close_memory_is_relevant(self):
        gate = DefaultRecallGate()
        assert gate.is_relevant(0.90) is True

    def test_a_distant_memory_is_not(self):
        """The measured miss band: under the TR-509 prefixes unrelated subjects score
        0.769-0.834 against this database, high enough to be returned by every search
        and mean nothing. 0.834 is the measured absent ceiling, so it must be excluded —
        the floor sits just above it."""
        gate = DefaultRecallGate()
        assert gate.is_relevant(0.834) is False
        assert gate.is_relevant(0.78) is False

    def test_the_floor_itself_counts_as_relevant(self):
        gate = DefaultRecallGate()
        assert gate.is_relevant(MIN_RECALL_SIMILARITY) is True

    def test_floor_is_configurable_per_persona(self):
        """A persona whose memories are dense and near-synonymous may need a stricter
        bar than the default."""
        gate = DefaultRecallGate(min_similarity=0.95)
        assert gate.is_relevant(0.90) is False
