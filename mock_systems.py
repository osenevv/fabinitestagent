"""Izolovaná náhrada Shopify, plateb, Odoo/WMS a n8n sync logu.

Každá relace dostane vlastní MockSystems. Dva agenti vedle sebe nesdílí mutace.
"""

from __future__ import annotations

import copy
from typing import Any

WMS_DOWN = "RAISE_TIMEOUT"

TEMPLATE: dict[str, dict[str, Any]] = {
    "100245": {
        "order": {
            "order_id": "100245",
            "shopify_order_gid": "gid://shopify/Order/589100245",
            "store": "fabini.cz",
            "customer": "Jan Novák",
            "status": "PROCESSING",
            "created_at": "2026-10-12T08:30:00Z",
            "items": [
                {
                    "sku": "FAB-LIT-PEK-26",
                    "title": "Litinový pekáč s poklicí 26 cm",
                    "quantity": 1,
                }
            ],
            "tags": ["FABINI_GARANCE"],
        },
        "payment": {
            "order_id": "100245",
            "status": "PAID",
            "method": "CARD",
            "amount_czk": 3490,
        },
        "fulfillment": {
            "order_id": "100245",
            "status": "NOT_FOUND",
            "fulfillment_id": None,
            "tracking_number": None,
        },
        "sync_logs": {
            "order_id": "100245",
            "last_attempt": "2026-10-12T08:30:05Z",
            "status": "FAILED",
            "channel": "n8n",
            "flow": "shopify-order-paid-to-odoo",
            "error_code": "ODOO_ERP_SYNC_TIMEOUT_504",
            "message": (
                "n8n webhook shopify-order-paid-to-odoo vytimeoutoval "
                "při zápisu objednávky 100245 do Odoo ERP."
            ),
        },
    },
    "100246": {
        "order": {
            "order_id": "100246",
            "shopify_order_gid": "gid://shopify/Order/589100246",
            "store": "fabini.sk",
            "customer": "Petra Svobodová",
            "status": "AWAITING_PAYMENT",
            "created_at": "2026-10-13T14:10:00Z",
            "items": [
                {
                    "sku": "FAB-PAN-NEREZ-28",
                    "title": "Nerezová pánev 28 cm",
                    "quantity": 1,
                }
            ],
            "tags": ["FABINI_GARANCE"],
        },
        "payment": {
            "order_id": "100246",
            "status": "UNPAID_AWAITING_BANK_TRANSFER",
            "method": "BANK_TRANSFER",
            "amount_czk": 2190,
        },
        "fulfillment": {
            "order_id": "100246",
            "status": "NOT_FOUND",
            "fulfillment_id": None,
            "tracking_number": None,
        },
        "sync_logs": {
            "order_id": "100246",
            "last_attempt": "2026-10-13T14:10:02Z",
            "status": "SUCCESS",
            "channel": "n8n",
            "flow": "shopify-order-paid-to-odoo",
            "error_code": None,
            "message": "Shopify objednávka je v Odoo. Expedice čeká na platbu převodem.",
        },
    },
    "100500": {
        "order": {
            "order_id": "100500",
            "shopify_order_gid": "gid://shopify/Order/589100500",
            "store": "fabini.cz",
            "customer": "Martin Dvořák",
            "status": "PROCESSING",
            "created_at": "2026-10-14T07:00:00Z",
            "items": [
                {
                    "sku": "FAB-SET-HRNCE",
                    "title": "Sada hrnců FABINI",
                    "quantity": 1,
                }
            ],
            "tags": ["FABINI_GARANCE"],
        },
        "payment": {
            "order_id": "100500",
            "status": "PAID",
            "method": "APPLE_PAY",
            "amount_czk": 6990,
        },
        "fulfillment": WMS_DOWN,
        "sync_logs": {
            "order_id": "100500",
            "last_attempt": "2026-10-14T07:00:04Z",
            "status": "SUCCESS",
            "channel": "n8n",
            "flow": "shopify-order-paid-to-odoo",
            "error_code": None,
            "message": "Odoo objednávku přijalo. Čtení WMS je jiná cesta a teď neodpovídá.",
        },
    },
}


class ConnectionTimeoutError(TimeoutError):
    """Skladové API neodpovědělo dřív, než vypršel limit spojení."""


def _not_found(message: str) -> dict[str, Any]:
    return {"status": "ERROR", "code": 404, "message": message}


