"""Kontroly `expected.json` proti výstupu (prahy, ne golden string).

Neznámý klíč v `kontroly` je chyba — překlep (např. `zaplava_mni`) by jinak
kontrolu potichu vypnul a test by zeleně procházel bez ověření.
"""

from __future__ import annotations

from typing import Any

ORDER = ["zadne", "nizke", "stredni", "vysoke", "kriticke"]
AXES = ("zaplava", "ochrana_prirody", "uzemni_plan", "pristup", "pravni_stav")

KNOWN = {
    "doporuceni",          # list povolených
    "nesmi_doporuceni",    # list zakázaných
    "skore_zajmu_min", "skore_zajmu_max",
    "jistota_min", "jistota_max",
    "hlavni_potencial",    # list povolených
    "rizika_min",          # {osa: úroveň}; `neznamo` min NESPLŇUJE
    "rizika_in",           # {osa: [úrovně]}
    "dalsi_obsahuje",      # list podřetězců, každý musí být v nějakém rizika.dalsi[].nazev (case-insensitive)
    "musi_mit_konflikt", "musi_mit_chybejici", "bez_konfliktu",
    "vlastnictvi_typ",     # list povolených
    "identifikace",        # {pole: hodnota} přesná shoda v parcelni_identifikace
}


def _risk(actual: dict[str, Any], axis: str) -> Any:
    return ((actual.get("rizika") or {}).get(axis) or {}).get("uroven")


def run_checks(checks: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    unknown = set(checks) - KNOWN
    if unknown:
        errs.append(f"neznámé kontroly v expected.json: {sorted(unknown)}")
    dop = actual.get("doporuceni")
    if "doporuceni" in checks and dop not in checks["doporuceni"]:
        errs.append(f"doporuceni={dop} mimo očekávané {checks['doporuceni']}")
    if dop in checks.get("nesmi_doporuceni", []):
        errs.append(f"doporuceni={dop} je zakázané")
    skore = actual.get("skore_zajmu")
    if "skore_zajmu_min" in checks and not (isinstance(skore, int) and skore >= checks["skore_zajmu_min"]):
        errs.append(f"skore_zajmu={skore} pod minimem {checks['skore_zajmu_min']}")
    if "skore_zajmu_max" in checks and not (isinstance(skore, int) and skore <= checks["skore_zajmu_max"]):
        errs.append(f"skore_zajmu={skore} nad maximem {checks['skore_zajmu_max']}")
    jistota = (actual.get("jistota") or {}).get("skore")
    if "jistota_min" in checks and not (isinstance(jistota, int) and jistota >= checks["jistota_min"]):
        errs.append(f"jistota={jistota} pod minimem {checks['jistota_min']}")
    if "jistota_max" in checks and not (isinstance(jistota, int) and jistota <= checks["jistota_max"]):
        errs.append(f"jistota={jistota} nad maximem {checks['jistota_max']}")
    if "hlavni_potencial" in checks:
        got = (actual.get("vyuzitelnost") or {}).get("hlavni_potencial")
        if got not in checks["hlavni_potencial"]:
            errs.append(f"hlavni_potencial={got} mimo {checks['hlavni_potencial']}")
    for axis, want in (checks.get("rizika_min") or {}).items():
        got = _risk(actual, axis)
        if axis not in AXES or want not in ORDER:
            errs.append(f"rizika_min: neplatná osa/úroveň {axis}={want}")
        elif got not in ORDER or ORDER.index(got) < ORDER.index(want):
            errs.append(f"rizika.{axis}={got} < požadované min {want}")
    for axis, allowed in (checks.get("rizika_in") or {}).items():
        got = _risk(actual, axis)
        if axis not in AXES:
            errs.append(f"rizika_in: neplatná osa {axis}")
        elif got not in allowed:
            errs.append(f"rizika.{axis}={got} mimo povolené {allowed}")
    names = [str(d.get("nazev", "")).lower() for d in (actual.get("rizika") or {}).get("dalsi", [])]
    for needle in checks.get("dalsi_obsahuje", []):
        if not any(needle.lower() in n for n in names):
            errs.append(f"rizika.dalsi neobsahuje položku s '{needle}'")
    if checks.get("musi_mit_konflikt") and not actual.get("konflikty_dat"):
        errs.append("očekáván neprázdný konflikty_dat")
    if checks.get("bez_konfliktu") and actual.get("konflikty_dat"):
        errs.append(f"neočekávané konflikty_dat: {actual.get('konflikty_dat')}")
    if checks.get("musi_mit_chybejici") and not actual.get("chybejici_data"):
        errs.append("očekáván neprázdný chybejici_data")
    if "vlastnictvi_typ" in checks:
        got = (actual.get("vlastnictvi") or {}).get("typ")
        if got not in checks["vlastnictvi_typ"]:
            errs.append(f"vlastnictvi.typ={got} mimo {checks['vlastnictvi_typ']}")
    ident = actual.get("parcelni_identifikace") or {}
    for key, want in (checks.get("identifikace") or {}).items():
        if ident.get(key) != want:
            errs.append(f"parcelni_identifikace.{key}={ident.get(key)!r} ≠ {want!r}")
    return errs
