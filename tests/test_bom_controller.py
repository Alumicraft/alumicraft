import importlib
import sys
import types
import unittest


class FakeFrappeError(Exception):
	pass


class FakeDocument:
	pass


class PermissionDocument:
	def check_permission(self, _permission):
		return None


class ProjectDocument(PermissionDocument):
	company = "Alumicraft"
	project_type = "Build"


class FakeDB:
	def get_value(self, doctype, name, fieldname):
		if doctype == "Company" and name == "Alumicraft" and fieldname == "default_currency":
			return "USD"
		return None


class FakeFrappe(types.ModuleType):
	def __init__(self):
		super().__init__("frappe")
		self.flags = types.SimpleNamespace(vehicle_bom_internal=False)
		self.db = FakeDB()

	def throw(self, message):
		raise FakeFrappeError(message)

	def get_doc(self, doctype, _name):
		return ProjectDocument() if doctype == "Project" else PermissionDocument()


class Row(dict):
	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError as exc:
			raise AttributeError(name) from exc

	def __setattr__(self, name, value):
		self[name] = value

	def as_dict(self):
		return dict(self)


class StudyDoc:
	def __init__(self, controller_class, previous=None, **values):
		self._previous = previous
		self._controller_class = controller_class
		defaults = {
			"title": "Standard vehicle",
			"company": "Alumicraft",
			"standard_description": "Alumicraft standard vehicle",
			"mode": "Historical only",
			"projects": [Row(project="P-001", vehicle_count=1)],
			"from_date": None,
			"to_date": None,
			"status": "Needs Review",
			"requested_by": "test@example.com",
			"snapshot_json": "{\"snapshot\": true, \"currency\": \"USD\"}",
			"decisions_json": "{\"decisions\": true}",
			"metrics_json": "{\"metrics\": true}",
			"warnings": "",
			"error_message": "",
			"materials": [],
			"labor": [],
			"material_allowance": 0,
			"overhead_allowance": 0,
			"target_margin": 0,
		}
		defaults.update(values)
		for key, value in defaults.items():
			setattr(self, key, value)

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def get_doc_before_save(self):
		return self._previous

	def has_value_changed(self, fieldname):
		return self._previous is not None and getattr(self, fieldname) != getattr(
			self._previous, fieldname
		)

	def update(self, values):
		for key, value in values.items():
			setattr(self, key, value)

	def _validate_evidence(self, previous):
		return self._controller_class._validate_evidence(self, previous)


def _material(**overrides):
	values = {
		"name": "MAT-1",
		"line_key": "source-line-1",
		"assembly": "Unknown",
		"description": "Aluminium bracket",
		"stock_uom": "Nos",
		"proposed_quantity": 1,
		"unit_cost": 10,
		"cost_known": 1,
		"amount": 10,
		"purpose": "Unknown",
		"confidence": 0.8,
		"review_status": "Approved",
		"project_count": 1,
		"evidence": '{"source": "PII-1"}',
		"notes": "Generated evidence",
	}
	values.update(overrides)
	return Row(**values)


def _labor(**overrides):
	values = {
		"name": "LAB-1",
		"activity_type": "Fabrication",
		"proposed_hours": 2,
		"hourly_cost": 25,
		"cost_known": 1,
		"amount": 50,
		"review_status": "Approved",
		"evidence": '{"source": "TSD-1"}',
		"notes": "Generated evidence",
	}
	values.update(overrides)
	return Row(**values)


class VehicleBOMControllerTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.original_modules = {
			name: sys.modules.get(name)
			for name in ("frappe", "frappe.model", "frappe.model.document")
		}
		fake_frappe = FakeFrappe()
		frappe_model = types.ModuleType("frappe.model")
		frappe_document = types.ModuleType("frappe.model.document")
		frappe_document.Document = FakeDocument
		sys.modules["frappe"] = fake_frappe
		sys.modules["frappe.model"] = frappe_model
		sys.modules["frappe.model.document"] = frappe_document
		module_name = "alumicraft.alumicraft.doctype.vehicle_bom_study.vehicle_bom_study"
		sys.modules.pop(module_name, None)
		cls.controller = importlib.import_module(module_name)
		cls.frappe = fake_frappe

	@classmethod
	def tearDownClass(cls):
		module_name = "alumicraft.alumicraft.doctype.vehicle_bom_study.vehicle_bom_study"
		sys.modules.pop(module_name, None)
		for name, module in cls.original_modules.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	def study(self, previous=None, **values):
		return StudyDoc(self.controller.VehicleBOMStudy, previous=previous, **values)

	def assert_rejected(self, study, message):
		with self.assertRaisesRegex(FakeFrappeError, message):
			self.controller.VehicleBOMStudy.validate(study)

	def reviewed_pair(self):
		previous = self.study(
			materials=[_material()],
			labor=[_labor()],
			material_allowance=5,
			target_margin=10,
		)
		current = self.study(
			previous=previous,
			materials=[_material(proposed_quantity=2, amount=20)],
			labor=[_labor(proposed_hours=3, amount=75)],
			material_allowance=20,
			overhead_allowance=10,
			target_margin=15,
		)
		return previous, current

	def test_run_state_and_source_evidence_are_protected_from_user_edits(self):
		for field, value in {
			"status": "Queued",
			"requested_by": "another@example.com",
			"snapshot_json": '{"changed": true}',
			"decisions_json": '{"changed": true}',
			"metrics_json": '{"changed": true}',
			"warnings": "changed",
			"error_message": "changed",
		}.items():
			with self.subTest(field=field):
				previous = self.study(status="Draft", snapshot_json="")
				values = {"snapshot_json": ""}
				values[field] = value
				current = self.study(previous=previous, **values)
				self.assert_rejected(current, "Run state and source evidence")

	def test_source_configuration_and_projects_freeze_after_run(self):
		previous, current = self.reviewed_pair()
		current.standard_description = "A different vehicle"
		self.assert_rejected(current, "source configuration is frozen")

		previous, current = self.reviewed_pair()
		current.projects = [Row(project="P-002", vehicle_count=1)]
		self.assert_rejected(current, "Projects and vehicle counts cannot change")

	def test_queued_and_running_studies_reject_user_saves(self):
		for status in ("Queued", "Running"):
			previous = self.study(status=status)
			current = self.study(previous=previous, status=status)
			self.assert_rejected(current, "Wait for the background run")

	def test_review_edit_allows_costing_and_margin_changes(self):
		_, current = self.reviewed_pair()
		self.controller.VehicleBOMStudy.validate(current)
		self.assertEqual(current.material_allowance, 20)
		self.assertEqual(current.target_margin, 15)
		self.assertEqual(current.material_total, 20)
		self.assertEqual(current.labor_total, 75)

	def test_service_project_cannot_be_saved(self):
		original_type = ProjectDocument.project_type
		try:
			ProjectDocument.project_type = "Service/Parts"
			previous = self.study(status="Draft", snapshot_json="")
			self.assert_rejected(self.study(previous=previous, status="Draft", snapshot_json=""), "require Build projects")
		finally:
			ProjectDocument.project_type = original_type

	def test_generated_evidence_cannot_be_deleted(self):
		previous, current = self.reviewed_pair()
		current.materials = []
		self.assert_rejected(current, "Exclude a generated line")

	def test_manual_material_and_labor_additions_require_notes(self):
		previous, current = self.reviewed_pair()
		current.materials.append(
			_material(
				name="MANUAL-MAT",
				line_key="",
				evidence="",
				confidence=0,
				project_count=0,
				notes="",
			)
		)
		self.assert_rejected(current, "each manually added material or labor line")

		previous, current = self.reviewed_pair()
		current.labor.append(
			_labor(name="MANUAL-LAB", evidence="", notes="")
		)
		self.assert_rejected(current, "each manually added material or labor line")


if __name__ == "__main__":
	unittest.main()
