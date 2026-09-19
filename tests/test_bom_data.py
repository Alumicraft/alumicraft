import importlib
import sys
import types
import unittest


class FakePermissionError(Exception):
    pass


class FakeFrappe(types.ModuleType):
    def __init__(self):
        super().__init__("frappe")
        self.PermissionError = FakePermissionError
        self.rows = {
            "Company": [],
            "Project": [],
            "Purchase Invoice": [],
            "Purchase Invoice Item": [],
            "Timesheet": [],
            "Timesheet Detail": [],
            "Item": [],
            "Account": [],
        }
        self.calls = []

    @staticmethod
    def _matches(row, filters):
        for field, expected in (filters or {}).items():
            actual = row.get(field)
            if isinstance(expected, (list, tuple)):
                operator = str(expected[0]).lower()
                value = expected[1]
                if operator == "in" and actual not in value:
                    return False
                if operator == ">=" and actual < value:
                    return False
                if operator == "<=" and actual > value:
                    return False
                if operator == "between" and not (value[0] <= actual <= value[1]):
                    return False
            elif actual != expected:
                return False
        return True

    def _query(self, method, doctype, kwargs):
        self.calls.append((method, doctype, kwargs))
        rows = [
            dict(row)
            for row in self.rows.get(doctype, [])
            if self._matches(row, kwargs.get("filters"))
        ]
        fields = kwargs.get("fields") or ["*"]
        if fields == ["*"]:
            selected = rows
        else:
            selected = [{field: row.get(field) for field in fields} for row in rows]
        start = int(kwargs.get("start") or 0)
        length = int(kwargs.get("page_length") or len(selected))
        return selected[start : start + length]

    def get_list(self, doctype, **kwargs):
        return self._query("get_list", doctype, kwargs)

    def get_all(self, doctype, **kwargs):
        return self._query("get_all", doctype, kwargs)


class BomDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_frappe = sys.modules.get("frappe")
        cls.frappe = FakeFrappe()
        sys.modules["frappe"] = cls.frappe
        sys.modules.pop("alumicraft.bom.data", None)
        cls.data = importlib.import_module("alumicraft.bom.data")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("alumicraft.bom.data", None)
        if cls.original_frappe is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = cls.original_frappe

    def setUp(self):
        for rows in self.frappe.rows.values():
            rows.clear()
        self.frappe.calls.clear()
        self.frappe.rows["Company"] = [{"name": "Alumicraft", "default_currency": "USD"}]
        self.frappe.rows["Project"] = [
            {"name": "P-ONE", "company": "Alumicraft", "description": "First"},
            {"name": "P-TWO", "company": "Alumicraft", "description": "Second"},
            {"name": "OTHER", "company": "Other Co", "description": "Hidden"},
        ]

    def test_company_and_project_scope_is_authorized_before_children(self):
        self.frappe.rows["Purchase Invoice"] = [
            {
                "name": "PI-1",
                "company": "Alumicraft",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
            },
            {
                "name": "PI-OTHER",
                "company": "Other Co",
                "docstatus": 1,
                "posting_date": "2026-01-02",
            },
        ]
        self.frappe.rows["Purchase Invoice Item"] = [
            {"name": "PII-1", "parent": "PI-1", "project": "P-ONE", "qty": 1, "amount": 5}
        ]

        snapshot = self.data.collect_snapshot("Alumicraft", ["P-ONE"])

        self.assertEqual(["PII-1"], [row["source_id"] for row in snapshot["purchase_lines"]])
        child_calls = [call for call in self.frappe.calls if call[0] == "get_all"]
        self.assertEqual(1, len(child_calls))
        self.assertEqual(["PI-1"], child_calls[0][2]["filters"]["parent"][1])
        with self.assertRaises(FakePermissionError):
            self.data.collect_snapshot("Alumicraft", ["OTHER"])

    def test_row_project_wins_and_parent_project_is_only_fallback(self):
        self.frappe.rows["Purchase Invoice"] = [
            {
                "name": "PI-1",
                "company": "Alumicraft",
                "project": "P-ONE",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
            },
            {
                "name": "PI-2",
                "company": "Alumicraft",
                "project": "P-TWO",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
            },
        ]
        self.frappe.rows["Purchase Invoice Item"] = [
            {"name": "ROW-OTHER", "parent": "PI-1", "project": "P-TWO", "qty": 1, "amount": 10},
            {"name": "ROW-FALLBACK", "parent": "PI-1", "project": "", "qty": 2, "amount": 20},
            {"name": "ROW-CHILD", "parent": "PI-2", "project": "P-ONE", "qty": 3, "amount": 30},
        ]

        snapshot = self.data.collect_snapshot("Alumicraft", ["P-ONE"])

        self.assertEqual(["ROW-FALLBACK", "ROW-CHILD"], [row["source_id"] for row in snapshot["purchase_lines"]])
        self.assertEqual(["P-ONE", "P-ONE"], [row["project"] for row in snapshot["purchase_lines"]])

    def test_returns_are_signed_and_stock_uom_cost_is_normalized(self):
        self.frappe.rows["Purchase Invoice"] = [
            {
                "name": "PI-RETURN",
                "company": "Alumicraft",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
                "is_return": 1,
                "return_against": "PI-ORIGINAL",
                "supplier": "Supplier A",
            }
        ]
        self.frappe.rows["Purchase Invoice Item"] = [
            {
                "name": "PII-RETURN",
                "parent": "PI-RETURN",
                "project": "P-ONE",
                "item_code": "Bolt",
                "item_name": "Bolt",
                "stock_uom": "Nos",
                "uom": "Box",
                "qty": 2,
                "conversion_factor": 10,
                "base_net_rate": 3,
                "base_net_amount": 60,
                "description": "<b>Returned bolt</b>",
            }
        ]

        line = self.data.collect_snapshot("Alumicraft", ["P-ONE"])["purchase_lines"][0]

        self.assertEqual(-20, line["quantity"])
        self.assertEqual(-60, line["amount"])
        self.assertEqual(3, line["unit_cost"])
        self.assertTrue(line["is_return"])
        self.assertEqual("Returned bolt", line["description"])
        self.assertEqual("PI-ORIGINAL", line["return_against"])

    def test_generic_and_non_expense_rows_are_kept_with_review_flags(self):
        self.frappe.rows["Purchase Invoice"] = [
            {
                "name": "PI-GENERIC",
                "company": "Alumicraft",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
            }
        ]
        self.frappe.rows["Purchase Invoice Item"] = [
            {
                "name": "PII-GENERIC",
                "parent": "PI-GENERIC",
                "project": "P-ONE",
                "item_code": "Part",
                "qty": 1,
                "amount": 25,
                "expense_account": "Stock Asset - AC",
            }
        ]
        self.frappe.rows["Account"] = [
            {"name": "Stock Asset - AC", "root_type": "Asset"}
        ]

        line = self.data.collect_snapshot("Alumicraft", ["P-ONE"])["purchase_lines"][0]

        self.assertIn("generic_item", line["flags"])
        self.assertIn("non_expense_account", line["flags"])
        self.assertIn("financial_account_review", line["flags"])

    def test_timesheet_overlap_and_foreign_currency_labor_use_in_window_rows(self):
        self.frappe.rows["Timesheet"] = [
            {
                "name": "TS-SPANNING",
                "company": "Alumicraft",
                "docstatus": 1,
                "start_date": "2025-12-31",
                "end_date": "2026-01-03",
                "parent_project": "P-ONE",
                "currency": "EUR",
                "exchange_rate": 1.2,
            }
        ]
        self.frappe.rows["Timesheet Detail"] = [
            {
                "name": "TSD-IN",
                "parent": "TS-SPANNING",
                "project": "",
                "from_time": "2026-01-01 09:00:00",
                "hours": 2,
                "costing_amount": 100,
                "description": "In range",
            },
            {
                "name": "TSD-OUT",
                "parent": "TS-SPANNING",
                "project": "",
                "from_time": "2026-01-03 09:00:00",
                "hours": 8,
                "costing_amount": 400,
                "description": "Out of range",
            },
        ]

        snapshot = self.data.collect_snapshot(
            "Alumicraft",
            ["P-ONE"],
            from_date="2026-01-01",
            to_date="2026-01-02",
        )

        self.assertEqual(["TSD-IN"], [row["source_id"] for row in snapshot["labor_lines"]])
        self.assertEqual(120, snapshot["labor_lines"][0]["costing_amount"])
        self.assertNotIn("employee", snapshot["labor_lines"][0])
        timesheet_call = next(
            call for call in self.frappe.calls if call[0] == "get_list" and call[1] == "Timesheet"
        )
        self.assertEqual(["<=", "2026-01-02"], timesheet_call[2]["filters"]["start_date"])
        self.assertEqual([">=", "2026-01-01"], timesheet_call[2]["filters"]["end_date"])

    def test_base_costing_amount_is_preferred_and_missing_exchange_is_unknown(self):
        self.frappe.rows["Timesheet"] = [
            {
                "name": "TS-COST",
                "company": "Alumicraft",
                "docstatus": 1,
                "start_date": "2026-01-01",
                "end_date": "2026-01-01",
                "parent_project": "P-ONE",
                "currency": "EUR",
                "exchange_rate": None,
            }
        ]
        self.frappe.rows["Timesheet Detail"] = [
            {
                "name": "TSD-BASE",
                "parent": "TS-COST",
                "project": "P-ONE",
                "from_time": "2026-01-01 09:00:00",
                "hours": 1,
                "costing_amount": 100,
                "base_costing_amount": 130,
            },
            {
                "name": "TSD-UNKNOWN",
                "parent": "TS-COST",
                "project": "P-ONE",
                "from_time": "2026-01-01 10:00:00",
                "hours": 1,
                "costing_amount": 100,
            },
        ]

        snapshot = self.data.collect_snapshot(
            "Alumicraft", ["P-ONE"], from_date="2026-01-01", to_date="2026-01-01"
        )
        lines = {line["source_id"]: line for line in snapshot["labor_lines"]}

        self.assertEqual(130, lines["TSD-BASE"]["costing_amount"])
        self.assertIsNone(lines["TSD-UNKNOWN"]["costing_amount"])
        self.assertTrue(any("no company-currency labor cost" in warning for warning in snapshot["warnings"]))

    def test_malformed_conversion_factor_does_not_assume_stock_units(self):
        self.frappe.rows["Purchase Invoice"] = [
            {
                "name": "PI-BAD-UOM",
                "company": "Alumicraft",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
            }
        ]
        self.frappe.rows["Purchase Invoice Item"] = [
            {
                "name": "PII-BAD-UOM",
                "parent": "PI-BAD-UOM",
                "project": "P-ONE",
                "uom": "Box",
                "stock_uom": "Nos",
                "qty": 2,
                "conversion_factor": "not-a-number",
                "amount": 20,
            }
        ]

        line = self.data.collect_snapshot("Alumicraft", ["P-ONE"])["purchase_lines"][0]

        self.assertIsNone(line["quantity"])
        self.assertIsNone(line["unit_cost"])
        self.assertIn("invalid_conversion_factor", line["flags"])

    def test_discounted_net_amount_determines_cost_per_stock_unit(self):
        self.frappe.rows["Purchase Invoice"] = [
            {
                "name": "PI-DISCOUNT",
                "company": "Alumicraft",
                "docstatus": 1,
                "posting_date": "2026-01-02",
                "currency": "USD",
            }
        ]
        self.frappe.rows["Purchase Invoice Item"] = [
            {
                "name": "PII-DISCOUNT",
                "parent": "PI-DISCOUNT",
                "project": "P-ONE",
                "stock_qty": 20,
                "qty": 2,
                "conversion_factor": 10,
                "base_net_rate": 5,
                "base_net_amount": 80,
            }
        ]

        line = self.data.collect_snapshot("Alumicraft", ["P-ONE"])["purchase_lines"][0]

        self.assertEqual(4, line["unit_cost"])

    def test_pagination_and_labor_exclude_employee_identity(self):
        original_size = self.data.PAGE_SIZE
        self.data.PAGE_SIZE = 2
        try:
            self.frappe.rows["Purchase Invoice"] = [
                {
                    "name": f"PI-{index}",
                    "company": "Alumicraft",
                    "docstatus": 1,
                    "posting_date": "2026-01-02",
                    "currency": "USD",
                }
                for index in range(5)
            ]
            self.frappe.rows["Purchase Invoice Item"] = [
                {"name": f"PII-{index}", "parent": f"PI-{index}", "project": "P-ONE", "qty": 1, "amount": index}
                for index in range(5)
            ]
            self.frappe.rows["Timesheet"] = [
                {
                    "name": "TS-1",
                    "company": "Alumicraft",
                    "docstatus": 1,
                    "start_date": "2026-01-03",
                    "employee": "EMP-SECRET",
                }
            ]
            self.frappe.rows["Timesheet Detail"] = [
                {
                    "name": "TSD-1",
                    "parent": "TS-1",
                    "project": "P-ONE",
                    "activity_type": "Fabrication",
                    "hours": 4,
                    "costing_amount": 120,
                    "description": "Welding",
                }
            ]

            snapshot = self.data.collect_snapshot("Alumicraft", ["P-ONE"])

            self.assertEqual(5, len(snapshot["purchase_lines"]))
            self.assertEqual("TSD-1", snapshot["labor_lines"][0]["source_id"])
            self.assertNotIn("employee", snapshot["labor_lines"][0])
            self.assertGreaterEqual(
                len([call for call in self.frappe.calls if call[0] == "get_list" and call[1] == "Purchase Invoice"]),
                3,
            )
            self.assertGreaterEqual(
                len([call for call in self.frappe.calls if call[0] == "get_all" and call[1] == "Purchase Invoice Item"]),
                3,
            )
        finally:
            self.data.PAGE_SIZE = original_size


if __name__ == "__main__":
    unittest.main()
