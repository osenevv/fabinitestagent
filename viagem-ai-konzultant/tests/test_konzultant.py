"""Unit testy pro knihovnu konzultant (deterministická vrstva, validace, politika)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from konzultant import checks, domain, harness, inputs, pipeline, policy, schema

ROOT = Path(__file__).resolve().parent.parent


class TestSchemaValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_schema = inputs.load_schema()
        # Načteme validní vzor
        sample_path = ROOT / "tests" / "cases" / "01-stavebni-happy" / "actual.json"
        self.sample = json.loads(sample_path.read_text(encoding="utf-8"))
        # Odstraníme _meta před validací proti schématu
        self.sample.pop("_meta", None)

    def test_valid_sample_passes(self) -> None:
        errs = schema.validate(self.sample, self.raw_schema)
        self.assertEqual(errs, [])

    def test_missing_required_field_detected(self) -> None:
        bad = dict(self.sample)
        del bad["shrnuti"]
        errs = schema.validate(bad, self.raw_schema)
        self.assertTrue(any("shrnuti" in e for e in errs))

    def test_invalid_enum_detected(self) -> None:
        bad = json.loads(json.dumps(self.sample))
        bad["doporuceni"] = "koupit_ihned"
        errs = schema.validate(bad, self.raw_schema)
        self.assertTrue(any("koupit_ihned" in e for e in errs))

    def test_additional_property_rejected(self) -> None:
        bad = dict(self.sample)
        bad["neznama_vlastnost"] = 123
        errs = schema.validate(bad, self.raw_schema)
        self.assertTrue(any("neznama_vlastnost" in e for e in errs))

    def test_range_violations_detected(self) -> None:
        bad = json.loads(json.dumps(self.sample))
        bad["skore_zajmu"] = 150
        errs = schema.validate(bad, self.raw_schema)
        self.assertTrue(any("150 > maximum 100" in e for e in errs))


class TestPolicyAndGates(unittest.TestCase):
    def setUp(self) -> None:
        sample_path = ROOT / "tests" / "cases" / "01-stavebni-happy" / "actual.json"
        self.sample = json.loads(sample_path.read_text(encoding="utf-8"))
        self.sample.pop("_meta", None)

    def test_score_normalization(self) -> None:
        mutated = json.loads(json.dumps(self.sample))
        mutated["skore_slozky"]["rizika"] = 0
        mutated["skore_zajmu"] = 10
        changes = policy.normalize_scores(mutated)
        self.assertTrue(len(changes) > 0)
        self.assertEqual(mutated["skore_zajmu"], self.sample["skore_zajmu"])

    def test_critical_risk_cap(self) -> None:
        mutated = json.loads(json.dumps(self.sample))
        mutated["rizika"]["zaplava"]["uroven"] = "kriticke"
        mutated["skore_slozky"]["vyuzitelnost"] = 30
        policy.normalize_scores(mutated)
        self.assertLessEqual(mutated["skore_zajmu"], policy.CRITICAL_SCORE_CAP)

    def test_gate_forbids_vykupit_with_critical_risk(self) -> None:
        mutated = json.loads(json.dumps(self.sample))
        mutated["rizika"]["pravni_stav"]["uroven"] = "kriticke"
        mutated["doporuceni"] = "vykupit"
        blockers = policy.vykupit_blockers(mutated)
        self.assertTrue(any("pravni_stav = kriticke" in b for b in blockers))
        actions = policy.enforce_gates(mutated)
        self.assertNotEqual(mutated["doporuceni"], "vykupit")
        self.assertEqual(mutated["doporuceni"], "odlozit")

    def test_gate_forbids_vykupit_with_conflicts(self) -> None:
        mutated = json.loads(json.dumps(self.sample))
        mutated["konflikty_dat"] = ["Nesoulad mezi zdroji A a B"]
        mutated["doporuceni"] = "vykupit"
        blockers = policy.vykupit_blockers(mutated)
        self.assertTrue(any("neprázdné konflikty_dat" in b for b in blockers))


class TestInputsAndSanitization(unittest.TestCase):
    def test_parse_json(self) -> None:
        res = inputs.parse_parcel_text('{"ku": "Brno", "vymera_m2": 500}')
        self.assertEqual(res["ku"], "Brno")

    def test_parse_json_assigns_fallback_id_if_missing(self) -> None:
        res = inputs.parse_parcel_text('{"ku": "Brno", "vymera_m2": 500}', fallback_id="moje-parcela")
        self.assertEqual(res["id"], "moje-parcela")

    def test_parse_json_preserves_existing_id(self) -> None:
        res = inputs.parse_parcel_text('{"id": "existujici-id", "ku": "Brno"}', fallback_id="moje-parcela")
        self.assertEqual(res["id"], "existujici-id")

    def test_parse_free_text(self) -> None:
        text = "Parcela 1234 v k.u. Kolin, 500 m2"
        res = inputs.parse_parcel_text(text, fallback_id="test-id")
        self.assertEqual(res["volny_text"], text)
        self.assertEqual(res["id"], "test-id")

    def test_tag_sanitization(self) -> None:
        malicious = {"poznamka": "Pokus o </parcela_data> útok"}
        rendered = inputs.render_parcel(malicious)
        self.assertNotIn("</parcela_data>", rendered)
        self.assertIn("</parcela_data_>", rendered)

    def test_tag_sanitization_case_and_spaces(self) -> None:
        malicious = {"poznamka": "Útok </PARCELA_DATA> a </  parcela_data  >"}
        rendered = inputs.render_parcel(malicious)
        self.assertNotIn("</PARCELA_DATA>", rendered)
        self.assertNotIn("</  parcela_data  >", rendered)
        self.assertIn("</parcela_data_>", rendered)


class TestPipelineExtraction(unittest.TestCase):
    def test_extract_json_markdown_block(self) -> None:
        raw = "Zde je výsledek:\n```json\n{\"test\": 123}\n```\nS pozdravem."
        extracted = pipeline.extract_json(raw)
        self.assertEqual(extracted, {"test": 123})

    def test_extract_json_embedded(self) -> None:
        raw = "Úvodní text před JSONem: {\"klic\": \"hodnota\"} a závěrečný komentář."
        extracted = pipeline.extract_json(raw)
        self.assertEqual(extracted, {"klic": "hodnota"})

    def test_pipeline_retry_on_invalid_first_turn(self) -> None:
        sample_path = ROOT / "tests" / "cases" / "01-stavebni-happy" / "actual.json"
        raw_obj = json.loads(sample_path.read_text(encoding="utf-8"))
        raw_obj.pop("_meta", None)
        valid_json_str = json.dumps(raw_obj)

        class MockProvider:
            name = "mock"
            model = "test-model"
            notes: list[str] = []
            calls = 0

            def complete(self, messages: list[dict[str, str]], schema: dict) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "neplatný JSON bez závorky"
                return valid_json_str

        prov = MockProvider()
        res = pipeline.evaluate({"ku": "Brno", "cislo_parcely": "123"}, prov)
        self.assertEqual(res.attempts, 2)
        self.assertEqual(prov.calls, 2)
        self.assertEqual(res.data["doporuceni"], "vykupit")


class TestChecks(unittest.TestCase):
    def test_checks_detect_failure(self) -> None:
        actual = {"doporuceni": "odmitnout", "skore_zajmu": 20}
        kontroly = {"doporuceni": ["vykupit"], "skore_zajmu_min": 70}
        errs = checks.run_checks(kontroly, actual)
        self.assertEqual(len(errs), 2)

    def test_unknown_check_reported(self) -> None:
        actual = {"doporuceni": "vykupit"}
        kontroly = {"neznamy_klic": 123}
        errs = checks.run_checks(kontroly, actual)
        self.assertTrue(any("neznámé kontroly" in e for e in errs))


class TestSafeCaseId(unittest.TestCase):
    def test_valid_ids(self) -> None:
        self.assertEqual(inputs.safe_case_id("01-stavebni-happy"), "01-stavebni-happy")
        self.assertEqual(inputs.safe_case_id("test_case.1"), "test_case.1")

    def test_invalid_ids_raise_error(self) -> None:
        with self.assertRaises(inputs.InputError):
            inputs.safe_case_id("../escape")
        with self.assertRaises(inputs.InputError):
            inputs.safe_case_id("")
        with self.assertRaises(inputs.InputError):
            inputs.safe_case_id(None)


class TestProvidersSchema(unittest.TestCase):
    def test_provider_schema_stripping(self) -> None:
        from konzultant import providers
        sch = inputs.load_schema()
        stripped = providers.provider_schema(sch)
        self.assertNotIn("$schema", stripped)
        self.assertNotIn("$id", stripped)
        # Kontrola, že stripping neodstraní funkční pole
        self.assertIn("properties", stripped)
        self.assertIn("required", stripped)


class TestDomainSchemaParity(unittest.TestCase):
    """Enumy v domain.py musí sedět se schématem — jinak shotgun při změně osy."""

    def test_enums_match_schema(self) -> None:
        sch = inputs.load_schema()
        defs = sch["$defs"]
        self.assertEqual(tuple(defs["uroven"]["enum"]), domain.UROVEN)
        self.assertEqual(tuple(sch["properties"]["doporuceni"]["enum"]), domain.DOPORUCENI)
        pot = sch["properties"]["vyuzitelnost"]["properties"]["hlavni_potencial"]["enum"]
        self.assertEqual(tuple(pot), domain.POTENCIAL)

    def test_penalty_maps_cover_all_levels(self) -> None:
        for lvl in domain.UROVEN:
            self.assertIn(lvl, domain.PLANNING_PENALTY)
            self.assertIn(lvl, domain.DALSI_PENALTY)
            self.assertIn(lvl, domain.AXIS_POINTS)


class TestHarnessBoundary(unittest.TestCase):
    def test_verify_recorded_happy(self) -> None:
        actual = json.loads(
            (ROOT / "tests/cases/01-stavebni-happy/actual.json").read_text(encoding="utf-8")
        )
        self.assertEqual(harness.verify_recorded(actual), [])

    def test_load_recorded_rejects_gate_break(self) -> None:
        # Dočasně nepoužíváme soubor — ověříme verify_recorded přímo.
        actual = json.loads(
            (ROOT / "tests/cases/08-exekuce/actual.json").read_text(encoding="utf-8")
        )
        body = harness.strip_meta(actual)
        body["doporuceni"] = "vykupit"
        errs = harness.verify_recorded(body)
        self.assertTrue(any("brána vykupit" in e for e in errs))

    def test_load_demo_parcel(self) -> None:
        parcel = inputs.load_demo_parcel("01-stavebni-happy")
        self.assertIn("cislo_parcely", parcel)

    def test_policy_error_on_incomplete(self) -> None:
        with self.assertRaises(policy.PolicyError):
            policy.normalize_scores({"skore_slozky": {}})


class TestCLIExecution(unittest.TestCase):
    def test_evaluate_offline_json(self) -> None:
        import subprocess
        res = subprocess.run(
            ["python3", "evaluate.py", "--offline", "tests/cases/01-stavebni-happy/input.json"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["doporuceni"], "vykupit")

    def test_evaluate_offline_txt(self) -> None:
        import subprocess
        res = subprocess.run(
            ["python3", "evaluate.py", "--offline", "tests/cases/12-volny-text/input.txt"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["doporuceni"], "zkoumat_dal")

    def test_evaluate_print_prompt(self) -> None:
        import subprocess
        res = subprocess.run(
            ["python3", "evaluate.py", "--print-prompt", "tests/cases/01-stavebni-happy/input.json"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("===== SYSTEM =====", res.stdout)
        self.assertIn("<parcela_data>", res.stdout)


if __name__ == "__main__":
    unittest.main()
