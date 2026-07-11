"""Server-side permissions for Alumicraft kiosk accounts.

The browser kiosk guard is only a navigation aid. This module is the data
boundary: kiosk users cannot read Employee or User documents and can only
create, read, update, and submit Timesheets for their one linked employee.

The functions here are intentionally hook-ready. See the module tests and the
handoff notes for the Frappe v16 hook mappings.
"""

import json
import re
from urllib.parse import unquote

import frappe
from frappe import _

from alumicraft.api.boot import is_kiosk_user


PROTECTED_DOCTYPES = frozenset(("Employee", "User"))
TIMESHEET_DOCTYPE = "Timesheet"
RELEVANT_DOCTYPES = PROTECTED_DOCTYPES | frozenset((TIMESHEET_DOCTYPE,))
KIOSK_EMPLOYEE_FIELDS = ("name", "company", "employee_name", "department")

ALLOWED_TIMESHEET_PERMISSION_TYPES = frozenset(
    ("create", "read", "select", "write", "submit")
)

REPORT_AND_EXPORT_METHODS = frozenset(
    (
        "frappe.core.doctype.data_export.exporter.export_data",
        "frappe.desk.query_report.export_query",
        "frappe.desk.query_report.run",
        "frappe.desk.reportview.export_query",
        "frappe.desk.reportview.save_report",
        "frappe.utils.print_format.download_multi_pdf",
        "frappe.utils.print_format.download_multi_pdf_async",
        "frappe.utils.print_format.download_pdf",
        "frappe.utils.print_format.print_by_server",
        "frappe.utils.print_format.report_to_pdf",
    )
)

SENSITIVE_DATA_METHODS = frozenset(
    (
        "frappe.desk.form.load.get_user_info_for_viewers",
        "frappe.desk.search.get_names_for_mentions",
    )
)

TIMESHEET_COLLECTION_METHODS = frozenset(
    (
        "frappe.client.get_count",
        "frappe.client.get_list",
        "frappe.client.get_value",
        "frappe.client.get_values",
        "frappe.desk.reportview.get",
        "frappe.desk.reportview.get_count",
        "frappe.desk.reportview.get_filter_dashboard_data",
        "frappe.desk.reportview.get_list",
        "frappe.desk.reportview.get_sidebar_stats",
        "frappe.desk.reportview.get_stats",
        "frappe.desk.search.search_link",
    )
)

DOCTYPE_ARGUMENT_KEYS = frozenset(
    (
        "doctype",
        "document_type",
        "dt",
        "link_doctype",
        "parent_doctype",
        "reference_doctype",
        "target_doctype",
    )
)

QUERY_ARGUMENT_KEYS = (
    "fields",
    "filters",
    "or_filters",
    "order_by",
    "group_by",
    "aggregate_on_doctype",
    "aggregate_on_field",
)

_DOCUMENT_API_RE = re.compile(
    r"^/api/(?:(?:v\d+)/)?(?:document|resource)/([^/?]+)(?:/([^?]+))?/?$",
    re.IGNORECASE,
)
_METHOD_API_RE = re.compile(
    r"^/api/(?:(?:v\d+)/)?method/(.+?)/?$",
    re.IGNORECASE,
)


def _throw_permission(message):
    frappe.throw(message, frappe.PermissionError)


def _document_value(doc, fieldname):
    getter = getattr(doc, "get", None)
    if callable(getter):
        return getter(fieldname)
    return getattr(doc, fieldname, None)


def _row_value(row, fieldname):
    if isinstance(row, dict):
        return row.get(fieldname)

    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(fieldname)

    return getattr(row, fieldname, None)


def _linked_employee_records(user):
    if not user or user in ("Administrator", "Guest"):
        return []

    return list(
        frappe.get_all(
            "Employee",
            filters={"user_id": user, "status": "Active"},
            fields=list(KIOSK_EMPLOYEE_FIELDS),
            order_by="name asc",
            limit_page_length=2,
        )
        or []
    )


def get_kiosk_timesheet_defaults(user=None):
    """Return only the Employee values that must appear on a Timesheet."""

    user = user or frappe.session.user
    employees = _linked_employee_records(user)
    if len(employees) != 1:
        return {}

    employee = employees[0]
    employee_name = _row_value(employee, "name")
    if not employee_name:
        return {}

    return {
        "employee": employee_name,
        "company": _row_value(employee, "company"),
        "employee_name": _row_value(employee, "employee_name"),
        "department": _row_value(employee, "department"),
        "user": user,
    }


