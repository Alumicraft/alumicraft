"""Deterministic reconstruction of procurement evidence, never an as-built BOM."""

import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from statistics import median

ASSEMBLIES = (
    "Chassis and fabrication", "Suspension", "Drivetrain", "Brakes and steering",
    "Electrical", "Cooling and fuel", "Interior and body", "Wheels and tires",
    "Consumables", "Unknown",
)
PURPOSES = ("Standard", "Custom", "Spare", "Rework", "Unknown")
GENERIC_ITEMS = {"part", "parts", "misc", "miscellaneous", "expense", "expenses", "item-not-available", "quickbooks expense line"}


def number(value, default=0):
    if value is None or value == "":
        return Decimal(str(default))
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("A quantity, cost, or rate is not a valid number.") from None
    if not result.is_finite():
        raise ValueError("Quantities, costs, and rates must be finite numbers.")
    return result


def project_counts(projects):
    counts = {}
    for row in projects:
        project = row.get("project")
        count = number(row.get("vehicle_count"))
        if not project or project in counts or count <= 0 or count != count.to_integral_value():
            raise ValueError("Select unique projects with a positive whole number of vehicles each.")
        counts[project] = count
    if not counts:
        raise ValueError("Select at least one representative project.")
    return counts


def _normalized(value):
    return " ".join(str(value or "").casefold().split())


def _identity(row):
    code = str(row.get("item_code") or "").strip()
    uom = row.get("stock_uom") or ""
    if code and _normalized(code) not in GENERIC_ITEMS and "generic_item" not in row.get("flags", []):
        return ("item", code, uom)
    # A generic ERP item is not a component identity. Missing descriptions never merge.
    description = _normalized(row.get("description") or row.get("item_name"))
    if not description or description in GENERIC_ITEMS:
        return ("unresolved", str(row["source_id"]), uom)
    return ("description", _normalized(row.get("supplier")), description, uom)


def classification_rows(snapshot):
    """Only the minimum textual fields needed for classification leave ERPNext."""
    return [
        {
            "id": row["source_id"],
            "item_code": row.get("item_code") or "",
            "item_name": row.get("item_name") or "",
            "description": row.get("description") or "",
            "item_group": row.get("item_group") or "",
            "project": row.get("project") or "",
        }
        for row in snapshot.get("purchase_lines", [])
    ]


