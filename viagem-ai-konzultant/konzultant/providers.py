"""Poskytovatelé LLM. Každý implementuje `complete(messages, schema) -> str`.

Výchozí je lokální Claude Code CLI (`claude -p`) — bez API klíče v repu,
nejlepší dostupný model (`opus`). HTTP poskytovatelé zůstávají pro
reprodukci u interviewera (OpenAI / xAI / Gemini přes OpenAI-kompatibilní
endpoint, Anthropic Messages API).

Strukturovaný výstup: schéma se předává nativně (OpenAI `json_schema`
strict, Anthropic vynucený tool call, Claude Code `--json-schema`).
Nativní režim je pojistka tvaru; obsahová pravidla stejně ověřuje
`konzultant.schema` + `konzultant.policy`. Když endpoint `json_schema`
odmítne (HTTP 400), spadne poskytovatel jednou na `json_object` a zapíše
to do `notes` — nikdy potichu.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable

from .domain import HTTP_PROVIDER_KEYS

HTTP_TIMEOUT = 180
CLI_TIMEOUT = 600

# Klíčová slova, která strict režimy poskytovatelů často odmítají. Lokální
# validátor je vynucuje i tak, takže je z kopie pro poskytovatele vypustíme.
_PROVIDER_STRIP = {"$schema", "$id", "title", "minimum", "maximum", "minItems", "maxItems", "minLength"}


class ProviderError(Exception):
    """Volání modelu selhalo (síť, HTTP, chybová odpověď CLI, prázdný výstup)."""


def provider_schema(schema: dict[str, Any]) -> dict[str, Any]:
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _PROVIDER_STRIP}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(copy.deepcopy(schema))


def flatten_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
    """(system, user) pro rozhraní bez multi-turn (CLI). Opakovaný pokus
    s předchozí odpovědí a chybami se složí do jedné user zprávy."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    parts: list[str] = []
    for m in messages:
        if m["role"] == "user":
            parts.append(m["content"])
        elif m["role"] == "assistant":
            parts.append(f"<predchozi_odpoved>\n{m['content']}\n</predchozi_odpoved>")
    return system, "\n\n".join(parts)


UrlOpen = Callable[..., Any]


