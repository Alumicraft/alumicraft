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
    def __init__(self, frappe_module):
        self.frappe = frappe_module

    @staticmethod
    def escape(value, percent=False):
        return "'{0}'".format(value.replace("'", "''"))

    def get_value(self, doctype, name, fields, as_dict=False):
        if doctype != "Timesheet":
            return None

        row = self.frappe.timesheets.get(name)
        if not row:
            return None

        if isinstance(fields, str):
            return row.get(fields)

        values = {fieldname: row.get(fieldname) for fieldname in fields}
        if as_dict:
            return values
        return tuple(values[fieldname] for fieldname in fields)


class FakeFrappe(types.ModuleType):
    def __init__(self):
        super().__init__("frappe")
        self.PermissionError = FakePermissionError
        self._ = lambda value: value
        self.conf = {}
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
        self.employee_records = []
        self.timesheets = {}
        self.meta = {}
        self.last_get_all = None
        self.db = FakeDB(self)

    def get_roles(self, user):
        return self.roles.get(user, [])

    @staticmethod
    def safe_decode(value):
        return str(value)

    @staticmethod
    def parse_json(value):
        return json.loads(value)

    @staticmethod
    def whitelist(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]

        def decorator(function):
            return function

        return decorator

    @staticmethod
    def _matches_filter(record, fieldname, expected):
        actual = record.get(fieldname)
        if isinstance(expected, (list, tuple)) and len(expected) == 2:
            operator, value = expected
            if str(operator).lower() == "like":
                needle = str(value).replace("%", "").lower()
                return needle in str(actual or "").lower()
        return actual == expected

    def get_all(self, doctype, **kwargs):
        self.last_get_all = (doctype, kwargs)
        if doctype != "Employee":
            return []

        rows = [dict(row) for row in self.employee_records]
        filters = kwargs.get("filters") or {}
        rows = [
            row
            for row in rows
            if all(
                self._matches_filter(row, fieldname, expected)
                for fieldname, expected in filters.items()
            )
        ]

        or_filters = kwargs.get("or_filters") or {}
        if or_filters:
            rows = [
                row
                for row in rows
                if any(
                    self._matches_filter(row, fieldname, expected)
                    for fieldname, expected in or_filters.items()
                )
            ]

        rows.sort(key=lambda row: (row.get("employee_name") or "", row.get("name") or ""))
        start = max(int(kwargs.get("start") or 0), 0)
        page_length = kwargs.get("page_length")
        if page_length is None:
            page_length = kwargs.get("limit_page_length")
        if page_length is not None:
            rows = rows[start : start + int(page_length)]
        else:
            rows = rows[start:]

        fields = kwargs.get("fields")
        if not fields:
            return rows
        if isinstance(fields, str):
            fields = [fields]
        if kwargs.get("as_list"):
            return [[row.get(fieldname) for fieldname in fields] for row in rows]
        return [
            {fieldname: row.get(fieldname) for fieldname in fields}
            for row in rows
        ]

    def get_meta(self, doctype):
        return self.meta.get(doctype, FakeMeta())

    @staticmethod
    def throw(message, exc=Exception):
        raise exc(message)


