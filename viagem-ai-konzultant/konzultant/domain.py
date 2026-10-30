"""Doménové konstanty výkupu: osy, enumy, srážky, prahy bran."""

from __future__ import annotations

CORE_AXES: tuple[str, ...] = (
    "zaplava",
    "ochrana_prirody",
    "uzemni_plan",
    "pristup",
    "pravni_stav",
)
PLANNING_AXES: tuple[str, ...] = ("zaplava", "ochrana_prirody", "uzemni_plan")

UROVEN: tuple[str, ...] = ("zadne", "nizke", "stredni", "vysoke", "kriticke", "neznamo")
UROVEN_ORDER: tuple[str, ...] = ("zadne", "nizke", "stredni", "vysoke", "kriticke")

SAFE_FOR_VYKUPIT: frozenset[str] = frozenset({"zadne", "nizke", "stredni"})
BLOCKING_DALSI: frozenset[str] = frozenset({"vysoke", "kriticke"})

DOPORUCENI: tuple[str, ...] = ("vykupit", "zkoumat_dal", "odlozit", "odmitnout")
POTENCIAL: tuple[str, ...] = (
    "stavebni",
    "zemedelsky",
    "lesni",
    "rekreacni",
    "infrastrukturni",
    "nejisty",
    "nevyuzitelny",
)

PLANNING_PENALTY: dict[str, int] = {
    "zadne": 0,
    "nizke": 3,
    "stredni": 8,
    "vysoke": 15,
    "neznamo": 6,
    "kriticke": 30,
}
DALSI_PENALTY: dict[str, int] = {
    "zadne": 0,
    "nizke": 1,
    "stredni": 3,
    "vysoke": 6,
    "neznamo": 2,
    "kriticke": 30,
}
AXIS_POINTS: dict[str, int] = {
    "zadne": 20,
    "nizke": 16,
    "stredni": 10,
    "vysoke": 5,
    "neznamo": 8,
    "kriticke": 0,
}

CRITICAL_SCORE_CAP = 35
VYKUPIT_MIN_SKORE = 70
VYKUPIT_MIN_JISTOTA = 60

HTTP_PROVIDER_KEYS: tuple[tuple[str, str], ...] = (
    ("openai", "OPENAI_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
)
