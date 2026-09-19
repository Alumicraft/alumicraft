"""Frappe orchestration: immutable evidence, resumable decisions, editable draft."""

import csv
import io
import json
import time
from contextlib import contextmanager

import frappe

from alumicraft.bom.engine import build_draft, classification_rows, project_counts

STUDY = "Vehicle BOM Study"


@contextmanager
def internal_update():
    previous = getattr(frappe.flags, "vehicle_bom_internal", False)
    frappe.flags.vehicle_bom_internal = True
    try:
        yield
    finally:
        frappe.flags.vehicle_bom_internal = previous


def _save(doc):
    with internal_update():
        doc.save()


def _permission(doc, action="write"):
    if frappe.session.user == "Guest" or not set(frappe.get_roles()).intersection(
        {"System Manager", "Manufacturing Manager"}
    ):
        frappe.throw("A manufacturing or system manager is required.", frappe.PermissionError)
    doc.check_permission(action)


def _json(value, default):
    return json.loads(value) if value else default


def _provider_config():
    settings = frappe.get_single("Vehicle BOM Settings")
    if not settings.enabled:
        frappe.throw("Enable Jev in Vehicle BOM Settings first, or use Historical only mode.")
    api_key = settings.get_password("api_key", raise_exception=False)
    if not api_key:
        frappe.throw("A TypeSafe API key is required in Vehicle BOM Settings.")
    return {
        "api_key": api_key, "model": settings.model or "jev-latest",
        "batch_size": int(settings.batch_size or 20), "concurrency": int(settings.concurrency or 4),
        "max_requests": int(settings.max_requests or 500), "timeout": int(settings.timeout or 30),
        "confidence_threshold": float(settings.confidence_threshold if settings.confidence_threshold is not None else 0.9),
    }


@frappe.whitelist(methods=["POST"])
def start(name):
    doc = frappe.get_doc(STUDY, name, for_update=True)
    _permission(doc)
    if doc.status not in ("Draft", "Failed"):
        frappe.throw("This study is already running or has a draft. Create a new study to rebuild it.")
    project_counts([row.as_dict() for row in doc.projects])
    if doc.mode == "Jev":
        _provider_config()  # Validate setup before queueing. The key is never put in the job payload.
    if doc.materials or doc.labor:
        frappe.throw("Start from an empty study so existing review edits are preserved.")
    doc.status = "Queued"
    doc.requested_by = frappe.session.user
    doc.error_message = ""
    _save(doc)
    frappe.enqueue(
        "alumicraft.bom.service.run", queue="long", timeout=7200,
        enqueue_after_commit=True, name=doc.name,
        job_id="vehicle-bom-" + doc.name, deduplicate=True,
    )
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def recover(name):
    from frappe.utils.background_jobs import is_job_enqueued

    doc = frappe.get_doc(STUDY, name, for_update=True)
    _permission(doc)
    if doc.status not in ("Queued", "Running"):
        frappe.throw("Only an interrupted queued or running study needs recovery.")
    if is_job_enqueued("vehicle-bom-" + doc.name):
        frappe.throw("The worker job is still queued or running. Wait for it to finish.")
    doc.status = "Failed"
    doc.error_message = "The worker job was interrupted. Resume will reuse the saved snapshot and completed decisions."
    _save(doc)
    return {"name": doc.name, "status": doc.status}


