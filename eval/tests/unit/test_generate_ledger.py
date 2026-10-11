# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-3 — a generated Ledger is validated hard and repaired with the exact errors."""
import json

import pytest

from mamemai_eval.domain.model import EvalLanguage, FactKind, SpeechArchetype
from mamemai_eval.usecases.generate_ledger import (
    BRIEFS, SCENARIO_DATES, GenerateProfileAndLedger, LedgerGenerationError, build_prompt, parse_and_validate,
)
from tests.fakes import FakeReferenceLLM

BRIEF = BRIEFS[(EvalLanguage.FR, SpeechArchetype.HESITANT)]


def valid_payload(n=60):
    """60 French facts: 24 events, 22 attributes (5 revisions), 14 knowledge; 10 per scenario."""
    facts = []
    for i in range(n):
        scenario = i // 10
        if i % 5 in (0, 1) and i < 48 or i % 5 == 0:
            kind, happened = "event", SCENARIO_DATES[scenario].replace(day=1).isoformat()
            domain = "voyage"
        elif i % 5 == 4:
            kind, happened, domain = "knowledge", None, "astronomie"
        else:
            kind, happened, domain = "attribute", None, "maison"
        facts.append({
            "fact_id": f"f{i + 1:02d}", "kind": kind, "text": f"Camille a vécu dans la ville numéro {i} avec une amie",
            "revealed_in": scenario, "domain": domain, "happened_on": happened,
            "paraphrases": [f"Camille a habité la ville {i}", f"Camille était dans la ville {i} avec une amie"],
            "key_terms": [f"numéro {i}"],
            "supersedes": None,
        })
    # five revisions: later attribute supersedes an earlier attribute
    attrs = [f for f in facts if f["kind"] == "attribute"]
    pairs = [(a, b) for a in attrs for b in attrs if b["revealed_in"] > a["revealed_in"]]
    used_old, used_new = set(), set()
    for old, new in pairs:
        if old["fact_id"] not in used_old and new["fact_id"] not in used_new and len(used_old) < 5:
            new["supersedes"] = old["fact_id"]
            used_old.add(old["fact_id"]); used_new.add(new["fact_id"])
    return {"profile": {"display_name": "Camille", "biography": "Une retraitée bretonne.",
                        "speech_style": "Elle hésite beaucoup."}, "facts": facts}


def test_valid_answer_is_accepted_with_fixed_scenario_dates():
    profile, ledger = parse_and_validate(json.dumps(valid_payload()), BRIEF)
    assert profile.profile_id == BRIEF.profile_id == ledger.profile_id
    assert profile.archetype is SpeechArchetype.HESITANT and len(ledger.facts) == 60
    assert ledger.scenario_dates == SCENARIO_DATES
    assert sum(1 for f in ledger.facts if f.kind is FactKind.KNOWLEDGE) >= 8


def test_answer_wrapped_in_prose_and_fences_is_still_parsed():
    raw = "Voici:\n```json\n" + json.dumps(valid_payload()) + "\n```"
    assert len(parse_and_validate(raw, BRIEF)[1].facts) == 60


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d["facts"].__delitem__(slice(-10, None)), "facts; need"),
    (lambda d: d["facts"][3].update(domain="astronomie"), "not allowed"),
    (lambda d: d["facts"][0].update(text=d["facts"][0]["text"] + " word" * 60), "words"),
    (lambda d: d["facts"][1].update(paraphrases=["only one"]), "paraphrases"),
    (lambda d: d["facts"][5].update(text=d["facts"][6]["text"], key_terms=["numéro 6"]), "duplicate"),
    (lambda d: d["profile"].update(biography=""), "profile.biography"),
    (lambda d: d["facts"][2].update(key_terms=[]), "key_terms"),
    (lambda d: d["facts"][2].update(key_terms=["absent du texte"]), "key_terms not found"),
    (lambda d: [f.update(supersedes=None) for f in d["facts"]], "revisions"),
    (lambda d: [f.update(text=f"The man has a house and the dog was with his friend {f['key_terms'][0]}") for f in d["facts"]],
     "not written in fr"),
])
def test_rejections_name_the_problem(mutate, expect):
    d = valid_payload()
    mutate(d)
    with pytest.raises(ValueError, match=expect):
        parse_and_validate(json.dumps(d), BRIEF)


def test_repair_round_sends_the_exact_errors_back():
    bad = valid_payload(); del bad["facts"][-10:]
    llm = FakeReferenceLLM([json.dumps(bad), json.dumps(valid_payload())])
    _, ledger = GenerateProfileAndLedger(llm, "m").execute(BRIEF)
    assert len(ledger.facts) == 60 and len(llm.calls) == 2
    assert "REJECTED" in llm.calls[1].prompt and "facts; need" in llm.calls[1].prompt


def test_gives_up_after_max_attempts_instead_of_returning_garbage():
    llm = FakeReferenceLLM(["not json"] * 5)
    with pytest.raises(LedgerGenerationError):
        GenerateProfileAndLedger(llm, "m", max_attempts=2).execute(BRIEF)
    assert len(llm.calls) == 2


def test_the_four_briefs_are_distinct_people_and_prompt_pins_the_dates():
    assert len({b.profile_id for b in BRIEFS.values()}) == len(BRIEFS)
    prompt = build_prompt(BRIEF)
    assert "2026-06-08" in prompt and "French" in prompt and BRIEF.sketch in prompt


def test_english_prompt_uses_english_phrasing_and_the_english_briefs_exist():
    en = BRIEFS[(EvalLanguage.EN, SpeechArchetype.FLUENT)]
    prompt = build_prompt(en)
    assert "written in English" in prompt and "learned that" in prompt and "a appris" not in prompt
    assert "a appris" in build_prompt(BRIEF)
    assert {(b.language, b.archetype) for b in BRIEFS.values()} == {
        (lang, arch) for lang in EvalLanguage for arch in SpeechArchetype}
