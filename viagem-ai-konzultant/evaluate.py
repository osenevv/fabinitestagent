#!/usr/bin/env python3
"""Minimální obal: parcela JSON → strukturované vyhodnocení pro výkup.

Použití:
  python evaluate.py data/parcels/01-stavebni-happy.json
  python evaluate.py data/parcels/01-stavebni-happy.json --out /tmp/out.json
  python evaluate.py --run-all-tests          # porovná expected vs actual (offline)
  python evaluate.py --live data/parcels/...  # volá LLM (vyžaduje API klíč)

Bez --live skript nevytváří nové modelové výstupy; live režim potřebuje
OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / XAI_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"
SCHEMA_PATH = ROOT / "schema" / "vyhodnoceni.schema.json"
CASES_DIR = ROOT / "tests" / "cases"

REQUIRED_TOP = [
    "parcelni_identifikace",
    "shrnuti",
    "skore_zajmu",
    "doporuceni",
    "vyuzitelnost",
    "rizika",
    "jistota",
    "overit_rucne",
    "duvodovani",
]
DOPORUCENI = {"vykupit", "zkoumat_dal", "odlozit", "odmitnout"}
UROVEN = {"zadne", "nizke", "stredni", "vysoke", "kriticke", "neznamo"}


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_messages(parcel: dict[str, Any]) -> list[dict[str, str]]:
    system = load_text(PROMPTS / "system.txt")
    template = load_text(PROMPTS / "user_template.txt")
    user = template.replace("{{PARCEL_JSON}}", json.dumps(parcel, ensure_ascii=False, indent=2))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def validate_vyhodnoceni(data: Any) -> list[str]:
    """Lehká validace bez externí závislosti (jsonschema není povinné)."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["výstup není JSON objekt"]
    for key in REQUIRED_TOP:
        if key not in data:
            errs.append(f"chybí pole: {key}")
    if "skore_zajmu" in data and not (
        isinstance(data["skore_zajmu"], int) and 0 <= data["skore_zajmu"] <= 100
    ):
        errs.append("skore_zajmu musí být int 0–100")
    if "doporuceni" in data and data["doporuceni"] not in DOPORUCENI:
        errs.append(f"neplatné doporuceni: {data.get('doporuceni')}")
    jistota = data.get("jistota")
    if isinstance(jistota, dict):
        sk = jistota.get("skore")
        if not (isinstance(sk, int) and 0 <= sk <= 100):
            errs.append("jistota.skore musí být int 0–100")
    rizika = data.get("rizika")
    if isinstance(rizika, dict):
        for name in ("zaplava", "ochrana_prirody", "uzemni_plan"):
            r = rizika.get(name)
            if isinstance(r, dict) and r.get("uroven") not in UROVEN:
                errs.append(f"rizika.{name}.uroven neplatná")
    overit = data.get("overit_rucne")
    if overit is not None and (not isinstance(overit, list) or len(overit) < 1):
        errs.append("overit_rucne musí být neprázdné pole")
    return errs


