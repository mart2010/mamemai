from datetime import datetime, UTC

from mamemai_server.domain.model import Conversation, GENERAL_ASSISTANT_ID, Language
from mamemai_server.infrastructure.llm._common import _extraction_system_prompt, _parse_extraction


def _conversation() -> Conversation:
    return Conversation(id=1, started_at=datetime.now(UTC), persona_id=GENERAL_ASSISTANT_ID)


class TestExtractionSystemPrompt:
    def test_episode_summaries_forced_to_primary_language(self):
        """Spec: INV-10, TR-706"""
        prompt = _extraction_system_prompt(_conversation(), Language("fr"))
        assert "Write every episode summary in the language with IETF code 'fr'" in prompt
        assert "regardless of the language the conversation was held in" in prompt

    def test_no_language_rule_when_primary_language_unknown(self):
        """Spec: TR-706"""
        prompt = _extraction_system_prompt(_conversation(), None)
        assert "Write every episode summary" not in prompt

    def test_asks_for_category_on_concepts_only(self):
        """Spec: TR-706, FR-307 — procedures are never requested from conversation."""
        prompt = _extraction_system_prompt(_conversation(), Language("en"))
        assert prompt.count('"category"') == 1
        assert '"procedures"' not in prompt

    def test_procedures_never_requested_even_with_episodes_enabled(self):
        """Spec: FR-307"""
        prompt = _extraction_system_prompt(_conversation(), Language("en"), extract_episodes=True)
        assert "procedures" not in prompt

    def test_episode_prompt_excludes_meta_and_requires_time_grounding(self):
        """Spec: FR-307 — the 2026-07-18 review found a debugging session about the
        assistant's own TTS bug fabricated into a personal-event episode; the prompt
        must explicitly exclude conversation-about-itself content and require a real
        time/place, not just ask for a summary."""
        prompt = _extraction_system_prompt(_conversation(), Language("en"))
        assert "NOT this conversation itself" in prompt
        assert "assistant's own operation" in prompt
        assert "identifiable time or place" in prompt


class TestParseExtractionCategory:
    def test_category_parsed_when_present(self):
        """Spec: TR-706"""
        data = {
            "concepts": [{"name": "comer", "description": "To eat.", "language": "es", "category": "verb"}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].category == "verb"

    def test_category_defaults_to_none(self):
        """Spec: TR-706"""
        data = {
            "concepts": [{"name": "comer", "description": "To eat.", "language": "es"}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].category is None

    def test_concepts_are_organic(self):
        """Spec: FR-307 — live-conversation extraction always produces organic concepts."""
        data = {"concepts": [{"name": "comer", "description": "To eat.", "language": "es"}]}
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts[0].origin == "organic"

    def test_procedures_in_payload_are_ignored(self):
        """Spec: FR-307 — even if a model disobeys the prompt and emits a "procedures"
        array, ExtractionResult has no such field — parsing must not error on it."""
        data = {
            "concepts": [],
            "procedures": [{"name": "p", "description": "d.", "language": "en", "steps": []}],
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.concepts == []


class TestParseExtractionEpisodeTimeGrounding:
    def test_episode_without_happened_at_is_dropped(self):
        """Spec: FR-307 — no genuine time grounding means it's not a real episode,
        rather than silently backdating it to the conversation's own timestamp."""
        data = {"episodes": [{"summary": "Debugging the TTS voice download.", "happened_at": None}]}
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.episodes == []

    def test_episode_with_unparseable_happened_at_is_dropped(self):
        """Spec: FR-307"""
        data = {"episodes": [{"summary": "Something happened.", "happened_at": "not-a-date"}]}
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert result.episodes == []

    def test_episode_with_valid_happened_at_is_kept(self):
        """Spec: FR-307"""
        data = {
            "episodes": [
                {"summary": "Went hiking in the Alps.", "happened_at": "2026-06-01T00:00:00+00:00"}
            ]
        }
        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))
        assert len(result.episodes) == 1
        assert result.episodes[0].summary == "Went hiking in the Alps."


class TestParseExtractionMalformedItems:
    """Spec: FR-307 — well-formed JSON is not well-formed content. ~2% of measured
    extraction runs returned a syntactically perfect object with a null `name`, and one
    such item used to raise out of `merge_extractions` and discard every window's results
    for the conversation. A bad item must cost only itself."""

    def test_concept_with_null_name_is_dropped_and_its_neighbours_kept(self):
        """Spec: FR-307 — the measured crash, and the property that matters: the good
        concept beside it in the same window survives."""
        data = {
            "concepts": [
                {"name": None, "description": "Something.", "language": "en"},
                {"name": "Aurorae", "description": "Polar lights.", "language": "en"},
            ]
        }

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert [c.name for c in result.concepts] == ["Aurorae"]

    def test_concept_with_missing_or_blank_name_is_dropped(self):
        """Spec: FR-307 — a key the model omitted and one it filled with whitespace are
        the same answer as null: no name, no concept."""
        data = {
            "concepts": [
                {"description": "No name at all.", "language": "en"},
                {"name": "   ", "description": "Blank name.", "language": "en"},
            ]
        }

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert result.concepts == []

    def test_concept_with_no_description_is_dropped(self):
        """Spec: FR-307 — a named concept carrying nothing to remember is not worth a row;
        every consumer downstream reads the description."""
        data = {"concepts": [{"name": "Aurorae", "description": None, "language": "en"}]}

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert result.concepts == []

    def test_non_string_fields_are_treated_as_absent(self):
        """Spec: FR-307 — a model that answers with a number or a nested object where a
        string was asked for must not reach the domain objects (or `.strip()`)."""
        data = {
            "concepts": [
                {"name": 42, "description": "A number for a name.", "language": "en"},
                {"name": "Halos", "description": {"text": "nested"}, "language": "en"},
            ]
        }

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert result.concepts == []

    def test_non_string_language_falls_back_to_the_conversation_language(self):
        """Spec: FR-307, INV-10 — an unusable language field is a reason to fall back, not
        to lose the concept."""
        data = {"concepts": [{"name": "Halos", "description": "Ice rings.", "language": 7}]}

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("fr"))

        assert [c.language.code for c in result.concepts] == ["fr"]

    def test_episode_with_null_summary_is_dropped(self):
        """Spec: FR-307 — `episode.summary.strip()` in the merge has the same exposure the
        null name had."""
        data = {
            "episodes": [
                {"summary": None, "happened_at": "2026-06-01T00:00:00+00:00"},
                {"summary": "Went hiking in the Alps.", "happened_at": "2026-06-01T00:00:00+00:00"},
            ]
        }

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert [e.summary for e in result.episodes] == ["Went hiking in the Alps."]

    def test_a_null_array_is_not_an_iteration_error(self):
        """Spec: FR-307 — `"concepts": null` is not an empty array to `dict.get`, and the
        exception it raised cost the whole conversation, same as the null name did."""
        data = {"episodes": None, "concepts": None}

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert result.concepts == [] and result.episodes == []

    def test_a_bare_string_where_an_object_was_asked_for_is_skipped(self):
        """Spec: FR-307 — the model occasionally answers with a list of names instead of a
        list of objects; that is one unusable item, not a lost conversation."""
        data = {"concepts": ["Aurorae", {"name": "Halos", "description": "Ice rings.", "language": "en"}]}

        result = _parse_extraction(data, _conversation(), GENERAL_ASSISTANT_ID, Language("en"))

        assert [c.name for c in result.concepts] == ["Halos"]