class MockSystems:
    """Jeden svět pro jednu relaci. Seed se kopíruje, šablona zůstává."""

    def __init__(
        self,
        seed: dict[str, dict[str, Any]] | None = None,
        retry_fails: set[str] | None = None,
    ) -> None:
        self.db = copy.deepcopy(seed or TEMPLATE)
        self.retry_fails = {str(order_id) for order_id in (retry_fails or set())}

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Hlavička ze Shopify. Čtení, samo o sobě nic nemění."""
        record = self.db.get(str(order_id))
        if not record:
            return _not_found(f"Objednávka {order_id} neexistuje.")
        return {"status": "OK", "data": copy.deepcopy(record["order"])}

    def get_payment(self, order_id: str) -> dict[str, Any]:
        """Stav platby. Bez PAID se objednávka do Odoo/WMS posílat nesmí."""
        record = self.db.get(str(order_id))
        if not record:
            return _not_found(f"Platba pro objednávku {order_id} nenalezena.")
        return {"status": "OK", "data": copy.deepcopy(record["payment"])}

    def get_fulfillment(self, order_id: str) -> dict[str, Any]:
        """Stav expedice ve WMS napojeném na Odoo.

        U objednávky 100500 čtení jednou zopakuje a pak stejně spadne.
        """
        record = self.db.get(str(order_id))
        if not record:
            return _not_found(f"Expedice pro objednávku {order_id} nenalezena.")
        if record["fulfillment"] == WMS_DOWN:
            try:
                return self._wms_read(order_id, 1)
            except ConnectionTimeoutError:
                return self._wms_read(order_id, 2)
        return {"status": "OK", "data": copy.deepcopy(record["fulfillment"])}

    def get_sync_logs(self, order_id: str) -> dict[str, Any]:
        """Poslední běh n8n flow mezi Shopify a Odoo."""
        record = self.db.get(str(order_id))
        if not record:
            return _not_found(f"Logy pro objednávku {order_id} nenalezeny.")
        return {"status": "OK", "data": copy.deepcopy(record["sync_logs"])}

    def retry_sync(self, order_id: str) -> dict[str, Any]:
        """Znovu spustí přenos Shopify → Odoo/WMS. Mění stav, patří za schválení."""
        record = self.db.get(str(order_id))
        if not record:
            return _not_found(f"Objednávka {order_id} nenalezena.")
        if str(order_id) in self.retry_fails or record["fulfillment"] == WMS_DOWN:
            return {
                "status": "ERROR",
                "code": 503,
                "retryable": False,
                "error_type": "OdooWMSUnavailable",
                "message": (
                    f"Odoo/WMS objednávku {order_id} teď nepřijme. "
                    "Skladové API je nedostupné."
                ),
            }
        fulfillment_id = "FUL-998" if str(order_id) == "100245" else f"FUL-{order_id}"
        record["sync_logs"]["status"] = "SUCCESS"
        record["sync_logs"]["error_code"] = None
        record["sync_logs"]["message"] = f"n8n retry zapsal expedici {fulfillment_id} do Odoo."
        record["fulfillment"] = {
            "order_id": str(order_id),
            "status": "QUEUED_FOR_PACKING",
            "fulfillment_id": fulfillment_id,
            "tracking_number": None,
        }
        return {
            "status": "OK",
            "order_id": str(order_id),
            "fulfillment_id": fulfillment_id,
            "message": (
                f"Synchronizace objednávky {order_id} proběhla. "
                f"Expedice {fulfillment_id} je ve frontě na balení."
            ),
        }

    def create_ticket(self, description: str) -> dict[str, Any]:
        """Incident pro L2. Ven z firmy, proto jen po schválení."""
        return {
            "status": "OK",
            "ticket_id": "INC-2026-0891",
            "message": "Incident je založený a přiřazený L2 Operations.",
            "description": description,
        }

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, name)
        return fn(**args)

    @staticmethod
    def _wms_read(order_id: str, attempt: int) -> dict[str, Any]:
        raise ConnectionTimeoutError(
            "WMS_API_UNAVAILABLE: HTTP 503 Service Unavailable "
            f"(pokus {attempt}/2, order {order_id}). Connection pool exhausted."
        )


READ_ONLY_TOOLS = frozenset({"get_order", "get_payment", "get_fulfillment", "get_sync_logs"})
RISKY_TOOLS = frozenset({"retry_sync", "create_ticket"})
KNOWN_TOOLS = READ_ONLY_TOOLS | RISKY_TOOLS