def provider_config() -> tuple[str, str, str]:
    """Vrátí (base_url, api_key, model) podle dostupných env vars."""
    if os.environ.get("OPENAI_API_KEY"):
        return (
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            os.environ["OPENAI_API_KEY"],
            os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
    if os.environ.get("XAI_API_KEY"):
        return (
            os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
            os.environ["XAI_API_KEY"],
            os.environ.get("XAI_MODEL", "grok-3-mini"),
        )
    if os.environ.get("GEMINI_API_KEY"):
        return (
            os.environ.get(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/openai",
            ),
            os.environ["GEMINI_API_KEY"],
            os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        # Anthropic Messages API — jiný endpoint; mapujeme přes jednoduchý wrapper níže
        return (
            "anthropic",
            os.environ["ANTHROPIC_API_KEY"],
            os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        )
    raise SystemExit(
        "Chybí API klíč. Nastav OPENAI_API_KEY, XAI_API_KEY, GEMINI_API_KEY "
        "nebo ANTHROPIC_API_KEY. Bez klíče použij uložené tests/cases/*/actual.json."
    )


def chat_completions(base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    if base_url == "anthropic":
        return _anthropic_messages(api_key, model, messages)

    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} od API: {detail}") from e
    return payload["choices"][0]["message"]["content"]


def _anthropic_messages(api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    system = ""
    converted: list[dict[str, str]] = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            converted.append({"role": m["role"], "content": m["content"]})
    body = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.1,
        "system": system,
        "messages": converted,
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} od Anthropic: {detail}") from e
    parts = payload.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return text


def extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    return json.loads(text)


def evaluate_live(parcel: dict[str, Any]) -> dict[str, Any]:
    base_url, api_key, model = provider_config()
    messages = build_messages(parcel)
    raw = chat_completions(base_url, api_key, model, messages)
    data = extract_json(raw)
    errs = validate_vyhodnoceni(data)
    if errs:
        raise SystemExit("Model vrátil JSON, ale neprošel validací:\n- " + "\n- ".join(errs))
    data["_meta"] = {
        "model": model,
        "provider_base": base_url if base_url != "anthropic" else "https://api.anthropic.com",
        "live": True,
    }
    return data


def find_recorded_actual(parcel_path: Path) -> Path | None:
    """Hledá actual.json podle id parcely nebo názvu souboru."""
    stem = parcel_path.stem
    parcel = json.loads(parcel_path.read_text(encoding="utf-8"))
    case_id = parcel.get("id") or stem
    candidate = CASES_DIR / case_id / "actual.json"
    if candidate.exists():
        return candidate
    # fallback: shoda podle stem
    for d in CASES_DIR.iterdir():
        if d.is_dir() and d.name == stem:
            a = d / "actual.json"
            if a.exists():
                return a
    return None


def evaluate_offline(parcel_path: Path) -> dict[str, Any]:
    recorded = find_recorded_actual(parcel_path)
    if recorded is None:
        raise SystemExit(
            "Offline režim: nenalezen tests/cases/<id>/actual.json. "
            "Spusť s --live a API klíčem, nebo použij existující test case."
        )
    data = json.loads(recorded.read_text(encoding="utf-8"))
    # _meta může být v actual
    return data


def run_all_tests() -> int:
    """Porovná expected vs actual na úrovni klíčových polí + validace actual."""
    failed = 0
    cases = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    if not cases:
        print("Žádné test cases v", CASES_DIR)
        return 1
    for case_dir in cases:
        exp_p = case_dir / "expected.json"
        act_p = case_dir / "actual.json"
        name = case_dir.name
        if not exp_p.exists() or not act_p.exists():
            print(f"[FAIL] {name}: chybí expected.json nebo actual.json")
            failed += 1
            continue
        expected = json.loads(exp_p.read_text(encoding="utf-8"))
        actual = json.loads(act_p.read_text(encoding="utf-8"))
        errs = validate_vyhodnoceni(actual)
        checks = expected.get("kontroly") or {}
        if "doporuceni" in checks and actual.get("doporuceni") not in checks["doporuceni"]:
            errs.append(
                f"doporuceni={actual.get('doporuceni')} mimo očekávané {checks['doporuceni']}"
            )
        if "skore_zajmu_min" in checks:
            if actual.get("skore_zajmu", -1) < checks["skore_zajmu_min"]:
                errs.append("skore_zajmu pod minimem")
        if "skore_zajmu_max" in checks:
            if actual.get("skore_zajmu", 999) > checks["skore_zajmu_max"]:
                errs.append("skore_zajmu nad maximem")
        if "jistota_max" in checks:
            js = (actual.get("jistota") or {}).get("skore", 999)
            if js > checks["jistota_max"]:
                errs.append("jistota.skore nad maximem (očekávána nejistota)")
        if "zaplava_min" in checks:
            order = ["zadne", "nizke", "stredni", "vysoke", "kriticke"]
            got = (actual.get("rizika") or {}).get("zaplava", {}).get("uroven")
            want = checks["zaplava_min"]
            if got == "neznamo":
                pass  # neznamo splní min prah (chybějící/konfliktní data)
            elif got in order and want in order and order.index(got) < order.index(want):
                errs.append(f"zaplava {got} < požadované min {want}")
        if "zaplava_in" in checks:
            got = (actual.get("rizika") or {}).get("zaplava", {}).get("uroven")
            if got not in checks["zaplava_in"]:
                errs.append(f"zaplava {got} mimo povolené {checks['zaplava_in']}")
        if "musi_mit_konflikt" in checks and checks["musi_mit_konflikt"]:
            if not actual.get("konflikty_dat"):
                errs.append("očekáván neprázdný konflikty_dat")
        if "musi_mit_chybejici" in checks and checks["musi_mit_chybejici"]:
            if not actual.get("chybejici_data"):
                errs.append("očekáván neprázdný chybejici_data")
        if "hlavni_potencial" in checks:
            got_p = (actual.get("vyuzitelnost") or {}).get("hlavni_potencial")
            if got_p not in checks["hlavni_potencial"]:
                errs.append(f"hlavni_potencial={got_p} mimo {checks['hlavni_potencial']}")
        if errs:
            print(f"[FAIL] {name}")
            for e in errs:
                print("  -", e)
            failed += 1
        else:
            print(f"[OK]   {name}  doporuceni={actual.get('doporuceni')} skore={actual.get('skore_zajmu')}")
    print(f"\nHotovo: {len(cases) - failed}/{len(cases)} OK")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vyhodnocení parcely pro výkup (Viagem interview)")
    parser.add_argument("parcel", nargs="?", help="Cesta k JSON parcely")
    parser.add_argument("--live", action="store_true", help="Zavolat LLM přes API")
    parser.add_argument("--out", type=Path, help="Uložit výstup do souboru")
    parser.add_argument("--run-all-tests", action="store_true", help="Offline kontrola test cases")
    parser.add_argument("--print-prompt", action="store_true", help="Vytisknout sestavený prompt a skončit")
    args = parser.parse_args(argv)

    if args.run_all_tests:
        return run_all_tests()

    if not args.parcel:
        parser.error("Zadej cestu k parcel JSON, nebo --run-all-tests")

    parcel_path = Path(args.parcel)
    if not parcel_path.is_file():
        raise SystemExit(f"Soubor neexistuje: {parcel_path}")
    parcel = json.loads(parcel_path.read_text(encoding="utf-8"))

    if args.print_prompt:
        messages = build_messages(parcel)
        for m in messages:
            print(f"===== {m['role'].upper()} =====")
            print(m["content"])
            print()
        return 0

    if args.live:
        result = evaluate_live(parcel)
    else:
        result = evaluate_offline(parcel_path)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"Uloženo: {args.out}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
