import importlib
import json
import sys
import types
import unittest


class FakePermissionError(Exception):
    pass


class FakeField:
    def __init__(self, fieldtype=None, options=None):
        self.fieldtype = fieldtype
        self.options = options


class FakeMeta:
    def __init__(self, fields=None):
        self.fields = fields or {}

    def get_field(self, fieldname):
        return self.fields.get(fieldname)


class FakeDB:
    @staticmethod
    def escape(value, percent=False):
        return "'{0}'".format(value.replace("'", "''"))


class FakeFrappe(types.ModuleType):
    def __init__(self):
        super().__init__("frappe")
        self.PermissionError = FakePermissionError
        self._ = lambda value: value
        self.conf = {}
        self.db = FakeDB()
        self.local = types.SimpleNamespace(
            request=types.SimpleNamespace(path="", method="GET"),
            form_dict={},
        )
        self.session = types.SimpleNamespace(user="kiosk@example.com")
        self.roles = {
            "kiosk@example.com": ["Kiosk User"],
            "manager@example.com": ["System Manager"],
            "mixed@example.com": ["Kiosk User", "Projects User"],
            "employee-manager@example.com": ["Employee", "Projects User"],
        }
        self.employee_records = [
            {
                "name": "EMP-0001",
                "company": "Alumicraft",
                "employee_name": "Terminal Operator",
                "department": "Fabrication",
            }
        ]
        self.meta = {}

    def get_roles(self, user):
        return self.roles.get(user, [])

    @staticmethod
    def safe_decode(value):
        return str(value)

    @staticmethod
    def parse_json(value):
        return json.loads(value)

    def get_all(self, doctype, **kwargs):
        self.last_get_all = (doctype, kwargs)
        return list(self.employee_records)

    def get_meta(self, doctype):
        return self.meta.get(doctype, FakeMeta())

    @staticmethod
    def throw(message, exc=Exception):
        raise exc(message)


class Doc:
    def __init__(self, employee=None):
        self.employee = employee

    def get(self, fieldname):
        return getattr(self, fieldname, None)


class KioskPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_frappe = sys.modules.get("frappe")
        cls.frappe = FakeFrappe()
        sys.modules["frappe"] = cls.frappe

        sys.modules.pop("alumicraft.api.boot", None)
        sys.modules.pop("alumicraft.permissions", None)
        cls.boot = importlib.import_module("alumicraft.api.boot")
        cls.permissions = importlib.import_module("alumicraft.permissions")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("alumicraft.api.boot", None)
        sys.modules.pop("alumicraft.permissions", None)
        if cls.original_frappe is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = cls.original_frappe

    def setUp(self):
        self.frappe.session.user = "kiosk@example.com"
        self.frappe.employee_records = [
            {
                "name": "EMP-0001",
                "company": "Alumicraft",
                "employee_name": "Terminal Operator",
                "department": "Fabrication",
            }
        ]
        self.frappe.local.request = types.SimpleNamespace(path="", method="GET")
        self.frappe.local.form_dict = {}
        self.frappe.meta = {}

    def test_public_kiosk_detection_is_fail_closed_for_kiosk_accounts(self):
        self.assertTrue(self.boot.is_kiosk_user("kiosk@example.com"))
        self.assertTrue(self.boot.is_kiosk_user("mixed@example.com"))
        self.assertTrue(
            self.boot.is_kiosk_user("kiosk@drivealumicraft.com")
        )
        self.assertFalse(
            self.boot.is_kiosk_user("employee-manager@example.com")
        )
        self.assertFalse(self.boot.is_kiosk_user("manager@example.com"))
        self.assertFalse(self.boot.is_kiosk_user("Administrator"))

        self.frappe.conf["alumicraft_kiosk_users"] = ["extra@example.com"]
        try:
            self.assertTrue(self.boot.is_kiosk_user("extra@example.com"))
            self.assertTrue(
                self.boot.is_kiosk_user("kiosk@drivealumicraft.com")
            )
        finally:
            self.frappe.conf.pop("alumicraft_kiosk_users", None)

    def test_protected_doctypes_deny_kiosk_but_not_manager(self):
        self.assertFalse(
            self.permissions.protected_doctype_has_permission(
                Doc(), user="kiosk@example.com", ptype="read"
            )
        )
        with self.assertRaises(FakePermissionError):
            self.permissions.deny_protected_doctype_query(
                "kiosk@example.com", doctype="Employee"
            )

        self.assertTrue(
            self.permissions.protected_doctype_has_permission(
                Doc(), user="manager@example.com", ptype="read"
            )
        )
        self.assertEqual(
            self.permissions.deny_protected_doctype_query(
                "manager@example.com", doctype="Employee"
            ),
            "",
        )

    def test_timesheet_queries_are_limited_to_the_one_linked_employee(self):
        self.assertEqual(
            self.permissions.timesheet_query_conditions("kiosk@example.com"),
            "`tabTimesheet`.`employee` = 'EMP-0001'",
        )

        self.frappe.employee_records = []
        self.assertEqual(
            self.permissions.timesheet_query_conditions("kiosk@example.com"),
            "1 = 0",
        )

        self.frappe.employee_records = [
            {"name": "EMP-0001"},
            {"name": "EMP-0002"},
        ]
        self.assertIsNone(
            self.permissions.get_kiosk_employee("kiosk@example.com")
        )

    def test_timesheet_permission_allows_only_own_narrow_lifecycle(self):
        own = Doc("EMP-0001")
        other = Doc("EMP-9999")

        for permission_type in ("read", "write", "submit"):
            self.assertTrue(
                self.permissions.timesheet_has_permission(
                    own,
                    user="kiosk@example.com",
                    ptype=permission_type,
                )
            )

        self.assertTrue(
            self.permissions.timesheet_has_permission(
                Doc(), user="kiosk@example.com", ptype="create"
            )
        )
        self.assertFalse(
            self.permissions.timesheet_has_permission(
                other, user="kiosk@example.com", ptype="read"
            )
        )
        self.assertFalse(
            self.permissions.timesheet_has_permission(
                own, user="kiosk@example.com", ptype="delete"
            )
        )

    def test_timesheet_validation_fills_blank_and_rejects_other_employee(self):
        blank = Doc()
        self.permissions.validate_kiosk_timesheet(blank)
        self.assertEqual(blank.employee, "EMP-0001")
        self.assertEqual(blank.company, "Alumicraft")
        self.assertEqual(blank.employee_name, "Terminal Operator")
        self.assertEqual(blank.department, "Fabrication")
        self.assertEqual(blank.user, "kiosk@example.com")

        tampered = Doc("EMP-0001")
        tampered.company = "Wrong Company"
        self.permissions.validate_kiosk_timesheet(tampered)
        self.assertEqual(tampered.company, "Alumicraft")

        with self.assertRaises(FakePermissionError):
            self.permissions.validate_kiosk_timesheet(Doc("EMP-9999"))

    def test_kiosk_cannot_cancel_discard_delete_or_rename_timesheets(self):
        with self.assertRaises(FakePermissionError):
            self.permissions.deny_kiosk_timesheet_mutation(Doc("EMP-0001"))

        self.frappe.session.user = "manager@example.com"
        self.permissions.deny_kiosk_timesheet_mutation(
            Doc("EMP-0001"), "before_rename", "old", "new", False
        )

    def test_before_request_denies_direct_employee_and_user_access(self):
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/resource/Employee/EMP-0001", method="GET"
        )
        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/frappe.client.get", method="GET"
        )
        self.frappe.local.form_dict = {"doctype": "User", "name": "kiosk@example.com"}
        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

    def test_before_request_denies_reports_and_exports(self):
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/frappe.desk.query_report.run", method="GET"
        )
        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

    def test_before_request_denies_indirect_user_data_endpoints(self):
        for command in (
            "frappe.desk.search.get_names_for_mentions",
            "frappe.desk.form.load.get_user_info_for_viewers",
        ):
            with self.subTest(command=command):
                self.frappe.local.request = types.SimpleNamespace(
                    path="/api/method/{0}".format(command), method="GET"
                )
                with self.assertRaises(FakePermissionError):
                    self.permissions.before_request()

    def test_before_request_forces_employee_filter_on_timesheet_lists(self):
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/resource/Timesheet", method="GET"
        )
        self.frappe.local.form_dict = {"filters": '[["docstatus", "=", 0]]'}

        self.permissions.before_request()

        self.assertEqual(
            self.frappe.local.form_dict["filters"],
            [
                ["docstatus", "=", 0],
                ["Timesheet", "employee", "=", "EMP-0001"],
            ],
        )

        self.frappe.local.form_dict = {}
        self.permissions.before_request()
        self.assertEqual(
            self.frappe.local.form_dict["filters"],
            [["Timesheet", "employee", "=", "EMP-0001"]],
        )

    def test_before_request_denies_link_expansion_into_employee(self):
        self.frappe.meta["Timesheet"] = FakeMeta(
            {"employee": FakeField("Link", "Employee")}
        )
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/resource/Timesheet", method="GET"
        )
        self.frappe.local.form_dict = {
            "fields": '["name", "employee.employee_name"]'
        }

        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

    def test_before_request_is_noop_for_non_kiosk_users(self):
        self.frappe.session.user = "manager@example.com"
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/resource/Employee", method="GET"
        )
        self.permissions.before_request()


if __name__ == "__main__":
    unittest.main()