def get_kiosk_employee(user=None):
    """Return the sole active Employee linked to a kiosk login, else ``None``.

    Failing closed when there are zero or multiple links prevents an accidental
    association from granting access to the wrong person's Timesheets.
    """

    return get_kiosk_timesheet_defaults(user).get("employee")


def deny_protected_doctype_query(user, doctype=None):
    """Stop list/select queries for Employee and User kiosk records entirely."""

    if is_kiosk_user(user):
        _throw_permission(
            _("Kiosk accounts cannot access Employee or User records.")
        )
    return ""


def protected_doctype_has_permission(
    doc, user=None, ptype=None, debug=False
):
    """Deny every document-level Employee/User permission for kiosk users."""

    return not is_kiosk_user(user)


def timesheet_query_conditions(user, doctype=None):
    """Restrict kiosk list queries to Timesheets for their linked employee."""

    if not is_kiosk_user(user):
        return ""

    defaults = get_kiosk_timesheet_defaults(user)
    employee = defaults.get("employee")
    if not employee:
        return "1 = 0"

    escaped_employee = frappe.db.escape(employee, percent=False)
    return "`tabTimesheet`.`employee` = {0}".format(escaped_employee)


def timesheet_has_permission(doc, user=None, ptype=None, debug=False):
    """Allow only the narrow Timesheet lifecycle needed by a kiosk user."""

    if not is_kiosk_user(user):
        # Frappe v16 requires permission hooks to explicitly return True when
        # they do not deny access.
        return True

    employee = get_kiosk_employee(user)
    if not employee:
        return False

    document_employee = _document_value(doc, "employee")

    # Frappe checks create permission before before_validate runs. A blank
    # employee is safe here because validate_kiosk_timesheet fills it in.
    if ptype == "create" and not document_employee:
        return True

    if ptype is not None and ptype not in ALLOWED_TIMESHEET_PERMISSION_TYPES:
        return False

    return document_employee == employee


def validate_kiosk_timesheet(doc, method=None):
    """Bind every kiosk Timesheet write to the login's one Employee record."""

    user = frappe.session.user
    if not is_kiosk_user(user):
        return

    defaults = get_kiosk_timesheet_defaults(user)
    employee = defaults.get("employee")
    if not employee:
        _throw_permission(
            _(
                "This kiosk login must be linked to exactly one active Employee "
                "before it can save a Timesheet."
            )
        )

    document_employee = _document_value(doc, "employee")
    if not document_employee:
        setattr(doc, "employee", employee)
    elif document_employee != employee:
        _throw_permission(
            _(
                "This kiosk login can only save Timesheets for its assigned "
                "employee."
            )
        )

    # Treat the client values only as presentation defaults. Re-apply the
    # authoritative Employee-derived values on every write so a crafted request
    # cannot change company, department, employee name, or owning user.
    for fieldname, value in defaults.items():
        setattr(doc, fieldname, value)


def deny_kiosk_timesheet_mutation(doc, method=None, *args, **kwargs):
    """Block cancel, delete, and after-submit edits from kiosk sessions."""

    if is_kiosk_user():
        _throw_permission(
            _("Kiosk accounts can save and submit Timesheets, but cannot undo them.")
        )


def _request_path():
    request = getattr(getattr(frappe, "local", None), "request", None)
    return unquote(getattr(request, "path", "") or "")


def _request_method():
    request = getattr(getattr(frappe, "local", None), "request", None)
    return (getattr(request, "method", "") or "").upper()


def _form_dict():
    form = getattr(getattr(frappe, "local", None), "form_dict", None)
    return form if form is not None else {}


def _request_command(form, path):
    command = form.get("cmd")
    if isinstance(command, str) and command:
        return command

    match = _METHOD_API_RE.match(path)
    return match.group(1) if match else ""


def _parse_json_if_possible(value):
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped or stripped[0] not in "[{\"":
        return value

    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return value


def _collect_relevant_doctypes(value, result):
    value = _parse_json_if_possible(value)

    if isinstance(value, dict):
        for key, nested_value in value.items():
            if (
                key in DOCTYPE_ARGUMENT_KEYS
                and isinstance(nested_value, str)
                and nested_value in RELEVANT_DOCTYPES
            ):
                result.add(nested_value)
            elif key in ("args", "data", "doc", "docs", "filters", "or_filters"):
                _collect_relevant_doctypes(nested_value, result)
        return

    if isinstance(value, (list, tuple)):
        # Four-part Frappe filters begin with a DocType.
        if (
            len(value) >= 4
            and isinstance(value[0], str)
            and value[0] in RELEVANT_DOCTYPES
        ):
            result.add(value[0])
        for nested_value in value:
            if isinstance(nested_value, (dict, list, tuple)):
                _collect_relevant_doctypes(nested_value, result)


