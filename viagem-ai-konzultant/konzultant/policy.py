"""Deterministická vrstva nad výstupem modelu.

Rozdělení práce (latent vs. deterministic):
- Model dělá úsudek: úrovně rizik, `skore_slozky.vyuzitelnost`, jistotu,
  doporučení, texty.
- Tento modul dělá aritmetiku a tvrdá pravidla, která jsou z definice
  jednoznačná: dopočet složek skóre z úrovní rizik, strop při kritickém
  riziku a brány pro `vykupit`.

Prompt obsahuje tatáž pravidla, aby fungoval i samostatně (vložený do
chatu bez runneru). Runner pak rozdíly NEskrývá: každá úprava je
zapsaná v `_meta.normalizace` / `_meta.politika`.
"""

from __future__ import annotations

from typing import Any

CORE_AXES = ("zaplava", "ochrana_prirody", "uzemni_plan", "pristup", "pravni_stav")
PLANNING_AXES = ("zaplava", "ochrana_prirody", "uzemni_plan")
SAFE_LEVELS = {"zadne", "nizke", "stredni"}

PLANNING_PENALTY = {"zadne": 0, "nizke": 3, "stredni": 8, "vysoke": 15, "neznamo": 6}
DALSI_PENALTY = {"zadne": 0, "nizke": 1, "stredni": 3, "vysoke": 6, "neznamo": 2}
AXIS_POINTS = {"zadne": 20, "nizke": 16, "stredni": 10, "vysoke": 5, "neznamo": 8, "kriticke": 0}

CRITICAL_SCORE_CAP = 35
VYKUPIT_MIN_SKORE = 70
VYKUPIT_MIN_JISTOTA = 60


def _levels(data: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    rizika = data["rizika"]
    core = {axis: rizika[axis]["uroven"] for axis in CORE_AXES}
    dalsi = [item["uroven"] for item in rizika["dalsi"]]
    return core, dalsi


def has_critical(data: dict[str, Any]) -> bool:
    core, dalsi = _levels(data)
    return "kriticke" in core.values() or "kriticke" in dalsi


def expected_components(data: dict[str, Any]) -> dict[str, int]:
    """Složky skóre, které jsou čistou funkcí úrovní rizik (vše kromě vyuzitelnost)."""
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
    """Přepočítá deterministické složky a `skore_zajmu` in-place.

    Vrací seznam změn (prázdný = model počítal správně). Předpokládá
    výstup, který už prošel schématem.
    """
    changes: list[str] = []
    slozky = data["skore_slozky"]
    for key, want in expected_components(data).items():
        if slozky[key] != want:
            changes.append(f"skore_slozky.{key}: {slozky[key]} → {want}")
            slozky[key] = want
    total = expected_total(data, slozky)
    if data["skore_zajmu"] != total:
        changes.append(f"skore_zajmu: {data['skore_zajmu']} → {total}")
        data["skore_zajmu"] = total
    return changes


def vykupit_blockers(data: dict[str, Any]) -> list[str]:
    """Důvody, proč `vykupit` není dovoleno (prázdný seznam = dovoleno)."""
    blockers: list[str] = []
    if data["skore_zajmu"] < VYKUPIT_MIN_SKORE:
        blockers.append(f"skore_zajmu {data['skore_zajmu']} < {VYKUPIT_MIN_SKORE}")
    if data["jistota"]["skore"] < VYKUPIT_MIN_JISTOTA:
        blockers.append(f"jistota {data['jistota']['skore']} < {VYKUPIT_MIN_JISTOTA}")
    if data["konflikty_dat"]:
        blockers.append("neprázdné konflikty_dat")
    core, _ = _levels(data)
    for axis, lvl in core.items():
        if lvl not in SAFE_LEVELS:
            blockers.append(f"rizika.{axis} = {lvl}")
    for item in data["rizika"]["dalsi"]:
        if item["uroven"] in {"vysoke", "kriticke"}:
            blockers.append(f"rizika.dalsi '{item['nazev']}' = {item['uroven']}")
    return blockers


def fallback_recommendation(data: dict[str, Any]) -> str:
    """Konzervativní náhrada za zakázané `vykupit`.

    Kritické plánovací/fyzické riziko → odmitnout; kritický právní stav
    (blokovaný převod) → odlozit; jinak → zkoumat_dal.
    """
    core, dalsi = _levels(data)
    if any(core[a] == "kriticke" for a in ("zaplava", "ochrana_prirody", "uzemni_plan", "pristup")):
        return "odmitnout"
    if core["pravni_stav"] == "kriticke" or "kriticke" in dalsi:
        return "odlozit"
    return "zkoumat_dal"


def gate_violations(data: dict[str, Any]) -> list[str]:
    if data["doporuceni"] != "vykupit":
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
