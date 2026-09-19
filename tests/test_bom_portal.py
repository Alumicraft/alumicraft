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
    def __init__(self, **values):
        super().__init__(
            name="BOM-STUDY-00001",
            modified="2026-09-18 10:00:00",
            title="Standard car",
            company="Alumicraft",
            currency="USD",
            standard_description="Standard car",
            mode="Historical only",
            status="Needs Review",
            projects=[Row(name="BOM-PROJECT-1", project="PROJECT-1", vehicle_count=1)],
            materials=[Row(name="MAT-1", line_key="generated", evidence='{"source":"PI-1"}', confidence=0.9,
                           project_count=1, description="Bracket", review_status="Pending", amount=10)],
            labor=[Row(name="LAB-1", evidence='{"source":"TS-1"}', activity_type="Fabrication", amount=20)],
            snapshot_json=json.dumps({"company": "Alumicraft", "projects": ["PROJECT-1"]}),
            decisions_json='{"secret": true}', requested_by="owner@example.com",
            material_allowance=0, overhead_allowance=0, target_margin=0,
            material_total=10, labor_total=20, estimated_total=30,
            suggested_retail=0, missing_cost_count=0, pending_review_count=1,
            warnings="", error_message="",
        )
        self.update(values)

    def check_permission(self, action):
        if self.get("deny_" + action):
            raise PermissionError("denied")

    def set(self, key, value):
        self[key] = [Row(v) for v in value]

    def save(self):
        self["modified"] = "2026-09-18 10:01:00"

    def insert(self):
        self["name"] = "BOM-STUDY-NEW"
        self["modified"] = "2026-09-18 10:01:00"


