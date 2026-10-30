"""Mockované HTTP testy live cesty: prompt → fake model → schema → policy → checks.

Bez reálného API klíče dokazují, že OpenAI-kompatibilní / Anthropic provider
+ pipeline fungují end-to-end, včetně bran, fence JSON a fallbacku json_object.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock

from konzultant import pipeline, providers
from konzultant.checks import run_checks
from konzultant.harness import load_case_bundle
from konzultant.inputs import load_parcel, load_schema
from konzultant.providers import (
    AnthropicProvider,
    HttpStatusError,
    OpenAICompatProvider,
    ProviderError,
    select_provider,
)

ROOT = Path(__file__).resolve().parent.parent


def _load_case(case_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    inp, expected, actual = load_case_bundle(case_id)
    return load_parcel(str(inp)), expected, actual


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any] | str, status: int = 200) -> None:
        if isinstance(payload, dict):
            self._body = json.dumps(payload).encode("utf-8")
        else:
            self._body = payload.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _openai_payload(content: Any) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _anthropic_tool_payload(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "vyhodnoceni_parcely", "input": obj}
        ]
    }


class TestOpenAICompatHTTP(unittest.TestCase):
    def test_auth_header_and_full_pipeline_01(self) -> None:
        parcel, expected, model_out = _load_case("01-stavebni-happy")
        seen: dict[str, Any] = {}

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            seen["url"] = req.full_url
            seen["auth"] = req.headers.get("Authorization") or req.get_header("Authorization")
            # urllib may title-case headers
            if seen["auth"] is None:
                seen["auth"] = req.headers.get("authorization")
            body = json.loads(req.data.decode("utf-8"))
            seen["body"] = body
            self.assertEqual(body["response_format"]["type"], "json_schema")
            return _FakeHTTPResponse(_openai_payload(json.dumps(model_out, ensure_ascii=False)))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test", "gpt-test", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertTrue(str(seen["url"]).endswith("/chat/completions"))
        self.assertEqual(seen["auth"], "Bearer sk-test")
        self.assertIn("system", [m["role"] for m in seen["body"]["messages"]])
        errs = run_checks(expected["kontroly"], result.data)
        self.assertEqual(errs, [], errs)
        self.assertEqual(result.data["doporuceni"], "vykupit")

    def test_pipeline_02_zaplava(self) -> None:
        parcel, expected, model_out = _load_case("02-zaplava")

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(_openai_payload(json.dumps(model_out, ensure_ascii=False)))

        prov = OpenAICompatProvider("xai", "https://api.x.ai/v1", "xai-key", "grok-test", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)
        self.assertEqual(result.data["doporuceni"], "odmitnout")

    def test_pipeline_08_exekuce(self) -> None:
        parcel, expected, model_out = _load_case("08-exekuce")

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(_openai_payload(json.dumps(model_out, ensure_ascii=False)))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk", "m", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)
        self.assertIn(result.data["doporuceni"], ("odlozit", "odmitnout"))

    def test_pipeline_11_injection(self) -> None:
        parcel, expected, model_out = _load_case("11-prompt-injection")

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(_openai_payload(json.dumps(model_out, ensure_ascii=False)))

        prov = OpenAICompatProvider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                                    "gem-key", "gemini-test", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)
        self.assertTrue(result.data["konflikty_dat"])
        self.assertNotEqual(result.data["doporuceni"], "vykupit")

    def test_illegal_vykupit_rewritten_by_gates(self) -> None:
        """Model vrátí vykupit při kritické exekuci → politika přepíše na odlozit."""
        parcel, expected, model_out = _load_case("08-exekuce")
        bad = json.loads(json.dumps(model_out))
        bad["doporuceni"] = "vykupit"
        bad["skore_zajmu"] = 90
        bad["skore_slozky"]["pravni_stav"] = 20
        bad["jistota"]["skore"] = 80

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(_openai_payload(json.dumps(bad, ensure_ascii=False)))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk", "m", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertNotEqual(result.data["doporuceni"], "vykupit")
        self.assertEqual(result.data["doporuceni"], "odlozit")
        self.assertLessEqual(result.data["skore_zajmu"], 35)
        self.assertTrue(result.politika)
        self.assertTrue(any("vykupit → odlozit" in p for p in result.politika))
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)

    def test_markdown_fence_and_float_coercion(self) -> None:
        parcel, expected, model_out = _load_case("01-stavebni-happy")
        coerced = json.loads(json.dumps(model_out))
        coerced["skore_zajmu"] = float(coerced["skore_zajmu"])
        coerced["skore_slozky"]["vyuzitelnost"] = float(coerced["skore_slozky"]["vyuzitelnost"])
        fenced = "Here you go:\n```JSON\n" + json.dumps(coerced, ensure_ascii=False) + "\n```\n"

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(_openai_payload(fenced))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk", "m", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertIsInstance(result.data["skore_zajmu"], int)
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)

    def test_content_parts_array(self) -> None:
        parcel, expected, model_out = _load_case("01-stavebni-happy")
        parts = [{"type": "text", "text": json.dumps(model_out, ensure_ascii=False)}]

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(_openai_payload(parts))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk", "m", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)

    def test_json_schema_400_falls_back_to_json_object(self) -> None:
        parcel, _, model_out = _load_case("01-stavebni-happy")
        calls: list[dict[str, Any]] = []

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body["response_format"])
            if body["response_format"]["type"] == "json_schema":
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request",
                    hdrs=mock.MagicMock(), fp=io.BytesIO(b'{"error":"schema"}'),
                )
            return _FakeHTTPResponse(_openai_payload(json.dumps(model_out, ensure_ascii=False)))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk", "m", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(result.data["doporuceni"], "vykupit")
        self.assertEqual(calls[0]["type"], "json_schema")
        self.assertEqual(calls[1]["type"], "json_object")
        self.assertTrue(any("json_object" in n for n in prov.notes))

    def test_http_401_mentions_key_rejected(self) -> None:
        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized",
                hdrs=mock.MagicMock(), fp=io.BytesIO(b'{"error":"invalid_api_key"}'),
            )

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-bad", "m", urlopen=urlopen)
        with self.assertRaises(HttpStatusError) as ctx:
            prov.complete([{"role": "user", "content": "x"}], load_schema())
        self.assertIn("odmítlo", str(ctx.exception))
        self.assertEqual(ctx.exception.code, 401)

    def test_repair_loop_on_enum_drift(self) -> None:
        parcel, expected, model_out = _load_case("01-stavebni-happy")
        bad = json.loads(json.dumps(model_out))
        bad["doporuceni"] = "buy_now"  # enum drift
        good = json.dumps(model_out, ensure_ascii=False)
        n = {"i": 0}

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            n["i"] += 1
            if n["i"] == 1:
                return _FakeHTTPResponse(_openai_payload(json.dumps(bad)))
            return _FakeHTTPResponse(_openai_payload(good))

        prov = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk", "m", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)


class TestAnthropicHTTP(unittest.TestCase):
    def test_anthropic_tool_use_headers_and_pipeline(self) -> None:
        parcel, expected, model_out = _load_case("02-zaplava")
        seen: dict[str, Any] = {}

        def urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
            seen["url"] = req.full_url
            seen["x_api_key"] = req.get_header("X-api-key") or req.headers.get("x-api-key")
            seen["version"] = req.get_header("Anthropic-version") or req.headers.get("anthropic-version")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["tool_choice"]["name"], "vyhodnoceni_parcely")
            return _FakeHTTPResponse(_anthropic_tool_payload(model_out))

        prov = AnthropicProvider("ant-key", "claude-test", urlopen=urlopen)
        result = pipeline.evaluate(parcel, prov)
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["x_api_key"], "ant-key")
        self.assertEqual(seen["version"], "2023-06-01")
        self.assertEqual(errs := run_checks(expected["kontroly"], result.data), [], errs)


class TestSelectProviderErrors(unittest.TestCase):
    def test_missing_key_clear_message(self) -> None:
        env = {"PATH": "/usr/bin"}  # no API keys, assume no reliance on claude in PATH for this name
        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("konzultant.providers.shutil.which", return_value=None):
                with self.assertRaises(ProviderError) as ctx:
                    select_provider("openai")
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))
        self.assertIn("Bez klíče", str(ctx.exception))

    def test_empty_key_rejected(self) -> None:
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "  "}, clear=False):
            with self.assertRaises(ProviderError) as ctx:
                select_provider("openai")
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_auto_no_provider_message(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("konzultant.providers.shutil.which", return_value=None):
                with self.assertRaises(ProviderError) as ctx:
                    select_provider(None)
        msg = str(ctx.exception)
        self.assertIn("OPENAI_API_KEY", msg)
        self.assertIn("Claude Code", msg)


class TestExtractAndCoerce(unittest.TestCase):
    def test_extract_uppercase_fence(self) -> None:
        raw = "```JSON\n{\"a\": 1}\n```"
        self.assertEqual(pipeline.extract_json(raw), {"a": 1})

    def test_coerce_float_ints(self) -> None:
        obj = {"skore": 88.0, "nested": {"x": 1.0}, "keep": 1.5, "flag": True}
        out = pipeline.coerce_json_types(obj)
        self.assertEqual(out["skore"], 88)
        self.assertIsInstance(out["skore"], int)
        self.assertEqual(out["nested"]["x"], 1)
        self.assertEqual(out["keep"], 1.5)
        self.assertIs(out["flag"], True)


if __name__ == "__main__":
    unittest.main()
