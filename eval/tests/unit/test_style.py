# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-9 — style measurement and the two Speech Archetypes' Style Targets."""
import pytest

from mamemai_eval.domain.model import EvalLanguage, SpeechArchetype
from mamemai_eval.domain.style import STYLE_TARGETS, compute_style

FR, EN = EvalLanguage.FR, EvalLanguage.EN

HESITANT_FR = [
    "Euh… bonjour, je voulais savoir… enfin, c'est un peu bête comme question",
    "oui",
    "Je je crois que j'ai oublié le nom de cette étoile, la grosse, là, dans le ciel d'été",
    "c'est quoi déjà, euh, la différence entre météo et climat ?",
    "Ah d'accord…",
    "Du coup il fait froid là-bas en ce moment ?",
    "hmm, ben je sais pas trop",
    "Et euh, pourquoi la Lune, elle change de forme comme ça pendant le mois ?",
    "Non non, c'est bon, merci",
    "Donc… j'ai lu quelque part que… que Mars avait deux lunes mais je suis pas sûr de moi, tu peux me dire",
    "Merci.",
    "D'accord, je note.",
]
FLUENT_FR = [
    "Bonjour, j'aimerais comprendre comment se forment les aurores boréales, parce que j'en ai vu une la semaine "
    "dernière et je n'ai pas bien saisi le rôle du vent solaire dans ce phénomène.",
    "Merci, c'est très clair. Est-ce que cela explique aussi pourquoi elles sont plus fréquentes près des pôles ?",
    "Très bien. Demain je pars en randonnée dans les Alpes, et je voudrais savoir quel temps il fera en altitude.",
    "D'accord, je prendrai donc des vêtements chauds et une veste imperméable pour être tranquille.",
    "Peux-tu me rappeler quelle est la capitale de l'Australie ? Je confonds toujours avec Sydney.",
    "Parfait, je m'en souviendrai. Pour finir, je cherche un bon livre sur l'histoire de l'astronomie, de préférence "
    "quelque chose d'accessible qui explique comment les astronomes de l'Antiquité ont mesuré la taille de la Terre.",
]


def test_measurement_on_a_small_known_sample():
    p = compute_style(["Oui", "Euh je je pense que c'est bien...", "Quelle heure est-il ?", "ok merci"], FR)
    assert (p.n_turns, p.fragment_rate, p.question_rate, p.ellipsis_rate) == (4, 0.75, 0.25, 0.25)
    assert p.repeated_word_rate == 0.25 and p.lowercase_start_rate == 0.25 and p.filler_per_100_words > 0


def test_no_turns_is_an_error():
    with pytest.raises(ValueError):
        compute_style(["  "], EN)


def test_archetypes_are_clearly_distinct_and_both_defined():
    assert set(STYLE_TARGETS) == set(SpeechArchetype)
    h, f = STYLE_TARGETS[SpeechArchetype.HESITANT].ranges, STYLE_TARGETS[SpeechArchetype.FLUENT].ranges
    assert h.keys() == f.keys()
    for metric in ("filler_per_100_words", "ellipsis_rate", "repeated_word_rate", "fragment_rate"):
        assert h[metric][0] > f[metric][1], metric  # ranges do not even overlap on the discriminating metrics
    assert h["words_p50"][1] <= f["words_p50"][0]


def test_hand_written_samples_conform_to_their_own_archetype_only():
    hesitant, fluent = STYLE_TARGETS[SpeechArchetype.HESITANT], STYLE_TARGETS[SpeechArchetype.FLUENT]
    h, f = compute_style(HESITANT_FR, FR), compute_style(FLUENT_FR, FR)
    assert hesitant.violations(h) == {}, hesitant.violations(h)
    assert fluent.violations(f) == {}, fluent.violations(f)
    assert len(fluent.violations(h)) >= 5 and len(hesitant.violations(f)) >= 5  # each rejects the other


def test_prompt_block_carries_description_and_numbers():
    block = STYLE_TARGETS[SpeechArchetype.HESITANT].to_prompt_block()
    assert "hesitant" in block.lower() and "filler_per_100_words 4–9" in block


HESITANT_EN = [
    "Um… hi, I wanted to ask… well, it's kind of a silly question",
    "yes",
    "I I think I forgot the name of that star, the bright one, in the summer sky",
    "what's the difference again, uh, between weather and climate?",
    "Oh okay…",
    "So is it cold over there right now?",
    "hmm, I don't know really",
    "And um, why does the Moon change shape like that during the month?",
    "No no, it's fine, thanks",
    "Right so… I read somewhere that… that Mars has two moons but I'm not sure, can you tell me",
    "Thanks.",
    "Okay, noted.",
]
FLUENT_EN = [
    "Hello, I would like to understand how the northern lights form, because I saw one last week and I did not "
    "really grasp the role of the solar wind in the phenomenon.",
    "Thank you, that is very clear. Does that also explain why they are more frequent near the poles?",
    "Well, tomorrow I am going hiking in the Alps, so I would like to know what the weather will be like at altitude.",
    "Alright, so I will bring warm clothes and a waterproof jacket just to be safe.",
    "Can you remind me what the capital of Australia is? I always confuse it with Sydney.",
    "Perfect, I will remember that. Finally, I am looking for a good book on the history of astronomy, preferably "
    "something accessible that explains how ancient astronomers measured the size of the Earth.",
]


def test_english_samples_conform_too_and_ambiguous_discourse_words_are_not_fillers():
    h, f = compute_style(HESITANT_EN, EN), compute_style(FLUENT_EN, EN)
    assert STYLE_TARGETS[SpeechArchetype.HESITANT].violations(h) == {}
    assert STYLE_TARGETS[SpeechArchetype.FLUENT].violations(f) == {}  # "Well,"/"so" do not count as fillers
    assert f.filler_per_100_words == 0
