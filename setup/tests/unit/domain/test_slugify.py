from mamemai_setup.domain.slugify import DEFAULT_SCHEMA, schema_slug


def test_lowercases_and_joins_words_with_underscore():
    assert schema_slug("Mary Ann") == "mary_ann"


def test_strips_accents_via_ascii_fallback():
    assert schema_slug("José") == "jose"


def test_falls_back_to_default_when_input_is_empty():
    assert schema_slug("") == DEFAULT_SCHEMA


def test_falls_back_to_default_when_nothing_ascii_survives():
    assert schema_slug("日本語") == DEFAULT_SCHEMA


def test_falls_back_to_default_when_only_digits():
    assert schema_slug("123") == DEFAULT_SCHEMA


def test_strips_a_leading_digit_rather_than_producing_an_invalid_identifier():
    assert schema_slug("1alice") == "alice"


def test_collapses_punctuation_runs_to_a_single_underscore():
    assert schema_slug("mary---ann!!") == "mary_ann"


def test_already_valid_slug_passes_through_unchanged():
    assert schema_slug("alice") == "alice"
