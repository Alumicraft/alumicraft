"""Permission-bound API for the standalone Vehicle BOM portal.

The portal deliberately has a smaller surface than the Desk form.  Every
read is bounded and permission filtered, and writes accept only the fields a
reviewer can change.  Run state, source snapshots, provider decisions, and
generated evidence remain owned by the service/controller.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import frappe

from alumicraft.bom import service
from alumicraft.bom.engine import ASSEMBLIES, PURPOSES


STUDY = "Vehicle BOM Study"
MAX_COMPANIES = 500
MAX_PROJECTS = 100
MAX_STUDIES = 100
MAX_QUERY_LENGTH = 120
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
MAX_PROJECT_ROWS = 100
MAX_MATERIAL_ROWS = 20_000
MAX_LABOR_ROWS = 20_000
MAX_TEXT_LENGTH = 10_000

_MANAGER_ROLES = {"System Manager", "Manufacturing Manager"}

_ROOT_SAVE_FIELDS = {
    "name",
    "modified",
    "title",
    "company",
    "standard_description",
    "mode",
    "from_date",
    "to_date",
    "projects",
    "materials",
    "labor",
    "material_allowance",
    "overhead_allowance",
    "target_margin",
}
_PROJECT_FIELDS = {"project", "vehicle_count"}
_MATERIAL_EDITABLE_FIELDS = {
    "name",
    "assembly",
    "item_code",
    "description",
    "stock_uom",
    "proposed_quantity",
    "unit_cost",
    "cost_known",
    "purpose",
    "review_status",
    "notes",
}
_LABOR_EDITABLE_FIELDS = {
    "name",
    "activity_type",
    "proposed_hours",
    "hourly_cost",
    "cost_known",
    "review_status",
    "notes",
}

_STUDY_RESPONSE_FIELDS = (
    "name",
    "modified",
    "title",
    "company",
    "currency",
    "standard_description",
    "mode",
    "from_date",
    "to_date",
    "status",
    "projects",
    "materials",
    "labor",
    "material_allowance",
    "overhead_allowance",
    "target_margin",
    "material_total",
    "labor_total",
    "estimated_total",
    "suggested_retail",
    "missing_cost_count",
    "pending_review_count",
    "warnings",
    "error_message",
)
_MATERIAL_RESPONSE_FIELDS = (
    "name",
    "line_key",
    "assembly",
    "item_code",
    "description",
    "stock_uom",
    "proposed_quantity",
    "unit_cost",
    "cost_known",
    "amount",
    "purpose",
    "confidence",
    "review_status",
    "project_count",
    "evidence",
    "notes",
)
_LABOR_RESPONSE_FIELDS = (
    "name",
    "activity_type",
    "proposed_hours",
    "hourly_cost",
    "cost_known",
    "amount",
    "review_status",
    "evidence",
    "notes",
)


def require_access(doc: Any | None = None, action: str = "read") -> str:
    """Require a signed-in manufacturing manager and, when supplied, doc access."""

    user = getattr(getattr(frappe, "session", None), "user", None)
    if not user or user == "Guest":
        frappe.throw("Sign in to use the vehicle BOM portal.", frappe.PermissionError)
    if not _MANAGER_ROLES.intersection(set(frappe.get_roles() or [])):
        frappe.throw("A manufacturing or system manager is required.", frappe.PermissionError)
    if doc is not None:
        service._permission(doc, action)
    return user


def _throw(message: str, error: type[Exception] | None = None) -> None:
    frappe.throw(message, error or getattr(frappe, "ValidationError", ValueError))


def _value(row: Any, fieldname: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(fieldname, default)
    getter = getattr(row, "get", None)
    if callable(getter):
        result = getter(fieldname)
        return default if result is None else result
    return getattr(row, fieldname, default)


def _as_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    as_dict = getattr(row, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    return dict(getattr(row, "__dict__", {}))


def _text(value: Any, fieldname: str, *, required: bool = False, max_length: int = 140) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value.strip()
    else:
        _throw(f"{fieldname} must be text.")
    if required and not text:
        _throw(f"{fieldname} is required.")
    if len(text) > max_length:
        _throw(f"{fieldname} is too long.")
    return text


def _strict_keys(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _throw(f"{label} must be an object.")
    if any(not isinstance(key, str) for key in value):
        _throw(f"{label} field names must be text.")
    unknown = set(value) - allowed
    if unknown:
        _throw(f"Unsupported {label} field: {sorted(unknown)[0]}.")
    return dict(value)


def _parse_payload(payload: Any) -> dict[str, Any]:
    try:
        serialized = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, UnicodeError):
        _throw("payload must contain JSON-compatible values.")
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        _throw("payload is too large.")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            _throw("payload must be valid JSON.")
    values = _strict_keys(payload, _ROOT_SAVE_FIELDS, "study")
    _validate_root_values(values)
    return values


def _snapshot_access(doc: Any) -> None:
    snapshot_json = _value(doc, "snapshot_json", "")
    if not snapshot_json:
        return
    try:
        snapshot = json.loads(snapshot_json)
    except (TypeError, ValueError):
        _throw("This study has an invalid saved snapshot.", frappe.PermissionError)
    if not isinstance(snapshot, Mapping):
        _throw("This study has an invalid saved snapshot.", frappe.PermissionError)
    service._check_snapshot_access(snapshot)


def _response_row(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    values = _as_dict(row)
    return {field: values.get(field) for field in fields}


def _study_response(doc: Any) -> dict[str, Any]:
    result = {field: _value(doc, field) for field in _STUDY_RESPONSE_FIELDS}
    result["projects"] = [
        _response_row(row, ("name", "project", "vehicle_count"))
        for row in (_value(doc, "projects", []) or [])
    ]
    result["materials"] = [
        _response_row(row, _MATERIAL_RESPONSE_FIELDS)
        for row in (_value(doc, "materials", []) or [])
    ]
    result["labor"] = [
        _response_row(row, _LABOR_RESPONSE_FIELDS)
        for row in (_value(doc, "labor", []) or [])
    ]
    return result


def _company_row(row: Any) -> dict[str, Any]:
    values = _as_dict(row)
    return {"name": values.get("name"), "currency": values.get("default_currency")}


def _company_is_readable(company: str) -> bool:
    rows = frappe.get_list(
        "Company",
        filters={"name": company},
        fields=["name", "default_currency"],
        limit_page_length=1,
    )
    return bool(rows)


@frappe.whitelist(methods=["GET"])
def bootstrap() -> dict[str, Any]:
    """Return bounded reference data needed to start a portal study."""

    user = require_access()
    companies = frappe.get_list(
        "Company",
        filters={},
        fields=["name", "default_currency"],
        order_by="name asc",
        limit_page_length=MAX_COMPANIES,
    )
    return {
        "user": user,
        "companies": [_company_row(row) for row in companies],
        "assemblies": list(ASSEMBLIES),
        "purposes": list(PURPOSES),
    }


@frappe.whitelist(methods=["GET"])
def search_projects(company: str, query: str = "") -> list[dict[str, Any]]:
    require_access()
    company = _text(company, "company", required=True)
    query = _text(query, "query", max_length=MAX_QUERY_LENGTH)
    if not _company_is_readable(company):
        frappe.throw("Company is not readable or does not exist.", frappe.PermissionError)
    filters: dict[str, Any] = {"company": company, "project_type": "Build"}
    kwargs: dict[str, Any] = {
        "filters": filters,
        "fields": ["name", "project_name", "status"],
        "order_by": "modified desc, name asc",
        "limit_page_length": MAX_PROJECTS,
    }
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        kwargs["or_filters"] = [
            ["Project", "name", "like", f"%{escaped}%"],
            ["Project", "project_name", "like", f"%{escaped}%"],
        ]
    rows = frappe.get_list("Project", **kwargs)
    return [
        {field: _value(row, field) for field in ("name", "project_name", "status")}
        for row in rows
    ]


@frappe.whitelist(methods=["GET"])
def list_studies() -> list[dict[str, Any]]:
    require_access()
    fields = ["name", "title", "status", "company", "currency", "estimated_total", "modified"]
    rows = frappe.get_list(
        STUDY,
        fields=fields,
        order_by="modified desc",
        limit_page_length=MAX_STUDIES,
    )
    return [{field: _value(row, field) for field in fields} for row in rows]


@frappe.whitelist(methods=["GET"])
def get_study(name: str) -> dict[str, Any]:
    require_access()
    name = _text(name, "name", required=True)
    doc = frappe.get_doc(STUDY, name)
    require_access(doc, "read")
    _snapshot_access(doc)
    return _study_response(doc)


def _prepare_child_rows(doc: Any, fieldname: str, value: Any, editable: set[str], label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _throw(f"{label} must be a list.")
    max_rows = MAX_MATERIAL_ROWS if fieldname == "materials" else MAX_LABOR_ROWS
    if len(value) > max_rows:
        _throw(f"{label} contains too many rows.")
    existing = {
        str(_value(row, "name")): _as_dict(row)
        for row in (_value(doc, fieldname, []) or [])
        if _value(row, "name")
    }
    prepared: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for raw in value:
        values = _strict_keys(raw, editable, label)
        row_name = values.get("name")
        _validate_child_values(values, label)
        if row_name not in (None, ""):
            row_name = _text(row_name, f"{label}.name", max_length=140)
            if row_name not in existing:
                _throw(f"Unknown {label} row.")
            if row_name in seen_names:
                _throw(f"Duplicate {label} row.")
            seen_names.add(row_name)
            row = dict(existing[row_name])
            row.update({key: val for key, val in values.items() if key != "name"})
            prepared.append(row)
        else:
            prepared.append({key: val for key, val in values.items() if key != "name"})
    return prepared


def _validate_scalar(value: Any, fieldname: str, *, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, (Mapping, list, tuple, set)):
        _throw(f"{fieldname} must be a scalar value.")


def _validate_text_value(value: Any, fieldname: str, *, max_length: int = MAX_TEXT_LENGTH) -> None:
    if value is None:
        return
    _text(value, fieldname, max_length=max_length)


def _validate_child_values(values: dict[str, Any], label: str) -> None:
    text_fields = {
        "assembly": 140,
        "item_code": 140,
        "description": MAX_TEXT_LENGTH,
        "stock_uom": 140,
        "purpose": 140,
        "review_status": 140,
        "activity_type": MAX_TEXT_LENGTH,
        "notes": MAX_TEXT_LENGTH,
    }
    for fieldname, max_length in text_fields.items():
        if fieldname in values:
            _validate_text_value(values[fieldname], f"{label}.{fieldname}", max_length=max_length)
    for fieldname in (
        "proposed_quantity",
        "unit_cost",
        "cost_known",
        "proposed_hours",
        "hourly_cost",
    ):
        if fieldname in values:
            _validate_scalar(values[fieldname], f"{label}.{fieldname}")


def _validate_root_values(values: dict[str, Any]) -> None:
    for fieldname in ("name", "modified"):
        if fieldname in values:
            _validate_text_value(values[fieldname], fieldname, max_length=140)
    for fieldname in ("title", "company", "standard_description", "mode", "from_date", "to_date"):
        if fieldname in values:
            _validate_text_value(values[fieldname], fieldname)
    for fieldname in ("material_allowance", "overhead_allowance", "target_margin"):
        if fieldname in values:
            _validate_scalar(values[fieldname], fieldname)
    for fieldname, max_rows in (("projects", MAX_PROJECT_ROWS), ("materials", MAX_MATERIAL_ROWS), ("labor", MAX_LABOR_ROWS)):
        if fieldname in values:
            if not isinstance(values[fieldname], list):
                _throw(f"{fieldname} must be a list.")
            if len(values[fieldname]) > max_rows:
                _throw(f"{fieldname} contains too many rows.")


def _apply_payload(doc: Any, payload: dict[str, Any]) -> None:
    for field in ("title", "company", "standard_description", "mode", "from_date", "to_date", "material_allowance", "overhead_allowance", "target_margin"):
        if field in payload:
            setattr(doc, field, payload[field])
    if "projects" in payload:
        if not isinstance(payload["projects"], list):
            _throw("projects must be a list.")
        projects = []
        for raw in payload["projects"]:
            values = _strict_keys(raw, _PROJECT_FIELDS, "projects row")
            values["project"] = _text(values.get("project"), "projects row.project", required=True, max_length=140)
            if "vehicle_count" in values:
                _validate_scalar(values["vehicle_count"], "projects row.vehicle_count")
            projects.append(values)
        doc.set("projects", projects)
    if "materials" in payload:
        doc.set("materials", _prepare_child_rows(doc, "materials", payload["materials"], _MATERIAL_EDITABLE_FIELDS, "materials row"))
    if "labor" in payload:
        doc.set("labor", _prepare_child_rows(doc, "labor", payload["labor"], _LABOR_EDITABLE_FIELDS, "labor row"))


@frappe.whitelist(methods=["POST"])
def save_study(payload: Any) -> dict[str, Any]:
    """Create a Draft or update a study using an optimistic concurrency token."""

    require_access()
    values = _parse_payload(payload)
    name = values.get("name")
    if name:
        name = _text(name, "name", required=True)
        token = values.get("modified")
        if token in (None, ""):
            _throw("modified is required when updating a study.")
        doc = frappe.get_doc(STUDY, name, for_update=True)
        require_access(doc, "write")
        _snapshot_access(doc)
        if str(_value(doc, "modified")) != str(token):
            _throw("This study changed after it was loaded. Reload before saving.")
        _apply_payload(doc, values)
        doc.save()
    else:
        if values.get("modified") not in (None, ""):
            _throw("modified is only valid when updating a study.")
        doc = frappe.get_doc({"doctype": STUDY, "status": "Draft"})
        _apply_payload(doc, values)
        # insert() performs Frappe's create permission checks.  This explicit
        # check also makes the boundary clear for callers and test doubles.
        check_permission = getattr(doc, "check_permission", None)
        if callable(check_permission):
            check_permission("create")
        doc.insert()
    return _study_response(doc)