def message_content_text(content: Any) -> str:
    """OpenAI-kompatibilní `message.content` může být str nebo seznam partů."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], urlopen: UrlOpen) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise ProviderError(f"odpověď {url} není JSON: {raw[:300]!r}") from e
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:2000]
        raise HttpStatusError(e.code, detail, url=url) from e
    except urllib.error.URLError as e:
        raise ProviderError(f"síťová chyba {url}: {e.reason}") from e
    except TimeoutError as e:
        raise ProviderError(f"timeout {HTTP_TIMEOUT}s: {url}") from e


class HttpStatusError(ProviderError):
    def __init__(self, code: int, detail: str, url: str = "") -> None:
        where = f" ({url})" if url else ""
        hint = ""
        if code in (401, 403):
            hint = " — klíč je nastavený, ale API ho odmítlo (neplatný/expirovaný klíč nebo oprávnění)"
        elif code == 429:
            hint = " — rate limit / kvóta; zkus později nebo jiný model"
        elif code >= 500:
            hint = " — výpadek služby poskytovatele"
        super().__init__(f"HTTP {code}{where}{hint}: {detail}")
        self.code = code
        self.detail = detail
        self.url = url


class Provider:
    name = "base"
    model = ""

    def __init__(self) -> None:
        self.notes: list[str] = []

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        raise NotImplementedError


class ClaudeCodeProvider(Provider):
    name = "claude-code"

    def __init__(self, model: str = "opus", binary: str = "claude", runner: Callable[..., Any] = subprocess.run) -> None:
        super().__init__()
        self.model = model
        self.binary = binary
        self._run = runner

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        system, user = flatten_messages(messages)
        cmd = [
            self.binary, "-p", user,
            "--system-prompt", system,
            "--model", self.model,
            "--tools", "",
            "--output-format", "json",
            "--json-schema", json.dumps(provider_schema(schema), ensure_ascii=False),
        ]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT, check=False)
        except FileNotFoundError as e:
            raise ProviderError(f"nenalezen příkaz {self.binary!r} (Claude Code CLI)") from e
        except subprocess.TimeoutExpired as e:
            raise ProviderError(f"Claude Code timeout {CLI_TIMEOUT}s") from e
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ProviderError(
                f"Claude Code nevrátil JSON (exit {proc.returncode}): {proc.stdout[:500]} {proc.stderr[:500]}"
            ) from e
        # Pozor: CLI hlásí `subtype: success` i při chybě účtu („Credit balance
        # is too low“, „Not logged in“) — rozhoduje `is_error`.
        if envelope.get("is_error") or proc.returncode != 0:
            raise ProviderError(f"Claude Code chyba: {envelope.get('result') or proc.stderr[:500]}")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured, ensure_ascii=False)
        result = envelope.get("result")
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        if not isinstance(result, str) or not result.strip():
            raise ProviderError("Claude Code vrátil prázdný výsledek")
        return result


class OpenAICompatProvider(Provider):
    def __init__(self, name: str, base_url: str, api_key: str, model: str, urlopen: UrlOpen = urllib.request.urlopen) -> None:
        super().__init__()
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.structured = True
        self._urlopen = urlopen

    def _body(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        if self.structured:
            fmt: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": "vyhodnoceni_parcely", "strict": True, "schema": provider_schema(schema)},
            }
        else:
            fmt = {"type": "json_object"}
        return {"model": self.model, "messages": messages, "temperature": 0.1, "response_format": fmt}

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        url = self.base_url + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            payload = _post_json(url, self._body(messages, schema), headers, self._urlopen)
        except HttpStatusError as e:
            if e.code != 400 or not self.structured:
                raise
            self.structured = False
            self.notes.append(f"{self.name}: json_schema odmítnuto (HTTP 400), fallback na json_object")
            payload = _post_json(url, self._body(messages, schema), headers, self._urlopen)
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"neočekávaný tvar odpovědi {self.name}: {str(payload)[:500]}") from e
        refusal = message.get("refusal") if isinstance(message, dict) else None
        if refusal:
            raise ProviderError(f"{self.name} odmítl požadavek (refusal): {refusal}")
        content = message_content_text(message.get("content") if isinstance(message, dict) else None)
        if not content.strip():
            raise ProviderError(f"{self.name} vrátil prázdný obsah: {str(payload)[:500]}")
        return content


class AnthropicProvider(Provider):
    name = "anthropic"
    TOOL = "vyhodnoceni_parcely"

    def __init__(self, api_key: str, model: str, urlopen: UrlOpen = urllib.request.urlopen) -> None:
        super().__init__()
        self.api_key = api_key
        self.model = model
        self._urlopen = urlopen

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        body = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0.1,
            "system": system,
            "messages": convo,
            "tools": [{
                "name": self.TOOL,
                "description": "Odevzdej strukturované vyhodnocení parcely.",
                "input_schema": provider_schema(schema),
            }],
            "tool_choice": {"type": "tool", "name": self.TOOL},
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        payload = _post_json("https://api.anthropic.com/v1/messages", body, headers, self._urlopen)
        for block in payload.get("content") or []:
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return json.dumps(block["input"], ensure_ascii=False)
        text = "".join(b.get("text", "") for b in payload.get("content") or [] if b.get("type") == "text")
        if not text.strip():
            raise ProviderError(f"Anthropic nevrátil tool_use ani text: {str(payload)[:500]}")
        return text


# Výchozí modely = nejsilnější obecně dostupné; vše přepisovatelné env proměnnou.
DEFAULT_MODELS = {
    "claude-code": "opus",
    "openai": "gpt-4.1",
    "xai": "grok-4",
    "gemini": "gemini-2.5-pro",
    "anthropic": "claude-opus-4-1",
}
PROVIDERS = tuple(DEFAULT_MODELS)


def select_provider(name: str | None = None, model: str | None = None) -> Provider:
    """`name` z CLI > env VIAGEM_PROVIDER > auto (Claude Code, pak první API klíč)."""
    name = name or os.environ.get("VIAGEM_PROVIDER") or None
    env = os.environ
    if name is None:
        if shutil.which("claude"):
            name = "claude-code"
        else:
            for cand, key in HTTP_PROVIDER_KEYS:
                if env.get(key):
                    name = cand
                    break
    if name is None:
        keys = " / ".join(k for _, k in HTTP_PROVIDER_KEYS)
        raise ProviderError(
            f"Není dostupný žádný model: nainstaluj Claude Code (`claude`) nebo nastav {keys}."
        )
    if name not in DEFAULT_MODELS:
        raise ProviderError(f"neznámý poskytovatel {name!r}; volby: {', '.join(PROVIDERS)}")

    def pick(env_var: str) -> str:
        return model or env.get(env_var) or DEFAULT_MODELS[name]

    def key(var: str) -> str:
        val = (env.get(var) or "").strip()
        if not val:
            raise ProviderError(
                f"poskytovatel {name!r} vyžaduje neprázdný {var}. "
                f"Bez klíče nelze volat API — nastav env nebo použij --offline / --run-all-tests."
            )
        return val

    if name == "claude-code":
        return ClaudeCodeProvider(model=pick("CLAUDE_CODE_MODEL"))
    if name == "openai":
        return OpenAICompatProvider("openai", env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                                    key("OPENAI_API_KEY"), pick("OPENAI_MODEL"))
    if name == "xai":
        return OpenAICompatProvider("xai", env.get("XAI_BASE_URL", "https://api.x.ai/v1"),
                                    key("XAI_API_KEY"), pick("XAI_MODEL"))
    if name == "gemini":
        return OpenAICompatProvider(
            "gemini",
            env.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
            key("GEMINI_API_KEY"), pick("GEMINI_MODEL"),
        )
    return AnthropicProvider(key("ANTHROPIC_API_KEY"), pick("ANTHROPIC_MODEL"))