def run(name):
    """Only invoked by the worker, with a DB lock ensuring one active run per study."""
    doc = frappe.get_doc(STUDY, name, for_update=True)
    if doc.status != "Queued":
        return
    previous_user = frappe.session.user
    frappe.set_user(doc.requested_by)
    cache = {}
    try:
        _permission(doc)
        doc.status = "Running"
        _save(doc)
        frappe.db.commit()

        if doc.snapshot_json:
            snapshot = _json(doc.snapshot_json, {})
        else:
            from alumicraft.bom.data import collect_snapshot

            snapshot = collect_snapshot(
                doc.company, [row.project for row in doc.projects], doc.from_date, doc.to_date,
            )
            doc.snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
            doc.currency = snapshot["currency"]
            _save(doc)
            frappe.db.commit()

        # Recheck access on resume rather than relying on permissions at snapshot time.
        _check_snapshot_access(snapshot)
        state = _json(doc.decisions_json, {})
        cache = state.get("cache", {})
        decisions = {}
        metrics = {"mode": doc.mode, "purchase_lines": len(snapshot.get("purchase_lines", [])),
                   "labor_lines": len(snapshot.get("labor_lines", []))}
        warnings = []
        if doc.mode == "Jev":
            from alumicraft.bom.jev import classify_rows

            config = _provider_config()
            metrics["requested_model"] = config["model"]
            checkpoint = [time.monotonic(), 0]

            def persist(key, decision):
                cache[key] = decision
                checkpoint[1] += 1
                if checkpoint[1] >= 100 or time.monotonic() - checkpoint[0] >= 3:
                    doc.decisions_json = json.dumps({"cache": cache}, ensure_ascii=False)
                    _save(doc)
                    frappe.db.commit()
                    checkpoint[:] = [time.monotonic(), 0]

            result = classify_rows(
                classification_rows(snapshot), doc.standard_description, config,
                cache=cache, on_result=persist,
            )
            decisions = result["decisions"]
            cache.update(result.get("cache", {}))
            metrics.update(result.get("metrics", {}))
            warnings.extend(result.get("warnings", []))
            doc.decisions_json = json.dumps({"cache": cache}, ensure_ascii=False)
            doc.metrics_json = json.dumps(metrics, indent=2)
            doc.warnings = "\n".join(dict.fromkeys(warnings))
            _save(doc)
            frappe.db.commit()
            if result.get("incomplete"):
                raise RuntimeError("Classification incomplete")
        draft = build_draft(snapshot, [row.as_dict() for row in doc.projects], decisions)
        doc.set("materials", draft["materials"])
        doc.set("labor", draft["labor"])
        doc.decisions_json = json.dumps({"cache": cache, "decisions": decisions}, ensure_ascii=False)
        doc.metrics_json = json.dumps(metrics, indent=2)
        doc.warnings = "\n".join(dict.fromkeys(draft["warnings"] + warnings))
        doc.status = "Needs Review"
        doc.error_message = ""
        _save(doc)
        frappe.db.commit()
    except Exception as exc:
        frappe.db.rollback()
        # No exception body is persisted: provider errors may echo source data or credentials.
        doc = frappe.get_doc(STUDY, name, for_update=True)
        doc.status = "Failed"
        doc.error_message = "The run stopped ({0}). Check source access and provider settings, then Resume. Completed decisions are retained.".format(type(exc).__name__)
        if cache:
            doc.decisions_json = json.dumps({"cache": cache}, ensure_ascii=False)
        # Failure state must survive a revoked source permission, but only the worker writes it.
        with internal_update():
            doc.save(ignore_permissions=True)
        frappe.db.commit()
    finally:
        frappe.set_user(previous_user)


def _check_snapshot_access(snapshot):
    """Source documents must remain readable, including after a failed run resumes."""
    sources = {
        "Purchase Invoice": {row["invoice"] for row in snapshot.get("purchase_lines", [])},
        "Timesheet": {row["timesheet"] for row in snapshot.get("labor_lines", [])},
        "Company": {snapshot["company"]},
        "Project": {p if isinstance(p, str) else p["name"] for p in snapshot.get("projects", [])},
    }
    for doctype, names in sources.items():
        names = sorted(names)
        for offset in range(0, len(names), 500):
            batch = names[offset:offset + 500]
            readable = frappe.get_list(doctype, filters={"name": ["in", batch]}, fields=["name"], limit_page_length=500)
            if {r["name"] for r in readable} != set(batch):
                frappe.throw("Some saved source documents are no longer readable.", frappe.PermissionError)


def _cell(value):
    # Invoice descriptions are untrusted spreadsheet text.
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@frappe.whitelist()
def export_csv(name):
    doc = frappe.get_doc(STUDY, name)
    _permission(doc, "read")
    if doc.status != "Needs Review":
        frappe.throw("Wait for the draft before exporting.")
    _check_snapshot_access(_json(doc.snapshot_json, {}))
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["Type", "Assembly / activity", "Item", "Description", "Quantity / hours", "Unit", "Unit cost", "Amount", "Currency", "Cost known", "Review", "Purpose", "Notes", "Evidence"])
    for row in doc.materials:
        writer.writerow([_cell(v) for v in (
            "Material", row.assembly, row.item_code, row.description, row.proposed_quantity,
            row.stock_uom, row.unit_cost, row.amount, doc.currency, row.cost_known,
            row.review_status, row.purpose, row.notes, row.evidence,
        )])
    for row in doc.labor:
        writer.writerow([_cell(v) for v in (
            "Labor", row.activity_type, "", "", row.proposed_hours, "Hour", row.hourly_cost,
            row.amount, doc.currency, row.cost_known, row.review_status, "", row.notes, row.evidence,
        )])
    writer.writerow([])
    writer.writerow(["Cost worksheet (historical evidence; physical coverage unknown)", _cell(doc.title)])
    for label, value in (
        ("Materials", doc.material_total), ("Labor", doc.labor_total),
        ("Material allowance", doc.material_allowance), ("Overhead allowance", doc.overhead_allowance),
        ("Estimated cost", doc.estimated_total), ("Target margin percent", doc.target_margin),
        ("Retail at selected margin (0 = unavailable)", doc.suggested_retail),
        ("Missing costs", doc.missing_cost_count), ("Pending review", doc.pending_review_count),
        ("Warnings", doc.warnings),
    ):
        writer.writerow([label, _cell(value)])
    frappe.response["filename"] = doc.name + ".csv"
    frappe.response["filecontent"] = "\ufeff" + out.getvalue()
    frappe.response["type"] = "download"
    frappe.response["content_type"] = "text/csv; charset=utf-8"