def build_draft(snapshot, projects, decisions=None):
    counts = project_counts(projects)
    decisions = decisions or {}
    groups = defaultdict(list)
    seen = set()
    warnings = list(snapshot.get("warnings") or [])
    for row in snapshot.get("purchase_lines", []):
        source_id = row.get("source_id")
        if not source_id or source_id in seen:
            raise ValueError("Purchase snapshot contains missing or duplicate source IDs.")
        seen.add(source_id)
        if row.get("project") not in counts:
            raise ValueError("Purchase snapshot contains a project outside this study.")
        groups[_identity(row)].append(row)

    materials = []
    for identity, rows in sorted(groups.items()):
        by_project = defaultdict(Decimal)
        for row in rows:
            by_project[row["project"]] += number(row.get("quantity"))
        observed = [quantity / counts[p] for p, quantity in by_project.items() if quantity > 0]
        notes = ["Purchased quantities require builder confirmation; they may include spares or stock."]
        unresolved_quantity = any(row.get("quantity") is None for row in rows)
        if unresolved_quantity:
            notes.append("At least one source quantity is unknown; enter the standard quantity manually.")
        if not observed:
            proposed = Decimal(0)
            notes.append("No positive net quantity remains after returns; excluded from the draft total.")
        else:
            proposed = median(observed)
        if len(set(observed)) > 1:
            notes.append("Observed quantities per vehicle differ between projects.")
        if len(observed) < len(counts):
            notes.append("Not observed as a positive net purchase in every selected project; absence is not proof of non-use.")
        if any(q < 0 for q in by_project.values()):
            notes.append("A project has net returns. Check date limits and the original purchase.")
        if identity[0] != "item":
            notes.append("Generic or missing item code: matched only by supplier and exact normalized description.")
        if not identity[-1]:
            notes.append("Unit of measure is missing.")
        for row in rows:
            notes.extend(str(flag) for flag in row.get("flags", []))

        # The latest observed positive purchase supplies a historical cost, not a current quote.
        prices = [r for r in rows if number(r.get("quantity")) > 0 and r.get("unit_cost") is not None]
        prices.sort(key=lambda r: (str(r.get("posting_date") or ""), str(r["source_id"])))
        price_row = prices[-1] if prices else None
        price = number(price_row["unit_cost"]) if price_row else Decimal(0)
        known = price > 0
        if not known:
            notes.append("Unit cost is missing, zero, or negative; enter an approved cost or allowance.")
            price = Decimal(0)
        elif price_row:
            notes.append("Historical unit cost from {0} ({1}); verify before pricing retail.".format(
                price_row["invoice"], price_row.get("posting_date", "unknown date")))

        classified = [decisions.get(r["source_id"], {}) for r in rows]
        assembly_values = {d.get("assembly", "Unknown") for d in classified}
        purpose_values = {d.get("purpose", "Unknown") for d in classified}
        assembly = next(iter(assembly_values)) if len(assembly_values) == 1 else "Unknown"
        purpose = next(iter(purpose_values)) if len(purpose_values) == 1 else "Unknown"
        if assembly not in ASSEMBLIES:
            assembly = "Unknown"
        if purpose not in PURPOSES:
            purpose = "Unknown"
        confidence = min([float(d.get("confidence") or 0) for d in classified] or [0])
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            confidence = 0
        if len(assembly_values) > 1 or len(purpose_values) > 1:
            notes.append("AI suggestions conflict between source lines.")
        if purpose in ("Custom", "Spare", "Rework"):
            notes.append("Suggested {0}: review before including or excluding.".format(purpose.lower()))
        representative = rows[0]
        evidence = {
            "quantity_basis": "Median positive net purchased stock quantity per vehicle across observed projects",
            "net_quantity_by_project": {p: float(q) for p, q in by_project.items()},
            "vehicle_counts": {p: float(counts[p]) for p in by_project},
            "sources": [{k: r.get(k) for k in (
                "source_id", "invoice", "posting_date", "project", "quantity", "stock_uom",
                "unit_cost", "amount", "is_return", "return_against",
            )} for r in rows],
        }
        materials.append({
            "line_key": hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
            "assembly": assembly,
            "item_code": representative.get("item_code") if identity[0] == "item" else None,
            "description": representative.get("description") or representative.get("item_name") or "Unidentified purchase",
            "stock_uom": representative.get("stock_uom") or None,
            "proposed_quantity": float(proposed), "unit_cost": float(price), "cost_known": int(known),
            "amount": float(proposed * price), "purpose": purpose, "confidence": confidence,
            "review_status": "Pending" if observed or unresolved_quantity else "Excluded", "project_count": len(observed),
            "evidence": json.dumps(evidence, ensure_ascii=False, default=str),
            "notes": "\n".join(dict.fromkeys(notes)),
        })

    activities = defaultdict(list)
    seen = set()
    for row in snapshot.get("labor_lines", []):
        if not row.get("source_id") or row["source_id"] in seen:
            raise ValueError("Labor snapshot contains missing or duplicate source IDs.")
        seen.add(row["source_id"])
        if row.get("project") not in counts:
            raise ValueError("Labor snapshot contains a project outside this study.")
        if number(row.get("hours")) < 0:
            raise ValueError("Labor snapshot contains negative hours.")
        activities[row.get("activity_type") or "Unspecified activity"].append(row)
    labor = []
    for activity, rows in sorted(activities.items()):
        hours_by_project = defaultdict(Decimal)
        total_hours, total_cost = Decimal(0), Decimal(0)
        cost_complete = True
        for row in rows:
            hours = number(row.get("hours"))
            hours_by_project[row["project"]] += hours
            if hours > 0:
                total_hours += hours
                cost = number(row.get("costing_amount"))
                cost_complete = cost_complete and cost > 0
                total_cost += max(cost, Decimal(0))
        observed = [h / counts[p] for p, h in hours_by_project.items() if h > 0]
        proposed = median(observed) if observed else Decimal(0)
        rate = total_cost / total_hours if cost_complete and total_hours else Decimal(0)
        labor.append({
            "activity_type": activity, "proposed_hours": float(proposed), "hourly_cost": float(rate),
            "cost_known": int(cost_complete and rate > 0), "amount": float(proposed * rate),
            "review_status": "Pending", "evidence": json.dumps(rows, ensure_ascii=False, default=str),
            "notes": "Median observed hours per vehicle. Review development/rework and missing project activity."
                     + (" Labor cost is incomplete; set an approved hourly cost." if not cost_complete or not rate else ""),
        })
    if not materials:
        warnings.append("No project-linked submitted purchase lines were found. An empty result does not mean zero material cost.")
    if not labor:
        warnings.append("No project-linked submitted labor entries were found. An empty result does not mean zero labor cost.")
    warnings.append("Coverage of the physical vehicle is unknown. Inventory withdrawals, fabricated components and consumables may be missing.")
    return {"materials": materials, "labor": labor, "warnings": list(dict.fromkeys(warnings))}


def calculate_totals(materials, labor, material_allowance=0, overhead_allowance=0, target_margin=0):
    material_total = Decimal(0)
    labor_total = Decimal(0)
    missing, pending = 0, 0
    for rows, quantity_field, rate_field, kind in (
        (materials, "proposed_quantity", "unit_cost", "materials"),
        (labor, "proposed_hours", "hourly_cost", "labor"),
    ):
        for row in rows:
            quantity, rate = number(row.get(quantity_field)), number(row.get(rate_field))
            if quantity < 0 or rate < 0:
                raise ValueError("Proposed quantities, hours, and costs cannot be negative.")
            status = row.get("review_status") or "Pending"
            if status not in ("Pending", "Approved", "Excluded"):
                raise ValueError("Invalid review status.")
            amount = quantity * rate if status != "Excluded" else Decimal(0)
            row["amount"] = float(amount)
            if status == "Excluded":
                continue
            if not row.get("cost_known"):
                missing += 1
            pending += status == "Pending"
            if kind == "materials":
                material_total += amount
            else:
                labor_total += amount
    allowance, overhead, margin = map(number, (material_allowance, overhead_allowance, target_margin))
    if allowance < 0 or overhead < 0 or not 0 <= margin < 100:
        raise ValueError("Allowances must be nonnegative and target margin must be from 0 to less than 100 percent.")
    total = material_total + labor_total + allowance + overhead
    return {
        "material_total": float(material_total), "labor_total": float(labor_total),
        "estimated_total": float(total), "missing_cost_count": missing, "pending_review_count": pending,
        # No retail recommendation for an unreviewed or empty evidence set.
        "suggested_retail": float(total / (1 - margin / 100))
        if not missing and not pending
        and any(r.get("review_status") != "Excluded" for r in materials)
        and any(r.get("review_status") != "Excluded" for r in labor) else 0,
    }
