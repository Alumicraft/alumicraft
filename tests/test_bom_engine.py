import json
import unittest

from alumicraft.bom.engine import build_draft, calculate_totals, classification_rows, project_counts


def purchase(source, project, qty, code="SHOCK", **kwargs):
    return dict(source_id=source, invoice="PI-" + source, project=project, quantity=qty,
                item_code=code, description="Shock absorber", stock_uom="Nos", unit_cost=100,
                amount=qty * 100 if qty is not None else None, posting_date="2026-08-01", **kwargs)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.projects = [{"project": "A", "vehicle_count": 1}, {"project": "B", "vehicle_count": 2}]

    def test_vehicle_quantities_are_normalized_not_summed(self):
        result = build_draft({"purchase_lines": [purchase("1", "A", 4), purchase("2", "B", 8)]}, self.projects)
        row = result["materials"][0]
        self.assertEqual(row["proposed_quantity"], 4)
        self.assertEqual(row["amount"], 400)
        self.assertEqual(row["review_status"], "Pending")
        self.assertEqual(row["project_count"], 2)

    def test_returns_are_netted_within_project(self):
        result = build_draft({"purchase_lines": [purchase("1", "A", 6), purchase("2", "A", -2)]}, self.projects)
        row = result["materials"][0]
        self.assertEqual(row["proposed_quantity"], 4)
        self.assertEqual(len(json.loads(row["evidence"])["sources"]), 2)

    def test_absent_project_is_not_assumed_zero_consumption(self):
        result = build_draft({"purchase_lines": [purchase("1", "A", 4)]}, self.projects)
        row = result["materials"][0]
        self.assertEqual(row["proposed_quantity"], 4)
        self.assertIn("absence is not proof", row["notes"])

    def test_generic_codes_do_not_merge_different_components(self):
        a, b = purchase("1", "A", 1, "Part"), purchase("2", "A", 1, "Part")
        b["description"] = "Transmission"
        result = build_draft({"purchase_lines": [a, b]}, self.projects)
        self.assertEqual(len(result["materials"]), 2)
        self.assertTrue(all(r["item_code"] is None for r in result["materials"]))

    def test_generic_flag_overrides_nonstandard_placeholder_code(self):
        a, b = purchase("1", "A", 1, "PLACEHOLDER"), purchase("2", "A", 1, "PLACEHOLDER")
        a["flags"] = b["flags"] = ["generic_item"]
        b["description"] = "Transmission"
        self.assertEqual(len(build_draft({"purchase_lines": [a, b]}, self.projects)["materials"]), 2)

    def test_unknown_quantity_remains_reviewable(self):
        row = build_draft({"purchase_lines": [purchase("1", "A", None)]}, self.projects)["materials"][0]
        self.assertEqual(row["review_status"], "Pending")
        self.assertIn("quantity is unknown", row["notes"])

    def test_model_spare_is_not_silently_excluded(self):
        rows = {"purchase_lines": [purchase("1", "A", 1)]}
        decisions = {"1": {"assembly": "Suspension", "purpose": "Spare", "confidence": 0.99}}
        row = build_draft(rows, self.projects, decisions)["materials"][0]
        self.assertEqual(row["review_status"], "Pending")
        self.assertEqual(row["purpose"], "Spare")
        self.assertEqual(row["amount"], 100)

    def test_latest_historical_cost_not_average_across_revisions(self):
        a, b = purchase("1", "A", 1), purchase("2", "B", 2)
        b.update(posting_date="2026-09-01", unit_cost=150)
        row = build_draft({"purchase_lines": [a, b]}, self.projects)["materials"][0]
        self.assertEqual(row["unit_cost"], 150)
        self.assertIn("Historical unit cost", row["notes"])

    def test_missing_labor_cost_is_not_averaged_as_free_work(self):
        rows = [dict(source_id="T1", project="A", hours=2, costing_amount=100, activity_type="Weld"),
                dict(source_id="T2", project="B", hours=4, costing_amount=0, activity_type="Weld")]
        row = build_draft({"labor_lines": rows}, self.projects)["labor"][0]
        self.assertEqual(row["proposed_hours"], 2)
        self.assertEqual(row["cost_known"], 0)
        self.assertEqual(row["hourly_cost"], 0)

    def test_duplicate_and_out_of_scope_evidence_rejected(self):
        row = purchase("1", "A", 4)
        with self.assertRaises(ValueError):
            build_draft({"purchase_lines": [row, row]}, self.projects)
        with self.assertRaises(ValueError):
            build_draft({"purchase_lines": [purchase("2", "SECRET", 4)]}, self.projects)

    def test_classification_payload_excludes_financial_personal_fields(self):
        row = purchase("1", "A", 4)
        row.update(employee="Private", supplier="Private", unit_cost=12000)
        payload = classification_rows({"purchase_lines": [row]})[0]
        self.assertNotIn("employee", payload)
        self.assertNotIn("supplier", payload)
        self.assertNotIn("unit_cost", payload)

    def test_cost_math_margin_review_and_exclusion(self):
        material = dict(proposed_quantity=4, unit_cost=100, cost_known=1, review_status="Approved")
        labor = dict(proposed_hours=2, hourly_cost=50, cost_known=1, review_status="Approved")
        result = calculate_totals([material], [labor], 50, 150, 30)
        self.assertEqual(result["estimated_total"], 700)
        self.assertEqual(result["suggested_retail"], 1000)
        material["review_status"] = "Pending"
        self.assertEqual(calculate_totals([material], [labor])["suggested_retail"], 0)
        material["review_status"] = "Excluded"
        self.assertEqual(calculate_totals([material], [labor])["material_total"], 0)

    def test_invalid_counts_and_nonfinite_costs_rejected(self):
        for count in (0, -1, 1.5, "NaN"):
            with self.assertRaises(ValueError):
                project_counts([{"project": "A", "vehicle_count": count}])
        with self.assertRaises(ValueError):
            calculate_totals([], [], target_margin=100)
        with self.assertRaises(ValueError):
            calculate_totals([{"proposed_quantity": 1, "unit_cost": "Infinity"}], [])


if __name__ == "__main__":
    unittest.main()
