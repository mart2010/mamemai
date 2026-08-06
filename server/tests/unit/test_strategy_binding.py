from dataclasses import dataclass
from uuid import UUID, uuid4

from mamemai_server.server import _build_strategies, _KNOWN_STRATEGY_NAMES


@dataclass
class _Persona:
    id: UUID
    name: str
    strategy: str | None


class _Repo:
    def __init__(self, *personas):
        self._personas = personas

    def list_all(self):
        return list(self._personas)


def test_capability_a_persona_does_not_implement_is_silent(capsys):
    """Spec: TR-004 — a strategy name registered elsewhere in the server is a persona
    declining *this* capability, not a misconfiguration, and must not warn.

    Observed 2026-08-03: 'cognitive_coach' is registered for selection and assessment and
    deliberately has no enrichment strategy, so it warned on every server start and every
    offline run. A permanent warning for a non-problem trains people to ignore warnings.
    """
    known = next(iter(_KNOWN_STRATEGY_NAMES))
    repo = _Repo(_Persona(id=uuid4(), name="Coach", strategy=known))

    bound = _build_strategies(ctx=None, factories={}, persona_repo=repo)

    assert bound == {}
    assert capsys.readouterr().out == ""


def test_name_no_registry_knows_still_warns(capsys):
    """Spec: TR-004 — a genuinely unknown name is a real gap and must still be reported,
    including when the registry it missed is empty (which used to suppress everything)."""
    repo = _Repo(_Persona(id=uuid4(), name="Ghost", strategy="no_such_strategy"))

    bound = _build_strategies(ctx=None, factories={}, persona_repo=repo)

    assert bound == {}
    out = capsys.readouterr().out
    assert "Ghost" in out and "no_such_strategy" in out


def test_registered_strategy_is_bound(capsys):
    """Spec: TR-004 — the ordinary path: a name this registry knows binds its object and
    says nothing."""
    persona_id = uuid4()
    repo = _Repo(_Persona(id=persona_id, name="Tutor", strategy="mine"))
    sentinel = object()

    bound = _build_strategies(ctx=None, factories={"mine": lambda _ctx: sentinel}, persona_repo=repo)

    assert bound == {persona_id: sentinel}
    assert capsys.readouterr().out == ""


def test_persona_without_a_strategy_is_skipped(capsys):
    """Spec: TR-004 — most personas (the GeneralAssistant included) declare no strategy at
    all; that is the norm, not a gap."""
    repo = _Repo(_Persona(id=uuid4(), name="GA", strategy=None))

    assert _build_strategies(ctx=None, factories={"mine": lambda _ctx: object()}, persona_repo=repo) == {}
    assert capsys.readouterr().out == ""