class Doc:
    def __init__(
        self,
        employee=None,
        *,
        owner="kiosk@example.com",
        name=None,
        is_new=True,
    ):
        self.employee = employee
        self.owner = owner
        self.name = name
        self.company = "Tampered Company"
        self.employee_name = "Tampered Name"
        self.department = "Tampered Department"
        self.user = "tampered@example.com"
        self._is_new = is_new

    def get(self, fieldname):
        return getattr(self, fieldname, None)

    def is_new(self):
        return self._is_new


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
        self.frappe.conf = {}
        self.frappe.employee_records = [
            {
                "name": "EMP-0001",
                "status": "Active",
                "company": "Alumicraft",
                "employee_name": "Alice Active",
                "department": "Fabrication",
            },
            {
                "name": "EMP-0002",
                "status": "Active",
                "company": "Alumicraft",
                "employee_name": "Bob Builder",
                "department": "Assembly",
            },
            {
                "name": "EMP-0003",
                "status": "Inactive",
                "company": "Alumicraft",
                "employee_name": "Ivy Inactive",
                "department": "Fabrication",
            },
            {
                "name": "EMP-0004",
                "status": "Active",
                "company": "Other Company",
                "employee_name": "Oscar Other",
                "department": "Fabrication",
            },
        ]
        self.frappe.timesheets = {
            "TS-OWN": {
                "owner": "kiosk@example.com",
                "employee": "EMP-0001",
            },
            "TS-OTHER": {
                "owner": "other@example.com",
                "employee": "EMP-0001",
            },
        }
        self.frappe.local.request = types.SimpleNamespace(path="", method="GET")
        self.frappe.local.form_dict = {}
        self.frappe.meta = {}
        self.frappe.last_get_all = None

    def _safe_employee_search_form(self):
        return {
            "doctype": "Employee",
            "txt": "ali",
            "query": self.permissions.KIOSK_EMPLOYEE_SEARCH_QUERY,
            "reference_doctype": "Timesheet",
            "link_fieldname": "employee",
            "page_length": "10",
        }

    def _safe_timesheet_summary_form(self, employee="\u2063"):
        return {
            "doctype": "Timesheet",
            "filters": json.dumps(
                {
                    "employee": employee,
                    "docstatus": ["<", 2],
                    "start_date": ["<=", "2026-07-12"],
                    "end_date": [">=", "2026-07-06"],
                }
            ),
            "fields": '["name"]',
            "limit_page_length": "0",
        }

    def test_kiosk_classification_remains_fail_closed(self):
        self.assertTrue(self.boot.is_kiosk_user("kiosk@example.com"))
        self.assertTrue(self.boot.is_kiosk_user("mixed@example.com"))
        self.assertTrue(self.boot.is_kiosk_user("kiosk@drivealumicraft.com"))
        self.assertFalse(self.boot.is_kiosk_user("employee-manager@example.com"))
        self.assertFalse(self.boot.is_kiosk_user("manager@example.com"))

    def test_kiosk_boot_exposes_only_the_fixed_company_not_employee_records(self):
        bootinfo = {}
        self.boot.boot_session(bootinfo)

        self.assertEqual(bootinfo["alumicraft_kiosk"]["company"], "Alumicraft")
        self.assertNotIn(
            "timesheet_defaults", bootinfo["alumicraft_kiosk"]
        )

        self.frappe.conf["alumicraft_kiosk_company"] = "Configured Company"
        self.assertEqual(self.boot.get_kiosk_company(), "Configured Company")

    def test_custom_employee_search_returns_only_active_company_labels(self):
        results = self.permissions.search_kiosk_employees(
            "Employee",
            "",
            "name",
            0,
            500,
            reference_doctype="Timesheet",
            link_fieldname="employee",
        )

        self.assertEqual(
            results,
            [
                ["EMP-0001", "Alice Active"],
                ["EMP-0002", "Bob Builder"],
            ],
        )
        doctype, kwargs = self.frappe.last_get_all
        self.assertEqual(doctype, "Employee")
        self.assertEqual(kwargs["fields"], ["name", "employee_name"])
        self.assertEqual(kwargs["filters"], {"status": "Active", "company": "Alumicraft"})
        self.assertEqual(kwargs["page_length"], 20)

    def test_custom_employee_search_rejects_wrong_context(self):
        with self.assertRaises(FakePermissionError):
            self.permissions.search_kiosk_employees(
                "Employee",
                "",
                "name",
                0,
                10,
                reference_doctype="Project",
                link_fieldname="employee",
            )

    def test_selected_employee_identity_returns_only_timesheet_fields(self):
        self.frappe.local.request = types.SimpleNamespace(
            path=(
                "/api/method/"
                "alumicraft.permissions.get_kiosk_employee_identity"
            ),
            method="POST",
        )
        self.frappe.local.form_dict = {"employee": "EMP-0001"}
        self.permissions.before_request()

        identity = self.permissions.get_kiosk_employee_identity("EMP-0001")
        self.assertEqual(
            identity,
            {
                "name": "EMP-0001",
                "company": "Alumicraft",
                "employee_name": "Alice Active",
                "department": "Fabrication",
            },
        )

        for employee in ("EMP-0003", "EMP-0004", "EMP-MISSING"):
            with self.subTest(employee=employee):
                with self.assertRaises(FakePermissionError):
                    self.permissions.get_kiosk_employee_identity(employee)

        self.frappe.session.user = "manager@example.com"
        with self.assertRaises(FakePermissionError):
            self.permissions.get_kiosk_employee_identity("EMP-0001")

        self.frappe.session.user = "manager@example.com"
        with self.assertRaises(FakePermissionError):
            self.permissions.search_kiosk_employees(
                "Employee",
                "",
                "name",
                0,
                10,
                reference_doctype="Timesheet",
                link_fieldname="employee",
            )

    def test_before_request_allows_only_safe_employee_search_envelope(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                self.frappe.local.request = types.SimpleNamespace(
                    path="/api/method/frappe.desk.search.search_link",
                    method=method,
                )
                self.frappe.local.form_dict = self._safe_employee_search_form()
                self.permissions.before_request()

        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/frappe.desk.search.search_link",
            method="DELETE",
        )
        self.frappe.local.form_dict = self._safe_employee_search_form()
        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

        unsafe_overrides = (
            {"query": "frappe.desk.search.search_widget"},
            {"reference_doctype": "Project"},
            {"link_fieldname": "owner"},
            {"filters": {"status": "Inactive"}},
            {"ignore_user_permissions": "1"},
        )
        for override in unsafe_overrides:
            with self.subTest(override=override):
                self.frappe.local.request = types.SimpleNamespace(
                    path="/api/method/frappe.desk.search.search_link",
                    method="POST",
                )
                self.frappe.local.form_dict = self._safe_employee_search_form()
                self.frappe.local.form_dict.update(override)
                with self.assertRaises(FakePermissionError):
                    self.permissions.before_request()

    def test_direct_custom_search_http_call_is_denied(self):
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/{0}".format(
                self.permissions.KIOSK_EMPLOYEE_SEARCH_QUERY
            ),
            method="GET",
        )
        self.frappe.local.form_dict = self._safe_employee_search_form()

        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

    def test_direct_employee_and_user_access_is_denied(self):
        self.assertFalse(
            self.permissions.protected_doctype_has_permission(
                Doc("EMP-0001"), user="kiosk@example.com", ptype="read"
            )
        )
        with self.assertRaises(FakePermissionError):
            self.permissions.deny_protected_doctype_query(
                "kiosk@example.com", doctype="Employee"
            )

        requests = (
            (
                "/api/resource/Employee/EMP-0001",
                {},
            ),
            (
                "/api/method/frappe.client.get",
                {"doctype": "User", "name": "kiosk@example.com"},
            ),
        )
        for path, form in requests:
            with self.subTest(path=path):
                self.frappe.local.request = types.SimpleNamespace(
                    path=path, method="GET"
                )
                self.frappe.local.form_dict = form
                with self.assertRaises(FakePermissionError):
                    self.permissions.before_request()

    def test_reports_exports_and_sensitive_user_endpoints_are_denied(self):
        commands = (
            "frappe.desk.query_report.run",
            "frappe.desk.reportview.export_query",
            "frappe.desk.search.get_names_for_mentions",
            "frappe.desk.form.load.get_user_info_for_viewers",
        )
        for command in commands:
            with self.subTest(command=command):
                self.frappe.local.request = types.SimpleNamespace(
                    path="/api/method/{0}".format(command), method="GET"
                )
                self.frappe.local.form_dict = {}
                with self.assertRaises(FakePermissionError):
                    self.permissions.before_request()

    def test_employee_and_user_link_expansion_is_denied(self):
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

    def test_active_employee_validation_overwrites_identity_and_blanks_user(self):
        doc = Doc("EMP-0001")

        self.permissions.validate_kiosk_timesheet(doc)

        self.assertEqual(doc.employee, "EMP-0001")
        self.assertEqual(doc.company, "Alumicraft")
        self.assertEqual(doc.employee_name, "Alice Active")
        self.assertEqual(doc.department, "Fabrication")
        self.assertIsNone(doc.user)

    def test_validation_rejects_missing_inactive_and_wrong_company_employees(self):
        for employee in (None, "EMP-0003", "EMP-0004", "EMP-MISSING"):
            with self.subTest(employee=employee):
                with self.assertRaises(FakePermissionError):
                    self.permissions.validate_kiosk_timesheet(Doc(employee))

    def test_existing_timesheet_validation_is_owner_and_employee_bound(self):
        allowed = Doc("EMP-0001", name="TS-OWN", is_new=False)
        self.permissions.validate_kiosk_timesheet(allowed)
        self.assertIsNone(allowed.user)

        changed_employee = Doc("EMP-0002", name="TS-OWN", is_new=False)
        with self.assertRaises(FakePermissionError):
            self.permissions.validate_kiosk_timesheet(changed_employee)

        wrong_owner = Doc("EMP-0001", name="TS-OTHER", is_new=False)
        with self.assertRaises(FakePermissionError):
            self.permissions.validate_kiosk_timesheet(wrong_owner)

    def test_timesheet_read_and_list_access_is_denied(self):
        own = Doc("EMP-0001", owner="kiosk@example.com")
        self.assertEqual(
            self.permissions.timesheet_query_conditions("kiosk@example.com"),
            "1 = 0",
        )
        self.assertFalse(
            self.permissions.timesheet_has_permission(
                own, user="kiosk@example.com", ptype="read"
            )
        )

        self.frappe.local.request = types.SimpleNamespace(
            path="/api/resource/Timesheet", method="GET"
        )
        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

    def test_timesheet_link_fields_do_not_count_as_saved_timesheet_lists(self):
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/frappe.desk.search.search_link",
            method="POST",
        )
        self.frappe.local.form_dict = {
            "doctype": "Company",
            "txt": "Alumicraft",
            "reference_doctype": "Timesheet",
            "link_fieldname": "company",
        }

        self.permissions.before_request()

    def test_only_empty_weekly_summary_probe_is_allowed(self):
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/frappe.client.get_list",
            method="POST",
        )

        for employee in ("\u2063", "EMP-0001"):
            with self.subTest(employee=employee):
                self.frappe.local.form_dict = self._safe_timesheet_summary_form(
                    employee
                )
                self.permissions.before_request()
                self.assertEqual(
                    self.permissions.guarded_client_get_list(
                        "Timesheet",
                        fields='["name"]',
                        filters=self.frappe.local.form_dict["filters"],
                        limit_page_length=0,
                    ),
                    [],
                )

        unsafe_overrides = (
            {"fields": '["name", "employee"]'},
            {"limit_page_length": "20"},
            {"order_by": "creation desc"},
            {"filters": json.dumps({"employee": "EMP-0001"})},
            {
                "filters": self._safe_timesheet_summary_form(
                    "EMP-0003"
                )["filters"]
            },
            {
                "filters": json.dumps(
                    {
                        "employee": "EMP-0001",
                        "docstatus": ["<", 2],
                        "start_date": ["<=", "2026-08-01"],
                        "end_date": [">=", "2026-07-01"],
                    }
                )
            },
        )
        for override in unsafe_overrides:
            with self.subTest(override=override):
                self.frappe.local.form_dict = self._safe_timesheet_summary_form()
                self.frappe.local.form_dict.update(override)
                with self.assertRaises(FakePermissionError):
                    self.permissions.before_request()

        self.frappe.local.request = types.SimpleNamespace(
            path="/api/method/frappe.client.get_list",
            method="GET",
        )
        self.frappe.local.form_dict = self._safe_timesheet_summary_form()
        with self.assertRaises(FakePermissionError):
            self.permissions.before_request()

    def test_timesheet_write_and_submit_are_owner_bound(self):
        own = Doc("EMP-0001", owner="kiosk@example.com")
        other_owner = Doc("EMP-0001", owner="other@example.com")
        inactive = Doc("EMP-0003", owner="kiosk@example.com")
        wrong_company = Doc("EMP-0004", owner="kiosk@example.com")

        for permission_type in ("write", "submit"):
            with self.subTest(permission_type=permission_type):
                self.assertTrue(
                    self.permissions.timesheet_has_permission(
                        own,
                        user="kiosk@example.com",
                        ptype=permission_type,
                    )
                )
                self.assertFalse(
                    self.permissions.timesheet_has_permission(
                        other_owner,
                        user="kiosk@example.com",
                        ptype=permission_type,
                    )
                )
                self.assertFalse(
                    self.permissions.timesheet_has_permission(
                        inactive,
                        user="kiosk@example.com",
                        ptype=permission_type,
                    )
                )
                self.assertFalse(
                    self.permissions.timesheet_has_permission(
                        wrong_company,
                        user="kiosk@example.com",
                        ptype=permission_type,
                    )
                )

        self.assertTrue(
            self.permissions.timesheet_has_permission(
                Doc(), user="kiosk@example.com", ptype="create"
            )
        )
        self.assertTrue(
            self.permissions.timesheet_has_permission(
                own, user="kiosk@example.com", ptype="create"
            )
        )
        self.assertFalse(
            self.permissions.timesheet_has_permission(
                inactive, user="kiosk@example.com", ptype="create"
            )
        )

    def test_kiosk_cannot_mutate_timesheets_outside_allowed_lifecycle(self):
        for method in (
            "before_cancel",
            "before_discard",
            "on_trash",
            "before_rename",
            "before_update_after_submit",
        ):
            with self.subTest(method=method):
                with self.assertRaises(FakePermissionError):
                    self.permissions.deny_kiosk_timesheet_mutation(
                        Doc("EMP-0001"), method, "old", "new", False
                    )

        self.frappe.session.user = "manager@example.com"
        self.permissions.deny_kiosk_timesheet_mutation(
            Doc("EMP-0001"), "before_cancel"
        )

    def test_non_kiosk_users_keep_standard_permissions(self):
        self.frappe.session.user = "manager@example.com"
        self.assertEqual(
            self.permissions.timesheet_query_conditions("manager@example.com"),
            "",
        )
        self.assertTrue(
            self.permissions.timesheet_has_permission(
                Doc("EMP-0004", owner="other@example.com"),
                user="manager@example.com",
                ptype="read",
            )
        )
        self.frappe.local.request = types.SimpleNamespace(
            path="/api/resource/Employee", method="GET"
        )
        self.permissions.before_request()


if __name__ == "__main__":
    unittest.main()
