"""Offline ukázka se zastavenými časy. --live zapne HTTP tool calling. --serve přijímá n8n."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from agent import LiveLLMPlanner, OperationsAgent, OpsState, cli_approval, handle_n8n_webhook, step_clock
from models import AgentExecutionReport

EXAMPLES = Path(__file__).resolve().parent / "examples"
CLOCK_START = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)

SCENARIOS: list[dict[str, str]] = [
    {
        "slug": "100245-navrh",
        "title": "Návrh postupu, n8n timeout Shopify → Odoo u 100245",
        "prompt": (
            "Zjisti, proč objednávka 100245 nebyla expedována, "
            "a pokud je problém v synchronizaci, navrhni další postup."
        ),
        "gate": "approve",
    },
    {
        "slug": "100245-retry",
        "title": "Oprava synchronizace 100245 po schválení",
        "prompt": (
            "Prověř objednávku 100245. Pokud vázne synchronizace do skladu "
            "a objednávka je zaplacená, rovnou synchronizaci zopakuj."
        ),
        "gate": "approve",
    },
    {
        "slug": "100245-zamitnuto",
        "title": "Operátor zamítl retry_sync u 100245",
        "prompt": (
            "Prověř objednávku 100245. Pokud vázne synchronizace do skladu "
            "a objednávka je zaplacená, rovnou synchronizaci zopakuj."
        ),
        "gate": "reject",
    },
    {
        "slug": "100245-ceka",
        "title": "retry_sync u 100245 čeká na schválení",
        "prompt": (
            "Prověř objednávku 100245. Pokud vázne synchronizace do skladu "
            "a objednávka je zaplacená, rovnou synchronizaci zopakuj."
        ),
        "gate": "defer",
    },
    {
        "slug": "100246-platba",
        "title": "100246 na fabini.sk čeká na převod",
        "prompt": "Zjisti, proč objednávka 100246 nebyla expedována, a navrhni další postup.",
        "gate": "approve",
    },
    {
        "slug": "100500-wms",
        "title": "WMS u 100500 neodpovídá, eskalace incidentem",
        "prompt": "Zákazník urguje objednávku 100500. Zjisti stav expedice a vyřeš případný problém.",
        "gate": "approve",
    },
    {
        "slug": "999999-nenalezeno",
        "title": "Objednávka 999999 ve Shopify není",
        "prompt": "Zjisti stav objednávky 999999 a navrhni další postup.",
        "gate": "approve",
    },
]


def scripted_gate(mode: str) -> Callable[[str, dict[str, Any], str], bool | None]:
    def gate(tool_name: str, tool_args: dict[str, Any], reason: str) -> bool | None:
        print(f"  [HITL] {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")
        print(f"         {reason}")
        if mode == "reject":
            print("         → zamítnuto")
            return False
        if mode == "defer":
            print("         → čeká na operátora")
            return None
        print("         → schváleno")
        return True

    return gate


def run_scenario(scenario: dict[str, str], live: bool, interactive: bool, save: bool) -> AgentExecutionReport:
    print("\n" + "=" * 78)
    print(scenario["title"])
    print(f"ZADÁNÍ: {scenario['prompt']}")
    print("=" * 78)
    if live:
        planner = LiveLLMPlanner.from_env()
        if planner is None:
            raise SystemExit("Chybí XAI_API_KEY, OPENAI_API_KEY nebo GEMINI_API_KEY.")
        agent = OperationsAgent(approval_callback=cli_approval if interactive else scripted_gate(scenario["gate"]))
        report = agent.run(scenario["prompt"], planner=planner)
    else:
        callback = cli_approval if interactive else scripted_gate(scenario["gate"])
        agent = OperationsAgent(approval_callback=callback, clock=step_clock(CLOCK_START))
        report = agent.run(scenario["prompt"])
    payload = report.model_dump_json(indent=2)
    print(payload)
    if save:
        EXAMPLES.mkdir(exist_ok=True)
        (EXAMPLES / f"{scenario['slug']}.json").write_text(payload + "\n", encoding="utf-8")
    return report


def build_server(
    host: str,
    port: int,
    ops: OpsState,
    clock: Callable[[], datetime] | None = None,
    planner: Callable[..., Any] | None = None,
) -> ThreadingHTTPServer:
    """POST /hooks/n8n přijme webhook. GET /health, /approvals a /runs jsou pro provoz."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            server = self.server
            if path == "/health":
                self._json(200, {"status": "ok"})
                return
            if path == "/approvals":
                self._json(200, {"approvals": server.ops.pending()})
                return
            if path == "/runs":
                self._json(200, {"runs": server.ops.runs()})
                return
            self._json(404, {"status": "not_found"})

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/hooks/n8n":
                self._json(404, {"status": "not_found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode() or "{}")
            except json.JSONDecodeError:
                self._json(400, {"status": "bad_json"})
                return
            if not isinstance(payload, dict):
                self._json(400, {"status": "bad_json"})
                return
            server = self.server
            try:
                body = handle_n8n_webhook(payload, clock=server.clock, ops=server.ops, planner=server.planner)
                self._json(200, body)
            except Exception as exc:
                self._json(500, {"status": "error", "message": f"Chyba při zpracování webhooku: {exc}"})

        def _json(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.ops = ops
    httpd.clock = clock or (lambda: datetime.now(timezone.utc))
    httpd.planner = planner
    return httpd


def serve(host: str = "0.0.0.0", port: int = 8080, live: bool = False) -> None:
    directory = os.environ.get("FABINI_STATE_DIR", "var")
    planner = None
    if live:
        planner = LiveLLMPlanner.from_env()
        if planner is None:
            raise SystemExit("Chybí XAI_API_KEY, OPENAI_API_KEY nebo GEMINI_API_KEY.")
    httpd = build_server(host, port, OpsState(directory), planner=planner)
    bound_host, bound_port = httpd.server_address[:2]
    print(f"Webhook: http://{bound_host}:{bound_port}/hooks/n8n")
    print(f"Stav: {directory}")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="FABINI operations agent")
    parser.add_argument("--live", action="store_true", help="HTTP tool calling podle API klíče v prostředí.")
    parser.add_argument("--serve", action="store_true", help="HTTP server pro n8n webhook.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--interactive", action="store_true", help="Na rizikovou akci se zeptat v terminálu.")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("slug", nargs="?")
    args = parser.parse_args()
    if args.serve:
        serve(host=args.host, port=args.port, live=args.live)
        return
    chosen = SCENARIOS
    if args.slug:
        chosen = [item for item in SCENARIOS if item["slug"] == args.slug]
        if not chosen:
            known = ", ".join(item["slug"] for item in SCENARIOS)
            raise SystemExit(f"Neznámý scénář {args.slug}. Známé: {known}")
    save = not args.no_save and not args.live and not args.interactive
    for scenario in chosen:
        run_scenario(scenario, live=args.live, interactive=args.interactive, save=save)


if __name__ == "__main__":
    main()
