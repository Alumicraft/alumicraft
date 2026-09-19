import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def as_dict(self):
        return dict(self)


class Study(Row):
    def check_permission(self, action):
        if self.get("deny"):
            raise PermissionError("denied")

    def save(self, **kwargs):
        self["saved"] = self.get("saved", 0) + 1

    def set(self, key, value):
        self[key] = [Row(r) for r in value] if isinstance(value, list) else value


def load_module(path, name, modules):
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
    return result


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.flags = Row()
        self.frappe.session = Row(user="manager")
        self.frappe.PermissionError = PermissionError
        self.frappe.get_roles = lambda: ["Manufacturing Manager"]
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, exc=ValueError: (_ for _ in ()).throw(exc(message))
        self.frappe.set_user = lambda user: self.frappe.session.update(user=user)
        self.frappe.db = Row(commit=lambda: None, rollback=lambda: None)
        self.frappe.response = {}
        self.queued = []
        self.frappe.enqueue = lambda *a, **kw: self.queued.append((a, kw))
        self.snapshot = {"company": "AC", "currency": "USD", "projects": ["P1"],
                         "purchase_lines": [], "labor_lines": [], "warnings": []}
        self.study = Study(name="BOM-STUDY-00001", title="Standard car", company="AC", currency="USD",
                           mode="Historical only", standard_description="Standard car", status="Draft",
                           projects=[Row(project="P1", vehicle_count=1)], materials=[], labor=[],
                           from_date=None, to_date=None, snapshot_json="", decisions_json="", metrics_json="")
        self.frappe.get_doc = lambda *a, **kw: self.study
        self.frappe.get_list = lambda dt, **kw: [Row(name=n) for n in kw["filters"]["name"][1]]
        root = Path(__file__).parents[1]
        self.service = load_module(root / "alumicraft/bom/service.py", "isolated_bom_service", {"frappe": self.frappe})

    def test_start_requires_saved_draft_and_enqueues_after_commit_without_secret(self):
        result = self.service.start(self.study.name)
        self.assertEqual(result["status"], "Queued")
        self.assertEqual(self.study.requested_by, "manager")
        self.assertTrue(self.queued[0][1]["enqueue_after_commit"])
        self.assertNotIn("api_key", self.queued[0][1])
        with self.assertRaises(ValueError):
            self.service.start(self.study.name)
        self.assertEqual(len(self.queued), 1)

    def test_readonly_role_and_owner_permissions_are_enforced(self):
        self.study.deny = True
        with self.assertRaises(PermissionError):
            self.service.start(self.study.name)
        self.study.deny = False
        self.frappe.get_roles = lambda: ["Employee"]
        with self.assertRaises(PermissionError):
            self.service.start(self.study.name)

    def test_historical_mode_does_not_load_or_call_provider(self):
        self.study.update(status="Queued", requested_by="owner", snapshot_json=json.dumps(self.snapshot))
        self.service._provider_config = lambda: self.fail("Provider config accessed in historical-only mode")
        self.service.run(self.study.name)
        self.assertEqual(self.study.status, "Needs Review")
        self.assertEqual(self.frappe.session.user, "manager")
        self.assertIn("No project-linked", self.study.warnings)

    def test_resume_uses_snapshot_and_retains_successes_after_partial_provider_failure(self):
        self.study.update(status="Queued", requested_by="owner", mode="Jev", snapshot_json=json.dumps(self.snapshot))
        self.service._provider_config = lambda: {"api_key": "SECRET", "model": "jev-latest"}
        provider = types.ModuleType("alumicraft.bom.jev")
        def classify(*args, **kwargs):
            kwargs["on_result"]("done", {"assembly": "Electrical"})
            return {"decisions": {}, "cache": {"done": {"assembly": "Electrical"}},
                    "incomplete": True, "metrics": {"requests": 1}, "warnings": ["Budget exhausted"]}
        provider.classify_rows = classify
        with patch.dict(sys.modules, {"alumicraft.bom.jev": provider}):
            self.service.run(self.study.name)
        self.assertEqual(self.study.status, "Failed")
        self.assertIn("done", json.loads(self.study.decisions_json)["cache"])
        self.assertNotIn("SECRET", self.study.error_message)
        self.assertEqual(self.study.materials, [])
        self.assertEqual(self.frappe.session.user, "manager")

    def test_recheck_access_uses_bounded_parent_queries_and_rejects_missing_permissions(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["purchase_lines"] = [{"invoice": "PI" + str(i)} for i in range(1001)]
        calls = []
        def get_list(dt, **kw):
            calls.append((dt, kw))
            return [Row(name=n) for n in kw["filters"]["name"][1]]
        self.frappe.get_list = get_list
        self.service._check_snapshot_access(snapshot)
        self.assertEqual(len([c for c in calls if c[0] == "Purchase Invoice"]), 3)
        self.frappe.get_list = lambda *a, **kw: []
        with self.assertRaises(PermissionError):
            self.service._check_snapshot_access(snapshot)

    def test_csv_escapes_formula_injection(self):
        for text in ("=HYPERLINK(1)", "+SUM(A1)", "  @SUM(A1)", "\tformula"):
            self.assertTrue(self.service._cell(text).startswith("'"))
        self.assertEqual(self.service._cell("Shock absorber"), "Shock absorber")
        self.assertEqual(self.service._cell(42), "42")

    def test_recovery_refuses_active_worker_and_preserves_completed_cache(self):
        jobs = types.ModuleType("frappe.utils.background_jobs")
        # Frappe v16 is_job_enqueued covers both QUEUED and STARTED states.
        jobs.is_job_enqueued = lambda name: True
        self.study.update(status="Running", decisions_json='{"cache":{"done":{}}}')
        with patch.dict(sys.modules, {"frappe.utils.background_jobs": jobs}):
            with self.assertRaises(ValueError):
                self.service.recover(self.study.name)
            self.assertEqual(self.study.status, "Running")
            jobs.is_job_enqueued = lambda name: False
            self.service.recover(self.study.name)
        self.assertEqual(self.study.status, "Failed")
        self.assertIn("done", json.loads(self.study.decisions_json)["cache"])


if __name__ == "__main__":
    unittest.main()