def _requested_doctypes(form, path):
    result = set()

    document_match = _DOCUMENT_API_RE.match(path)
    if document_match and document_match.group(1) in RELEVANT_DOCTYPES:
        result.add(document_match.group(1))

    for key in DOCTYPE_ARGUMENT_KEYS:
        value = form.get(key)
        if isinstance(value, str) and value in RELEVANT_DOCTYPES:
            result.add(value)

    for key in ("args", "data", "doc", "docs", "filters", "or_filters"):
        if key in form:
            _collect_relevant_doctypes(form.get(key), result)

    return result


def _iter_strings(value):
    value = _parse_json_if_possible(value)
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested_value in value.items():
            yield str(key)
            yield from _iter_strings(nested_value)
    elif isinstance(value, (list, tuple)):
        for nested_value in value:
            yield from _iter_strings(nested_value)


def _query_uses_protected_link(form, path):
    """Reject link-field expansion into Employee/User from another DocType."""

    source_doctype = form.get("doctype")
    if not source_doctype:
        document_match = _DOCUMENT_API_RE.match(path)
        source_doctype = document_match.group(1) if document_match else None

    if (
        not isinstance(source_doctype, str)
        or not source_doctype
        or source_doctype in PROTECTED_DOCTYPES
    ):
        return False

    try:
        meta = frappe.get_meta(source_doctype)
    except Exception:
        return False

    for key in QUERY_ARGUMENT_KEYS:
        if key not in form:
            continue

        for expression in _iter_strings(form.get(key)):
            lowered = expression.lower().replace("`", "")
            if "tabemployee." in lowered or "tabuser." in lowered:
                return True

            match = re.search(r"(?:^|[\s,(])([a-zA-Z0-9_]+)\.", expression)
            if not match:
                continue

            link_fieldname = match.group(1)
            if link_fieldname in ("owner", "modified_by"):
                return True

            field = meta.get_field(link_fieldname)
            if field and getattr(field, "fieldtype", None) == "Link":
                if getattr(field, "options", None) in PROTECTED_DOCTYPES:
                    return True

    return False


def _is_document_collection_request(path):
    match = _DOCUMENT_API_RE.match(path)
    return bool(
        match
        and match.group(1) == TIMESHEET_DOCTYPE
        and not (match.group(2) or "").strip("/")
        and _request_method() == "GET"
    )


def _add_timesheet_employee_filter(form, employee):
    raw_filters = form.get("filters")
    filters = _parse_json_if_possible(raw_filters)

    if filters in (None, ""):
        filters = []

    if isinstance(filters, dict):
        filters["employee"] = employee
    elif isinstance(filters, (list, tuple)):
        filters = list(filters)
        filters.append([TIMESHEET_DOCTYPE, "employee", "=", employee])
    elif isinstance(filters, str):
        # get_value also accepts a document name in place of a filter mapping.
        filters = {"name": filters, "employee": employee}
    else:
        _throw_permission(_("Invalid Timesheet filters for kiosk mode."))

    form["filters"] = filters


def before_request():
    """Close standard REST/RPC/report/export bypasses for kiosk sessions."""

    if not is_kiosk_user():
        return

    form = _form_dict()
    path = _request_path()
    command = _request_command(form, path)
    requested_doctypes = _requested_doctypes(form, path)

    if requested_doctypes.intersection(PROTECTED_DOCTYPES):
        _throw_permission(
            _("Kiosk accounts cannot access Employee or User records.")
        )

    if command in SENSITIVE_DATA_METHODS:
        _throw_permission(
            _("Kiosk accounts cannot access Employee or User records.")
        )

    if (
        command in REPORT_AND_EXPORT_METHODS
        or form.get("view") == "Report"
    ):
        _throw_permission(
            _("Reports, exports, and printing are not available in kiosk mode.")
        )

    if _query_uses_protected_link(form, path):
        _throw_permission(
            _("Kiosk accounts cannot expand Employee or User record fields.")
        )

    if TIMESHEET_DOCTYPE not in requested_doctypes:
        return

    if not (
        command in TIMESHEET_COLLECTION_METHODS
        or _is_document_collection_request(path)
    ):
        return

    employee = get_kiosk_employee()
    if not employee:
        _throw_permission(
            _(
                "This kiosk login must be linked to exactly one active Employee "
                "before it can list Timesheets."
            )
        )

    _add_timesheet_employee_filter(form, employee)
