#!/usr/bin/env python3
"""Viagem AI konzultant — CLI: parcela → strukturované vyhodnocení pro výkup.

Logika je v `konzultant/` (pipeline, policy, providers, harness).
Tento soubor jen skládá argumenty a tiskne výsledek.

Exit kódy: 0 OK · 1 testy/eval neprošly · 2 chyba vstupu/poskytovatele/validace.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from konzultant.checks import run_checks
from konzultant.domain import HTTP_PROVIDER_KEYS
from konzultant.harness import (
    CASES_DIR,
    RESULTS_DIR,
    case_input,
    list_cases,
    load_recorded_actual,
    verify_recorded,
)
from konzultant.inputs import (
    InputError,
    build_messages,
    load_parcel,
    load_schema,
    prompt_fingerprint,
    safe_case_id,
)
from konzultant.pipeline import EvaluationFailed, build_meta, evaluate
from konzultant.providers import PROVIDERS, ProviderError, select_provider


def run_all_tests() -> int:
    schema = load_schema()
    current = prompt_fingerprint()
    failed = 0
    kinds: Counter[str] = Counter()
    stale: list[str] = []
    cases = list_cases()
    if not cases:
        print("Žádné test cases v", CASES_DIR)
        return 1
    for case_dir in cases:
        name = case_dir.name
        exp_p, act_p = case_dir / "expected.json", case_dir / "actual.json"
        if case_input(case_dir) is None or not exp_p.exists() or not act_p.exists():
            print(f"[FAIL] {name}: chybí input.(json|txt), expected.json nebo actual.json")
            failed += 1
            continue
        expected = json.loads(exp_p.read_text(encoding="utf-8"))
        actual = json.loads(act_p.read_text(encoding="utf-8"))
        meta = actual.get("_meta") or {}
        kinds[meta.get("zdroj", "neuvedeno")] += 1
        if meta.get("prompt_sha") != current:
            stale.append(name)
        errs = verify_recorded(actual, schema) + run_checks(expected.get("kontroly") or {}, actual)
        if errs:
            print(f"[FAIL] {name}")
            for e in errs:
                print("  -", e)
            failed += 1
        else:
            print(
                f"[OK]   {name}  doporuceni={actual['doporuceni']} skore={actual['skore_zajmu']} "
                f"jistota={actual['jistota']['skore']}"
            )
    print(f"\nHotovo: {len(cases) - failed}/{len(cases)} OK")
    print("Původ actual.json: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    if kinds.get("referencni"):
        print(
            "POZOR: 'referencni' = výstup sestavený autorem podle promptu, ne běh modelu."
        )
    if kinds.get("live-agent-llm"):
        print(
            "POZOR: 'live-agent-llm' = interní LLM agenta + schema/policy, ne placené cloud API."
        )
    if kinds.get("referencni") or kinds.get("live-agent-llm"):
        print("Cloud měření: python3 evaluate.py --eval-live --save-actual (vyžaduje API klíč).")
    if stale:
        print(
            f"POZOR: {len(stale)} actual.json vznikly pro jinou verzi promptu/schématu "
            f"(prompt_sha ≠ {current}): {', '.join(stale)}"
        )
    return 1 if failed else 0


def eval_live(args: argparse.Namespace) -> int:
    provider = select_provider(args.provider, args.model)
    cases = list_cases(args.cases.split(",") if args.cases else None)
    report: dict[str, Any] = {
        "cas": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "poskytovatel": provider.name,
        "model": provider.model,
        "prompt_sha": prompt_fingerprint(),
        "runs": args.runs,
        "cases": {},
    }
    total_runs = total_pass = 0
    for case_dir in cases:
        parcel = load_parcel(str(case_input(case_dir)))
        checks = json.loads((case_dir / "expected.json").read_text(encoding="utf-8")).get("kontroly") or {}
        runs: list[dict[str, Any]] = []
        for i in range(args.runs):
            try:
                result = evaluate(parcel, provider)
            except (EvaluationFailed, ProviderError) as e:
                runs.append({"ok": False, "chyby": [str(e)]})
                continue
            data = result.data
            errs = run_checks(checks, data)
            runs.append({
                "ok": not errs,
                "chyby": errs,
                "doporuceni": data["doporuceni"],
                "skore": data["skore_zajmu"],
                "jistota": data["jistota"]["skore"],
                "pokusy": result.attempts,
                "normalizace": result.normalizace,
                "politika": result.politika,
            })
            if args.save_actual and i == 0:
                data["_meta"] = build_meta(result, provider)
                (case_dir / "actual.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
        passed = sum(r["ok"] for r in runs)
        scores = [r["skore"] for r in runs if "skore" in r]
        recs = Counter(r["doporuceni"] for r in runs if "doporuceni" in r)
        summary = {
            "pass_rate": passed / len(runs),
            "doporuceni": dict(recs),
            "stabilni_doporuceni": len(recs) <= 1,
            "skore_rozptyl": (max(scores) - min(scores)) if scores else None,
            "skore_stdev": round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
            "behy": runs,
        }
        report["cases"][case_dir.name] = summary
        total_runs += len(runs)
        total_pass += passed
        flag = "OK  " if passed == len(runs) else "FAIL"
        print(
            f"[{flag}] {case_dir.name}  {passed}/{len(runs)}  doporuceni={dict(recs)}  "
            f"skore_rozptyl={summary['skore_rozptyl']}"
        )
        for r in runs:
            for e in r["chyby"]:
                print("    -", e)
    rate = total_pass / total_runs if total_runs else 0.0
    report["pass_rate"] = rate
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"eval-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nPass rate {rate:.0%} (práh {args.threshold:.0%}); report: {out}")
    return 0 if rate >= args.threshold else 1


def run_single(args: argparse.Namespace) -> int:
    parcel = load_parcel(args.parcel)
    if args.print_prompt:
        for m in build_messages(parcel):
            print(f"===== {m['role'].upper()} =====\n{m['content']}\n")
        return 0
    if args.offline:
        case_id = safe_case_id(parcel.get("id"))
        # Validace na hranici (schema + normalizace + brány) — ne blind replay.
        result_data = load_recorded_actual(case_id)
        recorded = CASES_DIR / case_id / "actual.json"
        print(f"Poznámka: offline režim vrací uložený výstup ({recorded}), ne model.", file=sys.stderr)
    else:
        provider = select_provider(args.provider, args.model)
        print(f"Model: {provider.name}/{provider.model}", file=sys.stderr)
        try:
            result = evaluate(parcel, provider)
        except ProviderError as e:
            if args.provider is None and not os.environ.get("VIAGEM_PROVIDER"):
                for cand, key in HTTP_PROVIDER_KEYS:
                    if os.environ.get(key) and cand != provider.name:
                        print(
                            f"Poznámka: {provider.name} selhal ({e}). "
                            f"Přepínám na záložního poskytovatele {cand}...",
                            file=sys.stderr,
                        )
                        provider = select_provider(cand, args.model)
                        result = evaluate(parcel, provider)
                        break
                else:
                    raise
            else:
                raise
        result_data = result.data
        result_data["_meta"] = build_meta(result, provider)
        for line in result.normalizace + result.politika:
            print(f"Runner upravil: {line}", file=sys.stderr)
        if args.save_actual:
            case_dir = CASES_DIR / safe_case_id(parcel.get("id"))
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "actual.json").write_text(
                json.dumps(result_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if case_input(case_dir) is None:
                (case_dir / "input.json").write_text(
                    json.dumps(parcel, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            print(f"Uloženo: {case_dir / 'actual.json'}", file=sys.stderr)
    text = json.dumps(result_data, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"Uloženo: {args.out}", file=sys.stderr)
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Vyhodnocení parcely pro výkup (Viagem)")
    p.add_argument("parcel", nargs="?", help="JSON / text soubor parcely, nebo '-' pro stdin")
    p.add_argument("--provider", choices=PROVIDERS, help="poskytovatel modelu (jinak auto)")
    p.add_argument("--model", help="přepíše výchozí model poskytovatele")
    p.add_argument("--offline", action="store_true", help="vrátit uložený tests/cases/<id>/actual.json")
    p.add_argument("--out", type=Path, help="uložit výstup do souboru")
    p.add_argument("--save-actual", action="store_true", help="uložit živý výstup do tests/cases/<id>/actual.json")
    p.add_argument("--print-prompt", action="store_true", help="vytisknout sestavený prompt a skončit")
    p.add_argument("--run-all-tests", action="store_true", help="offline kontrola uložených výstupů")
    p.add_argument("--eval-live", action="store_true", help="živý eval všech cases")
    p.add_argument("--runs", type=int, default=1, help="počet běhů na case (konzistence)")
    p.add_argument("--cases", help="čárkou oddělená id cases pro --eval-live")
    p.add_argument("--threshold", type=float, default=0.8, help="minimální pass rate pro --eval-live")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs musí být ≥ 1")
    if args.save_actual and args.offline:
        parser.error("--save-actual nelze s --offline")
    try:
        if args.run_all_tests:
            return run_all_tests()
        if args.eval_live:
            return eval_live(args)
        if not args.parcel:
            parser.error("zadej parcelu (soubor nebo '-'), --run-all-tests, nebo --eval-live")
        return run_single(args)
    except (InputError, ProviderError) as e:
        print(f"Chyba: {e}", file=sys.stderr)
        return 2
    except EvaluationFailed as e:
        print(f"Chyba: {e}\n--- poslední odpověď modelu ---\n{e.raw[:4000]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
