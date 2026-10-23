"""Unit testy pro knihovnu konzultant (deterministická vrstva, validace, politika)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from konzultant import checks, inputs, pipeline, policy, schema

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


class TestPipelineExtraction(unittest.TestCase):
    def test_extract_json_markdown_block(self) -> None:
        raw = "Zde je výsledek:\n```json\n{\"test\": 123}\n```\nS pozdravem."
        extracted = pipeline.extract_json(raw)
        self.assertEqual(extracted, {"test": 123})

    def test_extract_json_embedded(self) -> None:
        raw = "Úvodní text před JSONem: {\"klic\": \"hodnota\"} a závěrečný komentář."
        extracted = pipeline.extract_json(raw)
        self.assertEqual(extracted, {"klic": "hodnota"})


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


if __name__ == "__main__":
    unittest.main()
