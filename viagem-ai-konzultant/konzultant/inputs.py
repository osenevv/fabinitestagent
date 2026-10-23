"""Načtení vstupu (JSON nebo volný text) a sestavení zpráv pro model."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
SCHEMA_PATH = ROOT / "schema" / "vyhodnoceni.schema.json"

MAX_INPUT_CHARS = 40_000  # ochrana před omylem vloženým celým PDF / LV se stovkami parcel
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class InputError(Exception):
    """Vstup nelze zpracovat (příliš velký, prázdný, neplatné id…)."""


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def parse_parcel_text(text: str, fallback_id: str | None = None) -> dict[str, Any]:
    """JSON objekt → dict; cokoliv jiného → `{"volny_text": …}`.

    Volný text je legitimní vstup: interviewer může vložit kopii z Nahlížení
    do KN nebo e-mail makléře. Prompt ho umí vytěžit (sekce 3).
    """
    text = text.strip()
    if not text:
        raise InputError("prázdný vstup")
    if len(text) > MAX_INPUT_CHARS:
        raise InputError(f"vstup má {len(text)} znaků, limit je {MAX_INPUT_CHARS} (jedna parcela)")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        if "id" not in parsed and fallback_id:
            parsed["id"] = fallback_id
        return parsed
    parcel: dict[str, Any] = {"volny_text": text}
    if fallback_id:
        parcel["id"] = fallback_id
    return parcel


def load_parcel(source: str) -> dict[str, Any]:
    """Cesta k souboru (.json / .txt / cokoliv), nebo `-` pro stdin."""
    if source == "-":
        return parse_parcel_text(sys.stdin.read())
    path = Path(source)
    if not path.is_file():
        raise InputError(f"soubor neexistuje: {path}")
    fallback = path.parent.name if path.stem == "input" else path.stem
    return parse_parcel_text(path.read_text(encoding="utf-8"), fallback_id=fallback)


def safe_case_id(raw: Any) -> str:
    """Id použitelné jako název adresáře (brání zápisu mimo tests/cases)."""
    if not isinstance(raw, str) or not _SAFE_ID.match(raw) or ".." in raw:
        raise InputError(f"neplatné id parcely pro uložení: {raw!r} (povoleno [A-Za-z0-9._-], bez '..')")
    return raw


def _model_view(parcel: dict[str, Any]) -> dict[str, Any]:
    """Pole jen pro runner model nevidí: `id` a cokoliv s prefixem `_`
    (např. `_poznamka_testu`). Nenesou informaci o parcele a název case
    typu `02-zaplava` nebo poznámka „záměrný konflikt“ by modelu napovídaly
    očekávanou odpověď — test by pak měřil čtení nápovědy, ne prompt."""
    return {k: v for k, v in parcel.items() if k != "id" and not k.startswith("_")}


def render_parcel(parcel: dict[str, Any]) -> str:
    view = _model_view(parcel)
    if set(view) == {"volny_text"}:
        body = view["volny_text"]
    else:
        body = json.dumps(view, ensure_ascii=False, indent=2)
    # Uzavírací značku uvnitř dat neutralizujeme (včetně mezer a velikosti písmen),
    # aby data nemohla „vyskočit“ z bloku <parcela_data> a tvářit se jako instrukce.
    return re.sub(r"<\s*/\s*parcela_data\s*>", "</parcela_data_>", body, flags=re.IGNORECASE)


def build_messages(parcel: dict[str, Any]) -> list[dict[str, str]]:
    system = (PROMPTS / "system.txt").read_text(encoding="utf-8")
    template = (PROMPTS / "user_template.txt").read_text(encoding="utf-8")
    user = template.replace("{{PARCEL}}", render_parcel(parcel))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def prompt_fingerprint() -> str:
    """sha256 promptů + schématu: identifikuje verzi, která vyrobila výstup."""
    h = hashlib.sha256()
    for p in (PROMPTS / "system.txt", PROMPTS / "user_template.txt", SCHEMA_PATH):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]
