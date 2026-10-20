"""Smyčka pro provozní diagnostiku. Planner jen navrhne nástroj. Spustí ho brána.

Live model volá OpenAI-kompatibilní /v1/chat/completions přes urllib a vrací
tool_calls. Python je vykoná. SDK, které by nástroje spustilo samo, tu není.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from mock_systems import KNOWN_TOOLS, RISKY_TOOLS, MockSystems
from models import (
    AgentExecutionReport,
    ApprovalState,
    AuditStep,
    ExecutionStatus,
    FinalLLMDecision,
)

MAX_ITERATIONS = 6
IntentChoice = Literal["auto", "propose", "execute"]
ApprovalCallback = Callable[[str, dict[str, Any], str], bool | None]
Clock = Callable[[], datetime]

SOP_SYNC = "SOP-OPS-01: Shopify->Odoo Sync Retry"
SOP_BANK = "SOP-FIN-02: Bank Transfer SLA 3 Days"
SOP_WMS = "SOP-IT-09: Odoo/WMS 503 L2 Escalation"
SOP_ID = "SOP-CC-03: Ověření čísla objednávky"
SOP_CAP = "SOP-OPS-04: Předání operátorovi po limitu kroků"

# ponytail: word-level intent. Pass intent="propose"|"execute" when the sentence is ambiguous.
_LEAKS = (
    "503",
    "504",
    "500",
    "502",
    "timeout",
    "odoo",
    "n8n",
    "wms",
    "erp",
    "http",
    "traceback",
    "shopify",
    "api",
    "json",
    "database",
    "sql",
    "exception",
    "stacktrace",
)

_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}


def _tool(name: str, description: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema or _SCHEMA,
        },
    }


TOOL_SCHEMAS = [
    _tool("get_order", "Shopify hlavička: store, GID, SKU, tagy FABINI_GARANCE."),
    _tool("get_payment", "Stav platby. Bez PAID se objednávka do skladu neposílá."),
    _tool("get_fulfillment", "Stav expedice ve WMS napojeném na Odoo."),
    _tool("get_sync_logs", "Poslední běh n8n flow Shopify → Odoo."),
    _tool("retry_sync", "Znovu spustí přenos Shopify → Odoo/WMS. Mutace, až po schválení."),
    _tool(
        "create_ticket",
        "Incident pro L2. Mutace, až po schválení.",
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        },
    ),
]

SYSTEM_PROMPT = """Jsi provozní agent FABINI (Shopify fabini.cz / fabini.sk, n8n, Odoo, WMS).
Diagnostikuj nástroji. Nemáš je čím spustit sám: vrať tool_calls a počkej na výsledek.
Režim propose: retry_sync ani create_ticket nevolej, jen navrhni postup.
Režim execute: rizikový nástroj navrhni, až když na to data stačí. Brána si řekne o schválení.
Nezaplacenou objednávku do skladu neposílej. Když sklad spadne, založ incident, ne další retry.
Až nástroje stačí, vrať jen JSON: status, root_cause, recommended_next_steps, summary_for_operator, sop_reference, customer_care_suggestion.
customer_care_suggestion je věta pro zákazníka. Žádné kódy, Odoo, n8n, HTTP."""


class Intent(str, Enum):
    PROPOSE = "propose"
    EXECUTE = "execute"


@dataclass
class PlannerAction:
    thought: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    finish: bool = False


@dataclass
class Session:
    request: str
    order_id: str | None
    intent: Intent
    env: MockSystems
    order: dict[str, Any] | None = None
    payment: dict[str, Any] | None = None
    fulfillment: dict[str, Any] | None = None
    sync_logs: dict[str, Any] | None = None
    order_error: dict[str, Any] | None = None
    payment_error: dict[str, Any] | None = None
    sync_logs_error: dict[str, Any] | None = None
    fulfillment_error: dict[str, Any] | None = None
    retry_result: dict[str, Any] | None = None
    ticket_result: dict[str, Any] | None = None
    tried: set[str] = field(default_factory=set)
    executed_retries: set[str] = field(default_factory=set)
    failed_retry_attempts: set[str] = field(default_factory=set)
    ticket_done: bool = False
    rejected: list[str] = field(default_factory=list)
    approval_pending: dict[str, Any] | None = None
    hit_step_cap: bool = False
    final_thought: str = ""
    trail: list[AuditStep] = field(default_factory=list)
    llm_decision: FinalLLMDecision | None = None


def classify_intent(prompt: str) -> Intent:
    words = re.findall(r"[0-9a-záčďéěíňóřšťúůýž]+", prompt.casefold())
    text = " ".join(words)
    phrases = (
        "zkus znovu",
        "vytvoř ticket",
        "vytvor ticket",
        "založ ticket",
        "zaloz ticket",
        "založ incident",
        "zaloz incident",
    )
    if any(phrase in text for phrase in phrases):
        return Intent.EXECUTE
    prefixes = (
        "zařiď",
        "zarid",
        "zařid",
        "vyřeš",
        "vyres",
        "zopakuj",
        "zopakovat",
        "proveď",
        "provést",
        "provest",
        "spusť",
        "spust",
        "eskaluj",
        "retry",
    )
    EXCLUDED_EXECUTE_WORDS = {
        "opravdu",
        "oprava",
        "opravy",
        "opravě",
        "opravu",
        "opravou",
        "vyřešen",
        "vyřešeno",
        "vyřešena",
        "vyřešeny",
        "vyřešení",
        "proveden",
        "provedena",
        "provedeno",
        "provedeny",
        "provedení",
    }
    for word in words:
        if word in EXCLUDED_EXECUTE_WORDS:
            continue
        if word.startswith("oprav"):
            return Intent.EXECUTE
        if word.startswith("proved") and not word.startswith("proveden"):
            return Intent.EXECUTE
        if any(word.startswith(prefix) for prefix in prefixes):
            return Intent.EXECUTE
    return Intent.PROPOSE


def resolve_intent(prompt: str, intent: IntentChoice) -> Intent:
    if intent == "propose":
        return Intent.PROPOSE
    if intent == "execute":
        return Intent.EXECUTE
    if intent != "auto":
        raise ValueError(f"intent musí být auto, propose nebo execute, ne {intent!r}")
    return classify_intent(prompt)


def extract_order_id(prompt: str) -> str | None:
    match = re.search(r"\b(\d{6})\b", prompt)
    return match.group(1) if match else None


def step_clock(start: datetime | None = None) -> Clock:
    origin = start or datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
    tick = 0

    def clock() -> datetime:
        nonlocal tick
        moment = origin + timedelta(seconds=tick)
        tick += 1
        return moment

    return clock


def _order_scope(state: Session, tool_args: dict[str, Any]) -> tuple[str, str] | None:
    """Guardrail: a tool may touch only the order named in the request."""
    if state.order_id is None:
        return ("ORDER_SCOPE", "V zadání není číslo objednávky. Systémy nevolám.")
    if "order_id" in tool_args:
        raw = tool_args.get("order_id")
        given = str(raw).strip() if raw is not None else ""
        if not given or given != state.order_id or str(raw) != state.order_id:
            return (
                "ORDER_SCOPE",
                f"Nástroj míří na objednávku {raw!r}, zadání řeší {state.order_id}.",
            )
    desc = tool_args.get("description")
    if isinstance(desc, str):
        matches = re.findall(r"\b(\d{6})\b", desc)
        for match in matches:
            if match != state.order_id:
                return (
                    "ORDER_SCOPE",
                    f"Nástroj míří na objednávku {match!r}, zadání řeší {state.order_id}.",
                )
    return None


def _retry_already_decided(state: Session, order_id: str) -> bool:
    for step in state.trail:
        if step.tool_called != "retry_sync":
            continue
        if str(step.tool_args.get("order_id", "")).strip() != order_id:
            continue
        if step.approval_status in (ApprovalState.HUMAN_REJECTED, ApprovalState.AWAITING):
            return True
    return False


def _signature(tool_name: str, tool_args: dict[str, Any]) -> str:
    payload = json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)
    return f"{tool_name}:{payload}"


def _payment_status(state: Session) -> str | None:
    return None if not state.payment else state.payment.get("status")


def _sync_status(state: Session) -> str | None:
    return None if not state.sync_logs else state.sync_logs.get("status")


def _sync_code(state: Session) -> str | None:
    return None if not state.sync_logs else state.sync_logs.get("error_code")


def _fulfillment_label(state: Session) -> str:
    if state.fulfillment_error:
        return "NEDOSTUPNÉ"
    if not state.fulfillment:
        return "NEZNÁMÉ"
    return str(state.fulfillment.get("status"))


def _item_title(state: Session) -> str:
    items = (state.order or {}).get("items") or []
    if items and isinstance(items[0], dict) and items[0].get("title"):
        return str(items[0]["title"])
    return "zboží"


def _leaks(text: str) -> bool:
    folded = text.casefold()
    return any(token in folded for token in _LEAKS)


_SAFE_CARE = "Dobrý den, vaši objednávku právě prověřujeme a ozveme se, jakmile budeme vědět víc."


def _care(text: str) -> str:
    return _SAFE_CARE if _leaks(text) else text


def _decision(
    status: ExecutionStatus,
    cause: str,
    steps: list[str],
    summary: str,
    sop: str,
    care: str,
) -> FinalLLMDecision:
    return FinalLLMDecision(
        status=status,
        root_cause=cause,
        recommended_next_steps=steps,
        summary_for_operator=summary,
        sop_reference=sop,
        customer_care_suggestion=_care(care),
    )


def _explained(state: Session) -> bool:
    if state.order_id is None or state.order_error or state.payment_error or state.sync_logs_error:
        return True
    if state.ticket_result or (state.retry_result and state.retry_result.get("status") == "OK"):
        return True
    if state.failed_retry_attempts or state.fulfillment_error:
        return True
    return bool(state.payment and state.sync_logs and (state.fulfillment or state.fulfillment_error))


def ticket_description(state: Session) -> str:
    if state.fulfillment_error:
        err = state.fulfillment_error
        detail = err.get("details") or err.get("message") or "WMS nevrátilo stav."
        return (
            f"Výpadek WMS při kontrole objednávky {state.order_id}. "
            f"{err.get('error_type', 'ConnectionTimeoutError')}: {detail}"
        )
    if state.retry_result and state.retry_result.get("status") != "OK":
        return f"retry_sync objednávky {state.order_id} selhal: {state.retry_result.get('message')}"
    return f"Incident k objednávce {state.order_id}."


def approval_reason(state: Session, tool_name: str) -> str:
    if tool_name == "retry_sync":
        return (
            f"Objednávka {state.order_id} je {_payment_status(state)}, "
            f"ve skladu {_fulfillment_label(state)}, sync {_sync_code(state) or _sync_status(state)}. "
            "Chci zopakovat přenos Shopify → Odoo."
        )
    if tool_name == "create_ticket":
        return f"U {state.order_id} selhalo čtení skladu nebo retry. Chci incident pro L2."
    return f"Riziková akce {tool_name} vyžaduje schválení."


def _hold(order_id: str, title: str) -> str:
    return (
        f"Dobrý den, {title[0].lower() + title[1:]} z objednávky {order_id} "
        "ještě předáváme do skladu. Jakmile zásilka odejde, pošleme vám sledování."
    )


def _warehouse_note(order_id: str) -> str:
    return (
        f"Dobrý den, u objednávky {order_id} právě ověřujeme předání do skladu. "
        "Jakmile budeme mít jisté odeslání, ozveme se."
    )


def diagnose(state: Session) -> FinalLLMDecision:
    """Jediný text závěru. Planner sem nepatří, jen sem se dívá."""
    order_id = state.order_id or "(chybí)"
    title = _item_title(state)
    code = _sync_code(state) or "bez kódu"

    if state.order_id is None:
        return _decision(
            ExecutionStatus.FAILED,
            "V zadání není šestimístné číslo objednávky.",
            ["Doplň číslo objednávky a spusť diagnostiku znovu."],
            "Bez čísla objednávky není co otevřít ve Shopify.",
            SOP_ID,
            "Dobrý den, pošlete nám prosím číslo objednávky z potvrzovacího e-mailu a my ji dohledáme.",
        )

    if state.approval_pending:
        pending = state.approval_pending
        tool = pending["tool"]
        return _decision(
            ExecutionStatus.AWAITING_APPROVAL,
            f"Diagnostika {order_id} je hotová. {tool} čeká na operátora a nespustilo se.",
            [
                f"Schválit nebo zamítnout {tool} ve Slacku nebo Google Chatu.",
                pending["reason"],
            ],
            f"{tool} pro {order_id} visí na schválení. Zákazníkovi zatím nic neslibovat.",
            SOP_WMS if tool == "create_ticket" else SOP_SYNC,
            _hold(order_id, title),
        )

    if state.order_error and state.order_error.get("code") == 404:
        return _decision(
            ExecutionStatus.FAILED,
            f"Objednávka {order_id} ve Shopify neexistuje (HTTP 404).",
            ["Ověřit číslo s zákazníkem.", "Odoo ani WMS kvůli tomu nevolat."],
            f"{order_id} není na fabini.cz ani fabini.sk.",
            SOP_ID,
            "Dobrý den, objednávku s tímto číslem jsme nenašli. Pošlete nám prosím potvrzení z e-mailu a my ji dohledáme.",
        )

    if state.order_error:
        detail = state.order_error.get("details") or state.order_error.get("message") or "čtení objednávky selhalo"
        error_type = state.order_error.get("error_type") or "chyba"
        return _decision(
            ExecutionStatus.FAILED,
            f"Shopify čtení objednávky {order_id} spadlo ({error_type}): {detail}",
            ["Zopakovat čtení, až Shopify odpoví.", "Do té doby Odoo ani sklad nevolat."],
            f"get_order pro {order_id} nevrátil data.",
            SOP_ID,
            "Dobrý den, objednávku se nám teď nepodařilo otevřít. Ozveme se, jakmile ji dohledáme.",
        )

    if state.payment_error:
        detail = state.payment_error.get("details") or state.payment_error.get("message") or "čtení platby selhalo"
        return _decision(
            ExecutionStatus.FAILED,
            f"Čtení platby pro objednávku {order_id} spadlo: {detail}",
            ["Zopakovat čtení platby, až platební brána odpoví.", "Do té doby sklad nevolat."],
            f"get_payment pro {order_id} nevrátil data.",
            SOP_ID,
            "Dobrý den, platbu se nám teď nepodařilo ověřit. Ozveme se, jakmile stav prověříme.",
        )

    if state.sync_logs_error:
        detail = state.sync_logs_error.get("details") or state.sync_logs_error.get("message") or "čtení synchronizace selhalo"
        return _decision(
            ExecutionStatus.FAILED,
            f"Čtení n8n synchronizace pro objednávku {order_id} spadlo: {detail}",
            ["Zopakovat čtení sync logů, až n8n odpoví."],
            f"get_sync_logs pro {order_id} nevrátil data.",
            SOP_ID,
            "Dobrý den, stav zpracování objednávky právě ověřujeme a ozveme se, jakmile budeme vědět víc.",
        )

    if state.ticket_result and state.ticket_result.get("status") == "OK":
        ticket_id = state.ticket_result.get("ticket_id")
        if state.failed_retry_attempts:
            cause = f"retry_sync u {order_id} vrátil výpadek Odoo/WMS. Založil jsem {ticket_id}."
        else:
            error_type = (state.fulfillment_error or {}).get("error_type", "ConnectionTimeoutError")
            cause = (
                f"Objednávka {order_id} je zaplacená a sync je {_sync_status(state)}, "
                f"jenže čtení WMS spadlo na {error_type}. Založil jsem {ticket_id}."
            )
        return _decision(
            ExecutionStatus.ESCALATED,
            cause,
            [
                "L2 obnoví čtení WMS.",
                f"Po obnově znovu načíst expedici {order_id}.",
                "Zákazníkovi neposílat sledování, dokud sklad nepotvrdí zásilku.",
            ],
            f"{ticket_id} je na L2. Zákaznická péče slíbí jen to, že se ozveme.",
            SOP_WMS,
            _warehouse_note(order_id),
        )

    if state.retry_result and state.retry_result.get("status") == "OK":
        fulfillment_id = state.retry_result.get("fulfillment_id")
        return _decision(
            ExecutionStatus.RESOLVED,
            (
                f"Objednávka {order_id} byla zaplacená, ve skladu chyběla a n8n sync padal na {code}. "
                f"Po schváleném retry vznikla expedice {fulfillment_id}."
            ),
            [
                f"Sledovat vychystání {fulfillment_id}.",
                "Když další přenos spadne, založit incident a retry neopakovat.",
            ],
            f"{fulfillment_id} je ve frontě. Zákazníkovi lze napsat, že zboží balíme.",
            SOP_SYNC,
            f"Dobrý den, {title[0].lower() + title[1:]} z objednávky {order_id} už je ve frontě na zabalení. Jakmile zásilka odejde, pošleme vám sledování.",
        )

    if state.failed_retry_attempts and not (state.ticket_result and state.ticket_result.get("status") == "OK"):
        return _decision(
            ExecutionStatus.ESCALATED if state.intent is Intent.EXECUTE else ExecutionStatus.PROPOSED_ACTION,
            f"retry_sync u {order_id} skončil výpadkem infrastruktury, ne úspěšným zápisem do Odoo.",
            ["Založit incident pro L2.", "Další retry_sync nespouštět, dokud WMS neodpoví."],
            f"Retry {order_id} je infrastrukturní chyba. Nepatří do executed_retries.",
            SOP_WMS,
            _warehouse_note(order_id),
        )

    if state.fulfillment_error:
        if "create_ticket" in state.rejected:
            return _decision(
                ExecutionStatus.ESCALATED,
                f"WMS u {order_id} neodpovídá a operátor incident zamítl.",
                ["Předat výpadek L2 ručně.", "retry_sync v tomhle stavu nepomůže."],
                f"WMS u {order_id} je dole, tiket nevznikl.",
                SOP_WMS,
                _warehouse_note(order_id),
            )
        return _decision(
            ExecutionStatus.PROPOSED_ACTION,
            (
                f"Objednávka {order_id} je {_payment_status(state)} a sync je {_sync_status(state)}, "
                "ale WMS po interním retry pořád neodpovídá."
            ),
            ["Po schválení zavolat create_ticket pro L2.", "get_fulfillment neopakovat, dokud sklad neobživne."],
            f"WMS u {order_id} je dole. V režimu návrhu ticket nezakládám.",
            SOP_WMS,
            _warehouse_note(order_id),
        )

    if _payment_status(state) and _payment_status(state) != "PAID" and state.fulfillment:
        return _decision(
            ExecutionStatus.PROPOSED_ACTION,
            (
                f"Objednávka {order_id} ve skladu není ({_fulfillment_label(state)}), "
                f"protože platba je {_payment_status(state)}. Sync je {_sync_status(state)}."
            ),
            [
                "retry_sync nevolat. Nezaplacená objednávka do Odoo skladu nepatří.",
                "Počkat na připsání převodu. Expedice až po PAID.",
                "Když převod nepřijde do tří pracovních dnů, připomenout platební údaje.",
            ],
            f"{order_id} na {((state.order or {}).get('store'))} čeká na převod. Není to chyba n8n.",
            SOP_BANK,
            (
                f"Dobrý den, objednávka {order_id} čeká na připsání platby převodem. "
                "Jakmile peníze dorazí, předáme ji do skladu. Převod obvykle vidíme do tří pracovních dnů."
            ),
        )

    if _sync_status(state) == "FAILED" and state.payment and state.fulfillment:
        if "retry_sync" in state.rejected:
            return _decision(
                ExecutionStatus.PROPOSED_ACTION,
                f"Objednávka {order_id} je zaplacená a sync padá na {code}, ale operátor retry zamítl.",
                [f"Až bude souhlas, zavolat retry_sync(\"{order_id}\") a zkontrolovat FUL-998.", "Do té doby neslibovat datum odeslání."],
                f"Retry {order_id} je zamítnutý. Expedice nevznikla.",
                SOP_SYNC,
                _hold(order_id, title),
            )
        return _decision(
            ExecutionStatus.PROPOSED_ACTION,
            (
                f"Objednávka {order_id} je {_payment_status(state)} a ve stavu "
                f"{(state.order or {}).get('status')}, ve skladu ale není ({_fulfillment_label(state)}). "
                f"n8n flow Shopify → Odoo skončil {code}."
            ),
            [
                f"Po schválení zavolat retry_sync(\"{order_id}\").",
                "Ověřit, že vznikl záznam expedice FUL-998.",
                "Zákazníkovi napsat až po potvrzení ze skladu.",
            ],
            f"Shopify {order_id} je PAID, Odoo expedici nemá. Příčina je {code}.",
            SOP_SYNC,
            _hold(order_id, title),
        )

    if state.hit_step_cap and not _explained(state):
        return _decision(
            ExecutionStatus.ESCALATED,
            state.final_thought or f"Relace {order_id} narazila na limit kroků dřív, než šla složit diagnóza.",
            ["Předat případ operátorovi i se stopou.", "Stejné volání nespouštět znovu."],
            f"Limit kroků u {order_id}. Data na závěr nestačí.",
            SOP_CAP,
            _hold(order_id, title) if state.order else "Dobrý den, vaši objednávku právě dohledáváme a ozveme se, jakmile budeme vědět víc.",
        )

    return _decision(
        ExecutionStatus.ESCALATED,
        (
            f"U objednávky {order_id} nemám bezpečnou automatickou akci. "
            f"Platba {_payment_status(state)}, sync {_sync_status(state)}, sklad {_fulfillment_label(state)}."
        ),
        ["Předat stopu operátorovi.", "Nic dalšího nespouštět naslepo."],
        f"{order_id} nemá jednoznačný další krok.",
        SOP_CAP,
        _hold(order_id, title) if state.order else "Dobrý den, vaši objednávku právě dohledáváme a ozveme se, jakmile budeme vědět víc.",
    )


def _finish(thought: str) -> PlannerAction:
    return PlannerAction(thought=thought, finish=True)


def offline_rule_planner(state: Session) -> PlannerAction:
    """Jen další nástroj. Text závěru píše diagnose()."""
    if state.order_id is None:
        return _finish("V zadání není číslo objednávky.")
    if state.retry_result and state.retry_result.get("status") == "OK":
        return _finish("Retry zapsal expedici. Končím.")
    if state.ticket_result and state.ticket_result.get("status") == "OK":
        return _finish("Incident je založený.")
    if state.failed_retry_attempts:
        if state.intent is Intent.EXECUTE and "create_ticket" not in state.tried:
            return PlannerAction(
                "Retry spadl na infrastruktuře. Eskaluji incidentem, ne dalším syncem.",
                tool_name="create_ticket",
                tool_args={"description": ticket_description(state)},
            )
        return _finish("Retry je infrastrukturní chyba. Další sync nespouštím.")

    if "get_order" not in state.tried:
        return PlannerAction(
            f"Nejdřív Shopify hlavičku {state.order_id}.",
            tool_name="get_order",
            tool_args={"order_id": state.order_id},
        )
    if state.order_error:
        return _finish("Objednávka ve Shopify není. Odoo ani sklad nevolám.")
    if "get_payment" not in state.tried:
        return PlannerAction(
            "Ověřím platbu, než budu řešit sklad.",
            tool_name="get_payment",
            tool_args={"order_id": state.order_id},
        )
    if state.payment_error:
        return _finish("Čtení platby selhalo. Dál nepokračuji.")
    if "get_sync_logs" not in state.tried:
        return PlannerAction(
            "Podívám se na poslední n8n běh Shopify → Odoo.",
            tool_name="get_sync_logs",
            tool_args={"order_id": state.order_id},
        )
    if state.sync_logs_error:
        return _finish("Čtení synchronizace selhalo. Dál nepokračuji.")
    if "get_fulfillment" not in state.tried:
        return PlannerAction(
            "Zjistím, jestli WMS objednávku vidí.",
            tool_name="get_fulfillment",
            tool_args={"order_id": state.order_id},
        )

    if state.fulfillment_error:
        if state.intent is Intent.EXECUTE and "create_ticket" not in state.tried:
            return PlannerAction(
                "WMS spadlo i po vlastním retry. Zakládám incident.",
                tool_name="create_ticket",
                tool_args={"description": ticket_description(state)},
            )
        return _finish("WMS je nedostupné. V režimu návrhu ticket nezakládám.")

    if _payment_status(state) != "PAID":
        return _finish(f"Platba je {_payment_status(state)}. Do skladu to nestrkám.")

    if _sync_status(state) == "FAILED" and "retry_sync" not in state.tried:
        if state.intent is Intent.EXECUTE:
            return PlannerAction(
                f"Zaplacená objednávka visí na {_sync_code(state)}. Jdu přes schválení.",
                tool_name="retry_sync",
                tool_args={"order_id": state.order_id},
            )
        return _finish(f"Příčina je {_sync_code(state)}. Zadání chce návrh, retry nespouštím.")

    return _finish("Další mutaci nemám proč spouštět.")


OfflineRulePlanner = offline_rule_planner


def _parse_decision(text: str) -> FinalLLMDecision | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return FinalLLMDecision.model_validate_json(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return FinalLLMDecision.model_validate_json(raw[start : end + 1])
        except Exception:
            return None


class LiveLLMPlanner:
    """HTTP tool calling. Model nástroj nespustí, vrátí PlannerAction do _dispatch."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        http_post: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http_post = http_post

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LiveLLMPlanner | None:
        source = os.environ if env is None else env
        if source.get("XAI_API_KEY"):
            return cls(source["XAI_API_KEY"], "https://api.x.ai/v1", "grok-3-mini")
        if source.get("OPENAI_API_KEY"):
            return cls(source["OPENAI_API_KEY"], "https://api.openai.com/v1", "gpt-4o-mini")
        if source.get("GEMINI_API_KEY"):
            return cls(
                source["GEMINI_API_KEY"],
                "https://generativelanguage.googleapis.com/v1beta/openai",
                "gemini-2.5-flash",
            )
        return None

    def __call__(self, state: Session) -> PlannerAction:
        try:
            message = self._complete(self._messages(state))["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, RuntimeError) as exc:
            return _finish(f"Odpověď modelu nejde přečíst: {exc}")
        calls = message.get("tool_calls") or []
        thought = message.get("content") or ""
        if not calls:
            # ponytail: no second completion round-trip; unparseable text falls back to diagnose()
            state.llm_decision = _parse_decision(thought)
            return _finish(thought or "Model skončil bez nástroje.")
        # One tool per completion. The next turn sees its result before another call.
        call = calls[0]
        fn = call.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            args = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            args = {}
        return PlannerAction(thought=thought or fn.get("name", ""), tool_name=fn.get("name"), tool_args=args)

    def _messages(self, state: Session) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Režim: {state.intent.value}\n\n{state.request}"},
        ]
        for step in state.trail:
            messages.append(
                {
                    "role": "assistant",
                    "content": step.thought,
                    "tool_calls": [
                        {
                            "id": f"call_{step.step_number}",
                            "type": "function",
                            "function": {
                                "name": step.tool_called,
                                "arguments": json.dumps(step.tool_args, ensure_ascii=False),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{step.step_number}",
                    "content": json.dumps(step.tool_output, ensure_ascii=False),
                }
            )
        return messages

    def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "tools": TOOL_SCHEMAS,
            "messages": messages,
        }
        if self._http_post:
            return self._http_post(payload)
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read().decode()
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                if exc.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"LLM nedostupné: {exc.reason}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM vrátil ne-JSON: {exc}") from exc


def cli_approval(tool_name: str, tool_args: dict[str, Any], reason: str) -> bool:
    print(f"\n[HITL] {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n       důvod: {reason}")
    answer = input("Povolit provedení této akce? (y/n): ").strip().casefold()
    return answer in {"y", "yes", "a", "ano"}


class OpsState:
    """Paměť workeru mezi webhooky. Soubor přežije restart procesu.

    Zápis do Odoo nebo incident se pamatuje jako Shopify GID + UTC den + nástroj.
    Čekající schválení má id, kterým ho Slack nebo n8n umí dokončit.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.log_path = self.directory / "runs.jsonl"
        self._lock = threading.Lock()
        with self._lock:
            self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"writes": {}, "approvals": {}}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        loaded.setdefault("writes", {})
        loaded.setdefault("approvals", {})
        return loaded

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def recall(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._data["writes"].get(key)
            return dict(found) if isinstance(found, dict) else None

    def remember(self, key: str, output: dict[str, Any]) -> None:
        with self._lock:
            self._data["writes"][key] = output
            self._save()

    def open_approval(
        self,
        prompt: str,
        intent: str,
        tool: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> str:
        approval_id = secrets.token_hex(8)
        with self._lock:
            self._data["approvals"][approval_id] = {
                "prompt": prompt,
                "intent": intent,
                "tool": tool,
                "arguments": arguments,
                "reason": reason,
                "response": None,
            }
            self._save()
        return approval_id

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._data["approvals"].get(approval_id)
            return dict(found) if isinstance(found, dict) else None

    def settle(self, approval_id: str, response: dict[str, Any]) -> None:
        with self._lock:
            record = self._data["approvals"].get(approval_id)
            if record is None:
                return
            record["response"] = response
            self._save()

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for approval_id, record in self._data["approvals"].items():
                if record.get("response"):
                    continue
                rows.append(
                    {
                        "approval_id": approval_id,
                        "tool": record.get("tool"),
                        "arguments": record.get("arguments"),
                        "reason": record.get("reason"),
                    }
                )
            return rows

    def append_run(self, body: dict[str, Any], approval_id: str | None = None) -> None:
        report = body.get("report") or {}
        trail = report.get("audit_trail") or []
        line = {
            "final_status": report.get("final_status"),
            "sop_reference": report.get("sop_reference"),
            "order_id": extract_order_id(str(report.get("request") or "")),
            "tools": [step.get("tool_called") for step in trail],
            "idempotent_replay": any(
                isinstance(step.get("tool_output"), dict) and step["tool_output"].get("idempotent_replay")
                for step in trail
            ),
            "approval_id": approval_id,
        }
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    def runs(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.log_path.exists():
                return []
            rows = []
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
            return rows[-50:]
            if line.strip():
                rows.append(json.loads(line))
        return rows[-50:]


class OperationsAgent:
    def __init__(
        self,
        max_steps: int = MAX_ITERATIONS,
        approval_callback: ApprovalCallback | None = None,
        clock: Clock | None = None,
        ops: OpsState | None = None,
    ) -> None:
        self.max_steps = max_steps
        self.approval_callback = approval_callback or cli_approval
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ops = ops
        self.session: Session | None = None

    def require_human_approval(self, tool_name: str, args: dict[str, Any], reason: str) -> bool | None:
        return self.approval_callback(tool_name, args, reason)

    def run(
        self,
        user_prompt: str,
        planner: Callable[[Session], PlannerAction] | None = None,
        intent: IntentChoice = "auto",
        environment: MockSystems | None = None,
    ) -> AgentExecutionReport:
        state = Session(
            request=user_prompt,
            order_id=extract_order_id(user_prompt),
            intent=resolve_intent(user_prompt, intent),
            env=environment or MockSystems(),
        )
        self.session = state
        choose = planner or offline_rule_planner
        seen: set[str] = set()

        for step in range(1, self.max_steps + 1):
            action = choose(state)
            if action.finish or not action.tool_name:
                state.final_thought = action.thought
                break
            outcome = self._dispatch(
                state,
                step,
                action.thought,
                action.tool_name,
                dict(action.tool_args or {}),
                seen,
            )
            state.trail.append(outcome)
            if state.approval_pending:
                state.final_thought = action.thought
                break
        else:
            state.hit_step_cap = True
            state.final_thought = f"Zastavil jsem se po {self.max_steps} krocích, aby se diagnostika nezacyklila."

        decision = self._close(state)
        return AgentExecutionReport.from_decision(user_prompt, decision, state.trail)

    def _close(self, state: Session) -> FinalLLMDecision:
        facts = diagnose(state)
        llm = state.llm_decision
        if not llm or llm.status != facts.status:
            return facts
        care = facts.customer_care_suggestion if _leaks(llm.customer_care_suggestion) else llm.customer_care_suggestion
        return llm.model_copy(
            update={"sop_reference": llm.sop_reference or facts.sop_reference, "customer_care_suggestion": care}
        )

    def _dispatch(
        self,
        state: Session,
        step: int,
        thought: str,
        tool_name: str,
        tool_args: dict[str, Any],
        seen: set[str],
    ) -> AuditStep:
        signature = _signature(tool_name, tool_args)
        if signature in seen:
            return self._blocked(
                step,
                thought,
                tool_name,
                tool_args,
                "DUPLICATE_CALL_BLOCKED",
                "Tento nástroj jsi se stejnými parametry již volal. Použij dříve získaná data nebo zvol jiný nástroj.",
            )
        seen.add(signature)
        if tool_name not in KNOWN_TOOLS:
            return self._blocked(step, thought, tool_name, tool_args, "UNKNOWN_TOOL", f"Neznámý nástroj: {tool_name}.")

        state.tried.add(tool_name)
        policy = self._policy_block(state, tool_name, tool_args)
        if policy is not None:
            code, message = policy
            return self._blocked(step, thought, tool_name, tool_args, code, message)

        approval = ApprovalState.AUTO_APPROVED
        if tool_name in RISKY_TOOLS:
            reason = approval_reason(state, tool_name)
            decision = self.require_human_approval(tool_name, tool_args, reason)
            if decision is None:
                pending = {"tool": tool_name, "args": tool_args, "reason": reason}
                if self.ops is not None:
                    pending["approval_id"] = self.ops.open_approval(
                        state.request,
                        state.intent.value,
                        tool_name,
                        dict(tool_args),
                        reason,
                    )
                state.approval_pending = pending
                return self._step(
                    step,
                    thought,
                    tool_name,
                    tool_args,
                    ApprovalState.AWAITING,
                    {
                        "status": "AWAITING_APPROVAL",
                        "message": "Akce čeká na operátora a nespustila se.",
                        "reason": reason,
                    },
                )
            if not decision:
                state.rejected.append(tool_name)
                return self._step(
                    step,
                    thought,
                    tool_name,
                    tool_args,
                    ApprovalState.HUMAN_REJECTED,
                    {
                        "status": "REJECTED_BY_OPERATOR",
                        "message": f"Operátor zamítl {tool_name}. Akce neproběhla.",
                    },
                )
            approval = ApprovalState.HUMAN_APPROVED

        key = self._write_key(state, tool_name, tool_args) if self.ops and tool_name in RISKY_TOOLS else None
        stored = self.ops.recall(key) if key and self.ops else None
        if stored is not None:
            output = stored
            output["idempotent_replay"] = True
            error = None
        else:
            try:
                output = state.env.call(tool_name, tool_args)
                error = None
                if not isinstance(output, dict):
                    output = {"status": "ERROR", "message": f"Nástroj vrátil {type(output).__name__}."}
                    error = output["message"]
            except Exception as exc:
                output = {
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "retryable": False,
                    "details": str(exc),
                }
                error = str(exc)

        self._count_mutation(state, tool_name, tool_args, output, error)
        if key and self.ops and output.get("status") == "OK" and not output.get("idempotent_replay"):
            self.ops.remember(key, dict(output))
        self._absorb(state, tool_name, output)
        return self._step(step, thought, tool_name, tool_args, approval, output, error)

    def _write_key(self, state: Session, tool_name: str, tool_args: dict[str, Any]) -> str:
        gid = (state.order or {}).get("shopify_order_gid")
        order_id = str(tool_args.get("order_id") or state.order_id or "")
        subject = str(gid or order_id or "unknown")
        moment = self.clock()
        day = moment.date().isoformat() if isinstance(moment, datetime) else "unknown"
        return f"{subject}|{day}|{tool_name}"

    def _count_mutation(
        self,
        state: Session,
        tool_name: str,
        tool_args: dict[str, Any],
        output: dict[str, Any],
        error: str | None,
    ) -> None:
        if tool_name == "retry_sync":
            order_id = str(tool_args.get("order_id") or "")
            if error or output.get("status") != "OK":
                state.failed_retry_attempts.add(order_id)
            else:
                state.executed_retries.add(order_id)
        elif tool_name == "create_ticket" and output.get("status") == "OK":
            state.ticket_done = True

    def _policy_block(self, state: Session, tool_name: str, tool_args: dict[str, Any]) -> tuple[str, str] | None:
        scoped = _order_scope(state, tool_args)
        if scoped is not None:
            return scoped
        if tool_name in RISKY_TOOLS and state.intent is Intent.PROPOSE:
            return (
                "POLICY_PROPOSE_ONLY",
                "Zadání žádá návrh postupu, ne provedení akce. Mutující nástroj nespouštím.",
            )
        if tool_name == "retry_sync":
            order_id = str(tool_args.get("order_id") or "").strip()
            if _retry_already_decided(state, order_id):
                return (
                    "DUPLICATE_CALL_BLOCKED",
                    f"retry_sync pro objednávku {order_id} už operátor rozhodl. Další znění argumentů ho neptá znovu.",
                )
            if order_id in state.executed_retries:
                return ("RETRY_LIMIT", f"retry_sync pro objednávku {order_id} už v téhle relaci úspěšně proběhl.")
            if order_id in state.failed_retry_attempts:
                return (
                    "BUSINESS_RULE",
                    "Předchozí retry_sync skončil výpadkem infrastruktury. Další retry nepouštím, patří sem incident.",
                )
            if state.fulfillment_error:
                return (
                    "BUSINESS_RULE",
                    "WMS je nedostupné. retry_sync by skončil stejnou chybou. Patří sem incident, ne další sync.",
                )
            if state.payment is None:
                return ("BUSINESS_RULE", "Nejdřív musí doběhnout get_payment. Bez stavu platby sync nespouštím.")
            if _payment_status(state) != "PAID":
                return (
                    "BUSINESS_RULE",
                    f"Platba je {_payment_status(state)}. Nezaplacenou objednávku do skladu neposílám.",
                )
        if tool_name == "create_ticket":
            if "create_ticket" in state.rejected:
                return (
                    "DUPLICATE_CALL_BLOCKED",
                    "create_ticket už operátor v téhle relaci zamítl. Další znění argumentů ho neptá znovu.",
                )
            if any(
                step.tool_called == "create_ticket" and step.approval_status == ApprovalState.AWAITING
                for step in state.trail
            ):
                return (
                    "DUPLICATE_CALL_BLOCKED",
                    "create_ticket už čeká na schválení.",
                )
            if state.ticket_done:
                return ("TICKET_LIMIT", "Incident už v téhle relaci vznikl. Druhý ticket nezakládám.")
        return None

    def _absorb(self, state: Session, tool_name: str, output: dict[str, Any]) -> None:
        ok = output.get("status") == "OK"
        if tool_name == "get_order":
            state.order = output.get("data") if ok else None
            state.order_error = None if ok else output
        elif tool_name == "get_payment":
            state.payment = output.get("data") if ok else None
            state.payment_error = None if ok else output
        elif tool_name == "get_fulfillment":
            state.fulfillment = output.get("data") if ok else None
            state.fulfillment_error = None if ok else output
        elif tool_name == "get_sync_logs":
            state.sync_logs = output.get("data") if ok else None
            state.sync_logs_error = None if ok else output
        elif tool_name == "retry_sync":
            state.retry_result = output
            if ok:
                state.fulfillment = {
                    "order_id": output.get("order_id"),
                    "status": "QUEUED_FOR_PACKING",
                    "fulfillment_id": output.get("fulfillment_id"),
                    "tracking_number": None,
                }
        elif tool_name == "create_ticket":
            state.ticket_result = output

    def _blocked(self, step: int, thought: str, tool_name: str, tool_args: dict[str, Any], code: str, message: str) -> AuditStep:
        return self._step(
            step,
            thought,
            tool_name,
            tool_args,
            ApprovalState.BLOCKED_GUARDRAIL,
            {"status": "ERROR", "error": code, "message": message},
            code,
        )

    def _step(
        self,
        step: int,
        thought: str,
        tool_name: str,
        tool_args: dict[str, Any],
        approval: ApprovalState,
        output: dict[str, Any],
        error: str | None = None,
    ) -> AuditStep:
        moment = self.clock()
        stamp = moment.isoformat() if isinstance(moment, datetime) else str(moment)
        return AuditStep(
            step_number=step,
            timestamp=stamp,
            thought=thought,
            tool_called=tool_name,
            tool_args=tool_args,
            tool_output=output,
            approval_status=approval,
            error=error,
        )


def _pack(report: AgentExecutionReport, hook: dict[str, Any] | None) -> dict[str, Any]:
    return {"report": report.model_dump(mode="json"), "approval_webhook_payload": hook}


def _static_failure(summary: str) -> dict[str, Any]:
    decision = FinalLLMDecision(
        status=ExecutionStatus.FAILED,
        root_cause=summary,
        recommended_next_steps=["Ověřit approval_id přes GET /approvals."],
        summary_for_operator=summary,
        sop_reference=SOP_ID,
        customer_care_suggestion=_SAFE_CARE,
    )
    return _pack(AgentExecutionReport.from_decision("", decision, []), None)


def _approval_hook(pending: dict[str, Any]) -> dict[str, Any]:
    hook: dict[str, Any] = {
        "destination": "slack-or-google-chat",
        "channel": "#ops-approvals",
        "text": pending["reason"],
        "proposed_action": {"tool": pending["tool"], "arguments": pending.get("args") or pending.get("arguments")},
        "buttons": ["approve", "reject"],
    }
    approval_id = pending.get("approval_id")
    if approval_id:
        hook["approval_id"] = approval_id
        hook["resume"] = {"approval_id": approval_id, "operator_approved": True}
        hook["reject"] = {"approval_id": approval_id, "operator_approved": False}
    return hook


def handle_n8n_webhook(
    payload: dict[str, Any],
    clock: Clock | None = None,
    ops: OpsState | None = None,
    environment: MockSystems | None = None,
    planner: Callable[[Session], PlannerAction] | None = None,
) -> dict[str, Any]:
    """Vstup z n8n/Make. operator_approved=None nechá akci čekat a vrátí payload pro Slack/Chat.

    S OpsState druhé doručení téhož zápisu Odoo neotevře a approval_id dokončí tu samou akci.
    """
    approval_id = payload.get("approval_id")
    if approval_id:
        return _resume_approval(str(approval_id), payload, clock, ops, environment, planner)

    approved = payload.get("operator_approved", None)

    def gate(_name: str, _args: dict[str, Any], _reason: str) -> bool | None:
        if approved is True:
            return True
        if approved is False:
            return False
        return None

    intent = payload.get("intent") or "auto"
    if intent not in ("auto", "propose", "execute"):
        intent = "auto"
    agent = OperationsAgent(approval_callback=gate, clock=clock or step_clock(), ops=ops)
    report = agent.run(
        str(payload.get("prompt") or ""),
        planner=planner,
        intent=intent,
        environment=environment,
    )
    hook = None
    pending = agent.session.approval_pending if agent.session else None
    if report.final_status is ExecutionStatus.AWAITING_APPROVAL and pending:
        hook = _approval_hook(pending)
    body = _pack(report, hook)
    if ops is not None:
        ops.append_run(body, pending.get("approval_id") if pending else None)
    return body


def _resume_approval(
    approval_id: str,
    payload: dict[str, Any],
    clock: Clock | None,
    ops: OpsState | None,
    environment: MockSystems | None,
    planner: Callable[[Session], PlannerAction] | None,
) -> dict[str, Any]:
    if ops is None:
        return _static_failure("Schválení nejde dohledat, worker nemá stav.")
    record = ops.get_approval(approval_id)
    if record is None:
        return _static_failure("Schválení s tímto id neexistuje.")
    if record.get("response"):
        return record["response"]

    approved = payload.get("operator_approved", None)
    pending = {
        "tool": record["tool"],
        "args": record["arguments"],
        "reason": record["reason"],
        "approval_id": approval_id,
    }
    if approved is None:
        decision = FinalLLMDecision(
            status=ExecutionStatus.AWAITING_APPROVAL,
            root_cause=record["reason"],
            recommended_next_steps=["Schválit nebo zamítnout akci ve Slacku nebo Google Chatu."],
            summary_for_operator=record["reason"],
            sop_reference=SOP_SYNC if record["tool"] == "retry_sync" else SOP_WMS,
            customer_care_suggestion=_SAFE_CARE,
        )
        return _pack(AgentExecutionReport.from_decision(record["prompt"], decision, []), _approval_hook(pending))

    def gate(name: str, args: dict[str, Any], _reason: str) -> bool | None:
        if name == record["tool"] and args == record["arguments"]:
            return bool(approved)
        return None

    agent = OperationsAgent(approval_callback=gate, clock=clock or step_clock(), ops=ops)
    report = agent.run(record["prompt"], planner=planner, intent=record["intent"], environment=environment)
    still = agent.session.approval_pending if agent.session else None
    hook = _approval_hook(still) if report.final_status is ExecutionStatus.AWAITING_APPROVAL and still else None
    body = _pack(report, hook)
    ops.settle(approval_id, body)
    ops.append_run(body, approval_id)
    return body
