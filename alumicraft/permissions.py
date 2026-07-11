"""Server-side permissions for Alumicraft kiosk accounts.

The browser kiosk guard is only a navigation aid. This module is the data
boundary: kiosk users cannot open Employee or User documents, cannot list
saved Timesheets, and can only write a Timesheet for an active employee chosen
through the terminal's deliberately narrow employee picker.
"""

import json
import re
from urllib.parse import unquote

import frappe
from frappe import _

from alumicraft.api.boot import get_kiosk_company, is_kiosk_user


PROTECTED_DOCTYPES = frozenset(("Employee", "User"))
TIMESHEET_DOCTYPE = "Timesheet"
RELEVANT_DOCTYPES = PROTECTED_DOCTYPES | frozenset((TIMESHEET_DOCTYPE,))
KIOSK_EMPLOYEE_FIELDS = ("name", "company", "employee_name", "department")
KIOSK_EMPLOYEE_SEARCH_QUERY = "alumicraft.permissions.search_kiosk_employees"

ALLOWED_TIMESHEET_PERMISSION_TYPES = frozenset(("create", "write", "submit"))

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


def _active_employee_identity(employee):
    if not employee:
        return None

    employees = list(
        frappe.get_all(
            "Employee",
            filters={
                "name": employee,
                "status": "Active",
                "company": get_kiosk_company(),
            },
            fields=list(KIOSK_EMPLOYEE_FIELDS),
            limit_page_length=2,
        )
        or []
    )
    if len(employees) != 1:
        return None

    result = {
        fieldname: _row_value(employees[0], fieldname)
        for fieldname in KIOSK_EMPLOYEE_FIELDS
    }
    return result if result.get("name") == employee else None


@frappe.whitelist()
def search_kiosk_employees(
    doctype,
    txt,
    searchfield,
    start,
    page_len,
    filters=None,
    **kwargs,
):
    """Return only the two labels required by the Timesheet employee picker."""

    if (
        not is_kiosk_user()
        or doctype != "Employee"
        or kwargs.get("reference_doctype") != TIMESHEET_DOCTYPE
        or kwargs.get("link_fieldname") != "employee"
    ):
        _throw_permission(_("This employee search is only available in kiosk mode."))

    try:
        start = max(int(start or 0), 0)
    except (TypeError, ValueError):
        start = 0
    try:
        page_len = min(max(int(page_len or 10), 1), 20)
    except (TypeError, ValueError):
        page_len = 10

    query_filters = {
        "status": "Active",
        "company": get_kiosk_company(),
    }
    or_filters = None
    txt = str(txt or "")[:100]
    if txt:
        pattern = "%{0}%".format(txt)
        or_filters = {
            "name": ["like", pattern],
            "employee_name": ["like", pattern],
        }

    rows = frappe.get_all(
        "Employee",
        filters=query_filters,
        or_filters=or_filters,
        fields=["name", "employee_name"],
        order_by="employee_name asc, name asc",
        start=start,
        page_length=page_len,
        as_list=True,
    )
    return [list(row) for row in (rows or [])]


@frappe.whitelist()
def get_kiosk_employee_identity(employee):
    """Return only the fields that the selected Employee contributes to a Timesheet."""

    if not is_kiosk_user():
        _throw_permission(_("This employee lookup is only available in kiosk mode."))

    identity = _active_employee_identity(employee)
    if not identity:
        _throw_permission(_("Please choose an active Alumicraft Employee."))
    return identity


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
    """Shared terminals never list saved Timesheets."""

    if not is_kiosk_user(user):
        return ""
    return "1 = 0"


def timesheet_has_permission(doc, user=None, ptype=None, debug=False):
    """Allow only the narrow Timesheet lifecycle needed by a kiosk user."""

    if not is_kiosk_user(user):
        # Frappe v16 requires permission hooks to explicitly return True when
        # they do not deny access.
        return True

    document_employee = _document_value(doc, "employee")

    # Frappe checks create permission before the operator has selected an
    # employee and before before_validate runs.
    if ptype == "create":
        return not document_employee or bool(
            _active_employee_identity(document_employee)
        )

    if ptype not in ALLOWED_TIMESHEET_PERMISSION_TYPES:
        return False

    return bool(
        _document_value(doc, "owner") == user
        and _active_employee_identity(document_employee)
    )


def validate_kiosk_timesheet(doc, method=None):
    """Bind every kiosk Timesheet write to one selected active Employee."""

    user = frappe.session.user
    if not is_kiosk_user(user):
        return

    document_employee = _document_value(doc, "employee")
    defaults = _active_employee_identity(document_employee)
    if not defaults:
        _throw_permission(_("Please choose an active Employee before saving."))

    is_new = getattr(doc, "is_new", None)
    is_new = is_new() if callable(is_new) else not _document_value(doc, "name")
    if not is_new:
        stored = frappe.db.get_value(
            TIMESHEET_DOCTYPE,
            _document_value(doc, "name"),
            ["owner", "employee"],
            as_dict=True,
        )
        if (
            not stored
            or _row_value(stored, "owner") != user
            or _row_value(stored, "employee") != document_employee
        ):
            _throw_permission(
                _(
                    "The shared terminal cannot change an existing Timesheet "
                    "owner or Employee."
                )
            )

    # Treat the client values only as presentation defaults. Re-apply the
    # authoritative Employee-derived values on every write so a crafted request
    # cannot change company, department, or employee name. The shared terminal
    # intentionally leaves Timesheet.user blank, matching the historical flow
    # and avoiding false overlap conflicts between different employees.
    for fieldname, value in defaults.items():
        setattr(doc, fieldname, value)
    setattr(doc, "user", None)


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


def _is_kiosk_employee_search_request(form, command):
    """Recognize the one Employee projection used by the Timesheet Link field."""

    filters = _parse_json_if_possible(form.get("filters"))
    ignore_permissions = str(form.get("ignore_user_permissions") or "").lower()
    return bool(
        command == "frappe.desk.search.search_link"
        and _request_method() in ("GET", "POST")
        and form.get("doctype") == "Employee"
        and form.get("query") == KIOSK_EMPLOYEE_SEARCH_QUERY
        and form.get("reference_doctype") == TIMESHEET_DOCTYPE
        and form.get("link_fieldname") == "employee"
        and filters in (None, "", {}, [])
        and ignore_permissions in ("", "0", "false")
    )


def before_request():
    """Close standard REST/RPC/report/export bypasses for kiosk sessions."""

    if not is_kiosk_user():
        return

    form = _form_dict()
    path = _request_path()
    command = _request_command(form, path)
    requested_doctypes = _requested_doctypes(form, path)
    allowed_employee_search = _is_kiosk_employee_search_request(form, command)
    protected_doctypes = requested_doctypes.intersection(PROTECTED_DOCTYPES)

    if (
        protected_doctypes
        and (
            not allowed_employee_search
            or protected_doctypes != {"Employee"}
        )
    ):
        _throw_permission(
            _("Kiosk accounts cannot access Employee or User records.")
        )

    if allowed_employee_search:
        return

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

    _throw_permission(
        _("Saved Timesheets are not available from the shared terminal.")
    )
