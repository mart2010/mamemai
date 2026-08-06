from mamemai_server.services.session import _spell_out_numbers


def test_french_space_grouped_thousands():
    """Spec: FR-106, TR-308 — a thousands separator must not split one number into two.

    Found live 2026-08-03: the pattern was a bare `\\d+`, so "12 000" matched as "12" and
    "000" and Kokoro spoke "douze zéro" — `num2words(0)` is "zéro", which is what turned
    every round thousand into nonsense. All three cases below were observed in one reply.
    """
    assert _spell_out_numbers("7 840 objets", "fr") == "sept mille huit cent quarante objets"
    assert _spell_out_numbers("12 000 objets", "fr") == "douze mille objets"
    assert _spell_out_numbers("93 000 objets", "fr") == "quatre-vingt-treize mille objets"


def test_non_breaking_group_separators():
    """Spec: FR-106, TR-308 — a model writing French thousands may use NBSP or the narrow
    NBSP rather than a plain space; all three are grouping, none is ever a decimal."""
    assert _spell_out_numbers("12 000 objets", "fr") == "douze mille objets"
    assert _spell_out_numbers("12 000 objets", "fr") == "douze mille objets"


def test_english_comma_grouped_thousands():
    """Spec: FR-106, TR-308 — `,` groups in English, where it previously read as a decimal
    point: "7,840" became 7.84 and was spoken "seven point eight four"."""
    assert _spell_out_numbers("12,000 objects", "en") == "twelve thousand objects"
    assert _spell_out_numbers("7,840 objects", "en").startswith("seven thousand")


def test_dot_groups_thousands_in_es_it_pt():
    """Spec: FR-106, TR-308 — Spanish/Italian/Portuguese group with `.` and take `,` as the
    decimal, the opposite of English. Same digits, different number."""
    assert _spell_out_numbers("12.000 objetos", "es") == "doce mil objetos"
    assert _spell_out_numbers("93.000 oggetti", "it") == "novantatremila oggetti"
    assert _spell_out_numbers("12.000 objetos", "pt") == "doze mil objetos"


def test_decimals_survive_in_both_conventions():
    """Spec: FR-106, TR-308 — grouping requires *exactly* three digits after the separator,
    which is the only thing distinguishing it from a decimal. A decimal must still be read
    as one, in whichever convention the language uses."""
    assert _spell_out_numbers("7,84 mètres", "fr") == "sept virgule huit quatre mètres"
    assert _spell_out_numbers("3.14 metres", "en") == "three point one four metres"


def test_ungrouped_numbers_are_unchanged():
    """Spec: FR-106, TR-308 — years and bare counts carry no separator and were always
    correct; the 2026-08-03 fix must not disturb them (this is why the bug survived so
    long — every date in every session read fine)."""
    assert _spell_out_numbers("en 1888", "fr") == "en mille huit cent quatre-vingt-huit"
    assert _spell_out_numbers("il y a 5 étoiles", "fr") == "il y a cinq étoiles"


def test_unsupported_language_left_alone():
    """Spec: FR-106 — outside en/fr/es/it/pt the digits are left to the TTS engine."""
    assert _spell_out_numbers("12 000 個", "ja") == "12 000 個"
    assert _spell_out_numbers("12 000", None) == "12 000"