def load_module(path, name, modules):
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
    return result


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.flags = Row()
        self.frappe.session = Row(user="manager@example.com")
        self.frappe.PermissionError = PermissionError
        self.frappe.ValidationError = ValueError
        self.frappe.get_roles = lambda: ["Manufacturing Manager"]
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, exc=ValueError: (_ for _ in ()).throw(exc(message))
        self.study = Study()
        self.company_rows = [Row(name="Alumicraft", default_currency="USD")]
        self.project_rows = [Row(name="PROJECT-1", project_name="Project one", status="Open")]
        self.frappe.get_list = self.get_list
        self.frappe.get_doc = self.get_doc
        self.snapshot_checks = []
        self.service = types.ModuleType("alumicraft.bom.service")
        self.service._permission = lambda doc, action="write": doc.check_permission(action)
        self.service._check_snapshot_access = lambda snapshot: self.snapshot_checks.append(snapshot)
        self.engine = types.ModuleType("alumicraft.bom.engine")
        self.engine.ASSEMBLIES = ("Suspension", "Unknown")
        self.engine.PURPOSES = ("Standard", "Unknown")
        root = Path(__file__).parents[1]
        self.portal = load_module(
            root / "alumicraft/bom/portal.py",
            "isolated_bom_portal",
            {
                "frappe": self.frappe,
                "alumicraft.bom.service": self.service,
                "alumicraft.bom.engine": self.engine,
            },
        )

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            values = dict(doctype)
            values.pop("doctype", None)
            values.setdefault("status", "Draft")
            return Study(**values)
        if name != self.study.name:
            raise PermissionError("not found")
        return self.study

    def get_list(self, doctype, **kwargs):
        if doctype == "Company":
            return self.company_rows
        if doctype == "Project":
            return self.project_rows
        if doctype == "Vehicle BOM Study":
            return [Row(name=self.study.name, title=self.study.title, status=self.study.status,
                        company=self.study.company, currency=self.study.currency,
                        estimated_total=self.study.estimated_total, modified=self.study.modified)]
        return []

    def test_guest_and_wrong_role_are_rejected(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.portal.bootstrap()
        self.frappe.session.user = "manager@example.com"
        self.frappe.get_roles = lambda: ["Employee"]
        with self.assertRaises(PermissionError):
            self.portal.bootstrap()

    def test_permission_filtered_reference_and_study_lists(self):
        self.assertEqual(self.portal.bootstrap()["companies"], [{"name": "Alumicraft", "currency": "USD"}])
        self.assertEqual(self.portal.search_projects("Alumicraft"), [{"name": "PROJECT-1", "project_name": "Project one", "status": "Open"}])
        self.assertEqual(self.portal.list_studies()[0]["name"], self.study.name)

    def test_study_response_excludes_snapshot_decisions_and_requester(self):
        result = self.portal.get_study(self.study.name)
        self.assertNotIn("snapshot_json", result)
        self.assertNotIn("decisions_json", result)
        self.assertNotIn("requested_by", result)
        self.assertEqual(len(self.snapshot_checks), 1)
        self.assertEqual(result["materials"][0]["evidence"], '{"source":"PI-1"}')

    def test_document_read_and_write_permissions_are_enforced(self):
        self.study.deny_read = True
        with self.assertRaises(PermissionError):
            self.portal.get_study(self.study.name)
        self.study.deny_read = False
        self.study.deny_write = True
        with self.assertRaises(PermissionError):
            self.portal.save_study({"name": self.study.name, "modified": self.study.modified, "title": "Denied"})

    def test_save_allowlist_preserves_evidence_and_rejects_sensitive_writes(self):
        payload = {
            "name": self.study.name,
            "modified": self.study.modified,
            "title": "Updated title",
            "materials": [{"name": "MAT-1", "description": "Updated bracket", "review_status": "Approved"}],
            "labor": [{"name": "LAB-1", "activity_type": "Welding"}],
            "projects": [{"project": "PROJECT-1", "vehicle_count": 1}],
        }
        result = self.portal.save_study(payload)
        self.assertEqual(result["title"], "Updated title")
        self.assertEqual(self.study.materials[0].evidence, '{"source":"PI-1"}')
        with self.assertRaises(ValueError):
            self.portal.save_study({"name": self.study.name, "modified": self.study.modified, "snapshot_json": "x"})
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "name": self.study.name,
                "modified": self.study.modified,
                "materials": [{"name": "MAT-1"}, {"name": "MAT-1"}],
            })

    def test_stale_modified_token_is_rejected_before_save(self):
        with self.assertRaises(ValueError):
            self.portal.save_study({"name": self.study.name, "modified": "old", "title": "Race"})

    def test_new_study_is_server_controlled_draft(self):
        result = self.portal.save_study({
            "title": "New car", "company": "Alumicraft", "standard_description": "New",
            "mode": "Historical only", "projects": [{"project": "PROJECT-1", "vehicle_count": 1}],
        })
        self.assertEqual(result["name"], "BOM-STUDY-NEW")
        self.assertEqual(result["status"], "Draft")

    def test_malformed_scalar_and_project_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "title": "New car",
                "company": {"name": "Alumicraft"},
                "projects": [{"project": "PROJECT-1", "vehicle_count": 1}],
            })
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "title": "New car",
                "company": "Alumicraft",
                "projects": [{"project": {"name": "PROJECT-1"}, "vehicle_count": 1}],
            })
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "name": self.study.name,
                "modified": self.study.modified,
                "materials": [{"name": "MAT-1", "description": {"text": "bad"}}],
            })

    def test_payload_text_and_row_limits_are_bounded(self):
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "title": "New car",
                "company": "Alumicraft",
                "standard_description": "x" * 10_001,
            })
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "name": self.study.name,
                "modified": self.study.modified,
                "materials": [{"name": "MAT-1", "notes": "x" * 10_001}],
            })
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "title": "New car",
                "company": "Alumicraft",
                "projects": [{"project": "PROJECT-1", "vehicle_count": 1}] * 101,
            })
        with self.assertRaises(ValueError):
            self.portal.save_study({
                "title": "New car",
                "company": "Alumicraft",
                "materials": [{"description": "small", "notes": "review"}] * 20_001,
            })
        with self.assertRaises(ValueError):
            self.portal.save_study("x" * (10 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
