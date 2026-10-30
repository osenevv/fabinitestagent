#!/usr/bin/env python3
"""Lokální OpenAI-kompatibilní /v1/chat/completions — stdlib only.

Slouží k důkazu, že evaluate.py opravdu volá HTTP provider.
Odpovědi sestavuje podle parcelního čísla ve user zprávě (agent-as-model).
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "cases"
HOST, PORT = "127.0.0.1", 8765


def _strip_meta(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return data


# Výchozí věrné odpovědi = referenční actual (prompt-faithful).
RESPONSES: dict[str, dict] = {
    "1234/5": _strip_meta(CASES / "01-stavebni-happy" / "actual.json"),
    "456/1": _strip_meta(CASES / "02-zaplava" / "actual.json"),
}

# Case 08: záměrně ilegální vykupit při kritické exekuci → runner musí přepsat.
_08 = _strip_meta(CASES / "08-exekuce" / "actual.json")
_08_bad = json.loads(json.dumps(_08))
_08_bad["doporuceni"] = "vykupit"
_08_bad["skore_zajmu"] = 90
_08_bad["skore_slozky"]["pravni_stav"] = 20
_08_bad["jistota"]["skore"] = 80
_08_bad["duvodovani"] = (
    "SIMULACE CHYBNÉHO MODELU: i přes exekuci doporučuji vykupit (má přepsat brána)."
)
RESPONSES["1880/3"] = _08_bad

_PARCEL_RE = re.compile(r"(?:cislo_parcely|Parcelní číslo)[\"\s:]*([0-9]+/[0-9]+|[0-9]+)")


def pick_response(messages: list[dict]) -> dict:
    blob = "\n".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))
    for needle, payload in RESPONSES.items():
        if needle in blob:
            return payload
    # fallback: zkus regex
    m = _PARCEL_RE.search(blob)
    if m and m.group(1) in RESPONSES:
        return RESPONSES[m.group(1)]
    raise KeyError(f"neznámá parcela ve zprávě (hledáno {list(RESPONSES)})")


class Handler(BaseHTTPRequestHandler):
    server_version = "ViagemLocalOpenAI/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[stub] {self.address_string()} {fmt % args}", flush=True)

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/v1/health", "/"):
            self._json(200, {"ok": True, "service": "viagem-local-openai-stub"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": f"only /v1/chat/completions, got {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json body"})
            return
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            self._json(401, {"error": "missing Bearer token"})
            return
        try:
            evaluation = pick_response(req.get("messages") or [])
        except KeyError as e:
            self._json(400, {"error": str(e)})
            return
        content = json.dumps(evaluation, ensure_ascii=False)
        self._json(
            200,
            {
                "id": "chatcmpl-local-stub",
                "object": "chat.completion",
                "model": req.get("model") or "local-sim",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )


def main() -> None:
    httpd = HTTPServer((HOST, PORT), Handler)
    print(f"Listening on http://{HOST}:{PORT}/v1/chat/completions", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
