"""Deterministické skóre a brány. Konstanty v `domain.py`. Vstup = schema-validní JSON."""

from __future__ import annotations

from typing import Any

from .domain import (
    AXIS_POINTS,
    BLOCKING_DALSI,
    CORE_AXES,
    CRITICAL_SCORE_CAP,
    DALSI_PENALTY,
    PLANNING_AXES,
    PLANNING_PENALTY,
    SAFE_FOR_VYKUPIT,
    VYKUPIT_MIN_JISTOTA,
    VYKUPIT_MIN_SKORE,
)

# Re-export used by tests.
__all__ = [
    "PolicyError",
    "normalize_scores",
    "vykupit_blockers",
    "fallback_recommendation",
    "gate_violations",
    "enforce_gates",
    "has_critical",
    "expected_components",
    "expected_total",
    "CRITICAL_SCORE_CAP",
]


class PolicyError(Exception):
    """Neúplný výstup pro politiku."""


def _levels(data: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    try:
        rizika = data["rizika"]
        core = {axis: rizika[axis]["uroven"] for axis in CORE_AXES}
        dalsi = [item["uroven"] for item in rizika["dalsi"]]
    except (KeyError, TypeError) as e:
        raise PolicyError(
            f"neúplný výstup pro politiku (očekáváno schema-validní JSON): {e}"
        ) from e
    return core, dalsi


def has_critical(data: dict[str, Any]) -> bool:
    core, dalsi = _levels(data)
    return "kriticke" in core.values() or "kriticke" in dalsi


def expected_components(data: dict[str, Any]) -> dict[str, int]:
    """Složky skóre, které jsou čistou funkcí úrovní rizik (kromě vyuzitelnost)."""
    core, dalsi = _levels(data)
    planning = [core[a] for a in PLANNING_AXES]
    if "kriticke" in planning or "kriticke" in dalsi:
        rizika = 0
    else:
        penalty = sum(PLANNING_PENALTY[lvl] for lvl in planning)
        penalty += sum(DALSI_PENALTY[lvl] for lvl in dalsi)
        rizika = max(0, 30 - penalty)
    return {
        "rizika": rizika,
        "pravni_stav": AXIS_POINTS[core["pravni_stav"]],
        "pristup": AXIS_POINTS[core["pristup"]],
    }


def expected_total(data: dict[str, Any], components: dict[str, int]) -> int:
    total = sum(components.values())
    if has_critical(data):
        total = min(total, CRITICAL_SCORE_CAP)
    return total


def normalize_scores(data: dict[str, Any]) -> list[str]:
    """Přepočítá deterministické složky a `skore_zajmu` in-place."""
    changes: list[str] = []
    try:
        slozky = data["skore_slozky"]
    except KeyError as e:
        raise PolicyError("chybí skore_slozky") from e
    for key, want in expected_components(data).items():
        if slozky.get(key) != want:
            changes.append(f"skore_slozky.{key}: {slozky.get(key)} → {want}")
            slozky[key] = want
    total = expected_total(data, slozky)
    if data.get("skore_zajmu") != total:
        changes.append(f"skore_zajmu: {data.get('skore_zajmu')} → {total}")
        data["skore_zajmu"] = total
    return changes


def vykupit_blockers(data: dict[str, Any]) -> list[str]:
    """Důvody, proč `vykupit` není dovoleno (prázdný seznam = dovoleno)."""
    blockers: list[str] = []
    skore = data.get("skore_zajmu")
    if not isinstance(skore, int) or skore < VYKUPIT_MIN_SKORE:
        blockers.append(f"skore_zajmu {skore} < {VYKUPIT_MIN_SKORE}")
    try:
        jistota = data["jistota"]["skore"]
    except (KeyError, TypeError):
        blockers.append("chybí jistota.skore")
        jistota = -1
    if not isinstance(jistota, int) or jistota < VYKUPIT_MIN_JISTOTA:
        blockers.append(f"jistota {jistota} < {VYKUPIT_MIN_JISTOTA}")
    if data.get("konflikty_dat"):
        blockers.append("neprázdné konflikty_dat")
    core, _ = _levels(data)
    for axis, lvl in core.items():
        if lvl not in SAFE_FOR_VYKUPIT:
            blockers.append(f"rizika.{axis} = {lvl}")
    for item in data["rizika"]["dalsi"]:
        if item.get("uroven") in BLOCKING_DALSI:
            blockers.append(f"rizika.dalsi '{item.get('nazev')}' = {item.get('uroven')}")
    return blockers


def fallback_recommendation(data: dict[str, Any]) -> str:
    """Konzervativní náhrada za zakázané `vykupit`."""
    core, dalsi = _levels(data)
    reject_axes = PLANNING_AXES + ("pristup",)
    if any(core[a] == "kriticke" for a in reject_axes):
        return "odmitnout"
    if core["pravni_stav"] == "kriticke" or "kriticke" in dalsi:
        return "odlozit"
    return "zkoumat_dal"


def gate_violations(data: dict[str, Any]) -> list[str]:
    if data.get("doporuceni") != "vykupit":
        return []
    return [f"brána vykupit: {b}" for b in vykupit_blockers(data)]


def enforce_gates(data: dict[str, Any]) -> list[str]:
    """Pokud model doporučil zakázané `vykupit`, nahradí ho. Vrací záznam změn."""
    violations = gate_violations(data)
    if not violations:
        return []
    new = fallback_recommendation(data)
    data["doporuceni"] = new
    return violations + [f"doporuceni: vykupit → {new}"]
