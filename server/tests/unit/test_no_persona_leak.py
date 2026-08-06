"""Spec: INV-6 — architectural conformance: no persona-specific vocabulary in the
generic core (`services/`, `domain/`).

INV-6 states that no generic code path may branch on `persona_state` or
`AssistantPersona.settings` contents. This test enforces the same rule one level
earlier, in the only place a violation is mechanically detectable before it becomes
a branch: the *vocabulary*. Generic code that has learned to talk about one persona's
domain is generic code that is about to start branching on it.

Rationale and the full leak inventory: docs/BRIEF_persona_contract.md.

Scope is deliberately the architectural core only. `infrastructure/` is NOT scanned:
it legitimately hosts each persona's own strategy package (e.g.
`infrastructure/language_tutor/`), where this vocabulary belongs. See the module's
closing note for the known generic-infrastructure hits that a widened scope would
need to address first.

Word list notes — both exclusions are deliberate:
  - "lesson" is NOT banned. `BundleLesson` / `bundle.lessons` is bundle-*format*
    vocabulary (a lesson is an ordering unit, ports.py) and belongs in `services/`.
  - "vocabulary" is NOT banned. Its occurrences in the core are the ordinary English
    sense ("the owning persona's own vocabulary"), not the language-learning one.
"""
import re
from pathlib import Path

import pytest

# The generic core: domain/ imports nothing external, services/ imports only domain
# and ports (INV-2). Neither may name a concrete persona's domain.
_SCANNED_PACKAGES = ("domain", "services")

# Terms that belong to one shipped persona's domain and nothing else. Substring-ish
# by design: "tutor" must also catch "tutors"/"tutoring"/"_TUTOR_DEBUG".
_PERSONA_TERMS = ("tutor", "learner", "CEFR", "HVPT")

_PATTERN = re.compile("|".join(_PERSONA_TERMS), re.IGNORECASE)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "mamemai_server"


def _core_source_files() -> list[Path]:
    files = [
        path
        for package in _SCANNED_PACKAGES
        for path in sorted((_PACKAGE_ROOT / package).rglob("*.py"))
    ]
    assert files, f"no source files found under {_PACKAGE_ROOT} — check the path"
    return files


@pytest.mark.parametrize("source", _core_source_files(), ids=lambda p: p.name)
def test_core_is_free_of_persona_vocabulary(source: Path) -> None:
    """Spec: INV-6 — `domain/` and `services/` must not name a concrete persona's domain."""
    offences = [
        f"{source.relative_to(_PACKAGE_ROOT)}:{lineno}: {line.strip()}"
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1)
        if _PATTERN.search(line)
    ]
    assert not offences, (
        "persona-specific vocabulary leaked into the generic core "
        f"(banned: {', '.join(_PERSONA_TERMS)}):\n  " + "\n  ".join(offences) + "\n\n"
        "Generic code must state the generic rule. If an example genuinely helps, use a "
        "neutral one ('a persona whose domain is language-sensitive'), not the shipped "
        "persona's name. If the behaviour itself is persona-specific, it belongs behind a "
        "port in infrastructure/<persona>/ — see docs/BRIEF_persona_contract.md."
    )


# Known, deliberately out-of-scope: generic *infrastructure* still carries this
# vocabulary — infrastructure/config.py:35 and infrastructure/llm/_common.py:50,55,63
# (the shared extraction-prompt builder describes one persona's episode exemption).
# Widening _SCANNED_PACKAGES to infrastructure/ requires excluding each persona's own
# strategy package and fixing those four sites first; tracked in
# docs/BRIEF_persona_contract.md.
