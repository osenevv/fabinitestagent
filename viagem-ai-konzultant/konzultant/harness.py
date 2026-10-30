"""Testovací harness a práce s cases — mimo CLI (`evaluate.py`).

Odděleno od god-file CLI: listing cases, ověření recorded actual, strip `_meta`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import policy
from .inputs import ROOT, InputError, load_schema
from .schema import validate

CASES_DIR = ROOT / "tests" / "cases"
RESULTS_DIR = ROOT / "tests" / "results"


def strip_meta(data: dict[str, Any]) -> dict[str, Any]:
    """Odstraní runner metadata — zůstane kontrakt schématu."""
    return {k: v for k, v in data.items() if k != "_meta"}


def case_input(case_dir: Path) -> Path | None:
    for name in ("input.json", "input.txt"):
        if (case_dir / name).is_file():
            return case_dir / name
    return None


def list_cases(only: list[str] | None = None) -> list[Path]:
    cases = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    if only:
        wanted = set(only)
        missing = wanted - {c.name for c in cases}
        if missing:
            raise InputError(f"neznámé cases: {sorted(missing)}")
        cases = [c for c in cases if c.name in wanted]
    return cases


def load_case_bundle(case_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """(input_path, expected, actual_without_meta) pro testy / live mocky."""
    case_dir = CASES_DIR / case_id
    inp = case_input(case_dir)
    if inp is None:
        raise InputError(f"case {case_id}: chybí input.(json|txt)")
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    actual = strip_meta(json.loads((case_dir / "actual.json").read_text(encoding="utf-8")))
    return inp, expected, actual


def verify_recorded(actual: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    """Hranice offline výstupu: schéma + už normalizované skóre + brány."""
    schema = schema or load_schema()
    body = strip_meta(actual)
    errs = validate(body, schema)
    if errs:
        return errs
    probe = json.loads(json.dumps(body))
    try:
        errs += [f"nenormalizované skóre: {c}" for c in policy.normalize_scores(probe)]
        errs += policy.gate_violations(probe)
    except policy.PolicyError as e:
        errs.append(str(e))
    return errs


def load_recorded_actual(case_id: str) -> dict[str, Any]:
    """Načte a na hranici ověří `tests/cases/<id>/actual.json`."""
    recorded = CASES_DIR / case_id / "actual.json"
    if not recorded.is_file():
        raise InputError(f"offline: nenalezen {recorded}; pro novou parcelu spusť bez --offline")
    actual = json.loads(recorded.read_text(encoding="utf-8"))
    errs = verify_recorded(actual)
    if errs:
        raise InputError(
            "uložený actual.json neprošel validací na hranici:\n- " + "\n- ".join(errs)
        )
    return actual
