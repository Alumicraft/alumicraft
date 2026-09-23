import json

import frappe
from frappe.model.document import Document

from alumicraft.bom.engine import ASSEMBLIES, PURPOSES, calculate_totals, project_counts


class VehicleBOMStudy(Document):
    def validate(self):
        internal = getattr(frappe.flags, "vehicle_bom_internal", False)
        previous = self.get_doc_before_save()
        if not internal:
            protected = ("status", "requested_by", "snapshot_json", "decisions_json", "metrics_json", "warnings", "error_message")
            for field in protected:
                before = previous.get(field) if previous else ("Draft" if field == "status" else None)
                if (self.get(field) or "") != (before or ""):
                    frappe.throw("Run state and source evidence can only be changed by the background job.")
            if previous and previous.status != "Draft":
                for field in ("company", "standard_description", "mode", "from_date", "to_date"):
                    if self.has_value_changed(field):
                        frappe.throw("The source configuration is frozen. Create a new study for a different build.")
                before = [(r.project, r.vehicle_count) for r in previous.projects]
                after = [(r.project, r.vehicle_count) for r in self.projects]
                if before != after:
                    frappe.throw("Projects and vehicle counts cannot change after a run starts.")
            if previous and previous.status in ("Queued", "Running"):
                # Prevent user saves from replacing worker checkpoints or partial results.
                frappe.throw("Wait for the background run before editing this study.")
            if not previous or previous.status != "Needs Review":
                if self.materials or self.labor:
                    frappe.throw("Generate the draft before entering review lines.")
            elif previous:
                # Keep evidence immutable while allowing quantity/cost/category review and manual additions.
                self._validate_evidence(previous)

        if self.mode not in ("Historical only", "Jev"):
            frappe.throw("Select Historical only or Jev mode.")
        try:
            project_counts([r.as_dict() for r in self.projects])
        except ValueError as exc:
            frappe.throw(str(exc))
        if self.from_date and self.to_date and str(self.from_date) > str(self.to_date):
            frappe.throw("From Date must not be later than To Date.")
        if not internal:
            frappe.get_doc("Company", self.company).check_permission("read")
            for row in self.projects:
                project = frappe.get_doc("Project", row.project)
                project.check_permission("read")
                if project.company and project.company != self.company:
                    frappe.throw("Every selected project must belong to the study company.")
                if project.project_type != "Build":
                    frappe.throw("Vehicle BOM studies require Build projects.")
        # Historic amounts retain the currency in which they were captured.
        self.currency = (
            json.loads(self.snapshot_json)["currency"] if self.snapshot_json
            else frappe.db.get_value("Company", self.company, "default_currency")
        )
        for row in self.materials:
            if row.assembly not in ASSEMBLIES or row.purpose not in PURPOSES:
                frappe.throw("Select a supported assembly and purchase purpose.")
            if row.review_status == "Approved" and (not row.description or not row.stock_uom):
                frappe.throw("Approved materials require a description and unit of measure.")
        materials, labor = [r.as_dict() for r in self.materials], [r.as_dict() for r in self.labor]
        try:
            totals = calculate_totals(materials, labor, self.material_allowance, self.overhead_allowance, self.target_margin)
        except ValueError as exc:
            frappe.throw(str(exc))
        for originals, calculated in ((self.materials, materials), (self.labor, labor)):
            for original, result in zip(originals, calculated):
                original.amount = result["amount"]
        self.update(totals)

    def _validate_evidence(self, previous):
        for field, immutable in (
            ("materials", ("line_key", "evidence", "confidence", "project_count")),
            ("labor", ("evidence",)),
        ):
            previous_rows = {r.name: r for r in previous.get(field)}
            current_names = {r.name for r in self.get(field)}
            if set(previous_rows) - current_names:
                frappe.throw("Exclude a generated line instead of deleting its evidence.")
            for row in self.get(field):
                old = previous_rows.get(row.name)
                if old:
                    if any(row.get(key) != old.get(key) for key in immutable):
                        frappe.throw("Source evidence and model confidence cannot be edited.")
                elif any(row.get(key) for key in immutable):
                    frappe.throw("Manual additions cannot supply generated evidence or model confidence.")
                elif not row.notes:
                    frappe.throw("Add a note explaining each manually added material or labor line.")

    def on_trash(self):
        if self.status in ("Queued", "Running"):
            frappe.throw("A running study cannot be deleted.")
