# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-1 — nothing in server/, client/ or setup/ may depend on eval/."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # monorepo root
PACKAGES = ("server", "client", "setup")


def _python_files():
    for pkg in PACKAGES:
        for path in (ROOT / pkg).rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def test_roots_exist():
    assert all((ROOT / p).is_dir() for p in PACKAGES), ROOT


def test_no_python_module_imports_eval():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            if any(n.split(".")[0] in ("mamemai_eval", "eval") for n in names):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_no_package_declares_eval_as_dependency():
    offenders = [p for p in PACKAGES if "mamemai-eval" in (ROOT / p / "pyproject.toml").read_text(encoding="utf-8")]
    assert not offenders, offenders
