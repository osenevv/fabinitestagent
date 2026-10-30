"""Pipeline: parcela → model → parsování → validace na hranici → (1× oprava) → politika."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any

from . import policy
from .inputs import build_messages, load_schema, prompt_fingerprint
from .providers import Provider
from .schema import validate

MAX_ATTEMPTS = 2


class EvaluationFailed(Exception):
    def __init__(self, errors: list[str], raw: str, attempts: int) -> None:
        super().__init__("výstup modelu neprošel validací ani po opravě:\n- " + "\n- ".join(errors))
        self.errors = errors
        self.raw = raw
        self.attempts = attempts


def extract_json(text: str) -> Any:
    """Tolerantní parsování: holý JSON, ```json / ```JSON blok, nebo text kolem objektu."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        while lines and lines[-1].strip() == "":
            lines = lines[:-1]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def coerce_json_types(obj: Any) -> Any:
    """LLM často vrátí 88.0 místo 88 — schema chce integer."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float) and obj.is_integer() and abs(obj) <= 2**53:
        return int(obj)
    if isinstance(obj, dict):
        return {k: coerce_json_types(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [coerce_json_types(v) for v in obj]
    return obj


def validate_at_boundary(data: Any, schema: dict[str, Any]) -> list[str]:
    """Validate model output right after parse."""
    if not isinstance(data, dict):
        return ["výstup není JSON objekt"]
    return validate(data, schema)


def postprocess(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Normalize scores and enforce gates in-place."""
    normalizace = policy.normalize_scores(data)
    politika = policy.enforce_gates(data)
    return normalizace, politika


@dataclass
class Result:
    data: dict[str, Any]
    attempts: int
    normalizace: list[str] = field(default_factory=list)
    politika: list[str] = field(default_factory=list)


def _feedback(errors: list[str]) -> str:
    return (
        "Tvůj předchozí výstup porušil kontrakt. Oprav POUZE tyto chyby a vrať celý JSON znovu:\n- "
        + "\n- ".join(errors)
    )


def evaluate(parcel: dict[str, Any], provider: Provider) -> Result:
    schema = load_schema()
    messages = build_messages(parcel)
    raw = ""
    errors: list[str] = []
    last_valid: tuple[dict[str, Any], int] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = provider.complete(messages, schema)
        try:
            data = coerce_json_types(extract_json(raw))
        except json.JSONDecodeError as e:
            errors = [f"výstup není validní JSON ({e.msg}, řádek {e.lineno})"]
        else:
            errors = validate_at_boundary(data, schema)
            if not errors:
                probe = json.loads(json.dumps(data))
                try:
                    policy.normalize_scores(probe)
                    errors = policy.gate_violations(probe)
                except policy.PolicyError as e:
                    errors = [str(e)]
                else:
                    if not errors:
                        normalizace, politika = postprocess(data)
                        return Result(data, attempt, normalizace, politika)
                    last_valid = (data, attempt)
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": _feedback(errors)},
        ]
    if last_valid is not None:
        data, _ = last_valid
        normalizace, politika = postprocess(data)
        return Result(data, MAX_ATTEMPTS, normalizace, politika)
    raise EvaluationFailed(errors, raw, MAX_ATTEMPTS)


def build_meta(result: Result, provider: Provider, *, source: str = "live") -> dict[str, Any]:
    return {
        "zdroj": source,
        "poskytovatel": provider.name,
        "model": provider.model,
        "pokusy": result.attempts,
        "prompt_sha": prompt_fingerprint(),
        "cas": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "normalizace": result.normalizace,
        "politika": result.politika,
        "poznamky_poskytovatele": list(provider.notes),
    }
