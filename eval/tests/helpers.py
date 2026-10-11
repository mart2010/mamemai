# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from datetime import date
from uuid import UUID, uuid4

from mamemai_eval.domain.model import (
    EvalLanguage, FactKind, Ledger, LedgerFact, Probe, ProbeKind, SpeechArchetype, SyntheticUserProfile,
)
from mamemai_eval.usecases.corpus import ProfileContent


def sample_content(language=EvalLanguage.FR, archetype=SpeechArchetype.HESITANT, pid: UUID | None = None) -> ProfileContent:
    pid = pid or uuid4()
    other = EvalLanguage.EN if language is EvalLanguage.FR else EvalLanguage.FR
    ledger = Ledger(pid, (
        LedgerFact("f01", FactKind.ATTRIBUTE, "Camille habite à Lyon", 0, "maison", key_terms=("Lyon",)),
        LedgerFact("f02", FactKind.ATTRIBUTE, "Camille habite à Paris", 2, "maison", key_terms=("Paris",), supersedes="f01"),
        LedgerFact("f03", FactKind.EVENT, "Camille a vu une éclipse à Nice", 1, "voyage", date(2026, 6, 10),
                   key_terms=("Nice",)),
        LedgerFact("f04", FactKind.KNOWLEDGE, "Camille a appris que Jupiter est la plus grande planète", 1, "astronomie",
                   key_terms=("Jupiter",)),
    ), (date(2026, 6, 8), date(2026, 6, 22), date(2026, 7, 13)))
    probes = (
        Probe("p-single", ProbeKind.SINGLE_FACT, language, "Où ai-je vu l'éclipse ?", ("f03",)),
        Probe("p-hard", ProbeKind.HARD_NEGATIVE, language, "Quelle planète est la plus grosse ?", ("f04",), ("f03",)),
        Probe("p-update", ProbeKind.UPDATE, language, "Où j'habite maintenant ?", ("f02",), ("f01",)),
        Probe("p-agg", ProbeKind.AGGREGATION, language, "Où ai-je vécu ?", ("f01", "f02")),
        Probe("p-abst", ProbeKind.ABSTENTION, language, "Quelle est ma couleur préférée ?"),
        Probe("p-cross", ProbeKind.CROSS_LINGUAL, other, "Where did I see the eclipse?", ("f03",)),
    )
    profile = SyntheticUserProfile(pid, language, "Camille", 1, "Bio", archetype, "Style")
    return ProfileContent(profile, ledger, probes)
