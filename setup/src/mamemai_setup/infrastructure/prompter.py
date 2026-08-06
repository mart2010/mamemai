# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from __future__ import annotations

import re
import textwrap

import questionary

from ..services.ports import PromptChoice

_HEADING_WIDTH = 78
_BULLET = re.compile(r"^(\s*)(- )?(.*)$")


class QuestionaryPrompter:
    def select(self, message: str, choices: list[PromptChoice], default: str | None = None) -> str:
        qchoices = [questionary.Choice(c.label, value=c.value) for c in choices]
        # questionary wants the Choice object (or exact title) as default — resolve
        # from the value, ignoring a default that isn't among the choices.
        default_choice = next((qc for qc in qchoices if qc.value == default), None)
        return questionary.select(message, choices=qchoices, default=default_choice).ask()

    def select_many(self, message: str, choices: list[PromptChoice]) -> list[str]:
        return questionary.checkbox(
            message, choices=[questionary.Choice(c.label, value=c.value, checked=c.checked) for c in choices]
        ).ask()

    def confirm(self, message: str, default: bool = True) -> bool:
        return questionary.confirm(message, default=default).ask()

    def text(self, message: str, default: str = "") -> str:
        return questionary.text(message, default=default).ask()

    def info(self, message: str) -> None:
        print(message)

    def heading(self, title: str, lines: list[str] | None = None) -> None:
        """The border and every wrapped line share one width so the banner reads as
        one consistent block regardless of terminal width — long unwrapped lines used
        to get wrapped by the terminal itself, with no indent on the continuation, so
        bullets and their overflow drifted out of alignment. `replace_whitespace=False`
        matters for lines like run_wizard.py's `_current_state_lines`, which pad with
        extra internal spaces to line up a "key: value" column; they're short enough to
        never actually wrap, but textwrap would otherwise collapse that padding."""
        width = max(_HEADING_WIDTH, len(title) + 4)
        border = "=" * width
        print(f"\n{border}")
        print(f"  {title}")
        if lines:
            print()
            for line in lines:
                if not line:
                    print()
                    continue
                match = _BULLET.match(line)
                assert match is not None  # `(\s*)(- )?(.*)` with every group optional always matches
                leading, marker, text = match.groups()
                indent = f"  {leading}{marker or ''}"
                cont_indent = " " * len(indent)
                wrapped = textwrap.wrap(text, width=width - len(indent), replace_whitespace=False) or [""]
                print(f"{indent}{wrapped[0]}")
                for cont in wrapped[1:]:
                    print(f"{cont_indent}{cont}")
        print(f"{border}\n")
