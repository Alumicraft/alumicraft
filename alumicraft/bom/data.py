"""Permission-aware, read-only ERPNext data access for vehicle BOM studies.

This module deliberately uses the authorized parent documents returned by
``frappe.get_list`` as the boundary for child-row reads.  A child table is
not a permission boundary in Frappe, so a ``get_all`` on a child table must
always be constrained to those parent names.
"""

from __future__ import annotations

import html
import math
import re
from datetime import date, datetime
from decimal import Decimal
from numbers import Number
from typing import Any, Iterable, Mapping

import frappe


PAGE_SIZE = 500
_TAG_RE = re.compile(r"<[^>]*>")
_GENERIC_ITEM_CODES = frozenset(
    {
        "item-not-available",
        "part",
        "parts",
        "quickbooks expense line",
    }
)
_NON_EXPENSE_ROOT_TYPES = frozenset({"asset", "liability", "equity", "income"})


def collect_snapshot(
    company: str,
    projects: list[str],
    from_date: str | date | datetime | None = None,
    to_date: str | date | datetime | None = None,
) -> dict[str, Any]:
    """Return submitted purchase and labor evidence for authorized projects.

    ``get_list`` is used for Company, Project, Item, Account, Purchase Invoice,
    and Timesheet so normal Frappe read permissions apply.  Child rows are
    read with ``get_all`` only after their authorized parent names have been
    collected.  The result contains no employee identifiers or unrelated
    accounting fields and is safe to serialize as JSON.
    """

    company = _required_text(company, "company")
    project_names = _unique_names(projects, "projects")

    company_rows = _paged_get_list(
        "Company",
        {"name": company},
        ["name", "default_currency"],
        order_by="name asc",
    )
    if not company_rows:
        _raise_permission(f"Company is not readable or does not exist: {company}")
    company_row = company_rows[0]
    currency = _text(_row_value(company_row, "default_currency")) or None

    project_rows = _paged_get_list(
        "Project",
        {"name": ["in", project_names], "company": company},
        ["name", "company", "project_name", "description"],
        order_by="name asc",
    )
    authorized_projects = {
        _text(_row_value(row, "name"))
        for row in project_rows
        if _text(_row_value(row, "name"))
    }
    missing_projects = [name for name in project_names if name not in authorized_projects]
    if missing_projects:
        _raise_permission(
            "Project is not readable, does not exist, or belongs to another company: "
            + ", ".join(missing_projects)
        )

    purchase_parents = _get_purchase_parents(company, from_date, to_date)
    purchase_parent_names = [
        _text(_row_value(row, "name"))
        for row in purchase_parents
        if _text(_row_value(row, "name"))
    ]
    purchase_children = _get_child_rows(
        "Purchase Invoice Item",
        purchase_parent_names,
        [
            "name",
            "parent",
            "idx",
            "project",
            "item_code",
            "item_name",
            "description",
            "item_group",
            "stock_uom",
            "uom",
            "qty",
            "stock_qty",
            "conversion_factor",
            "rate",
            "amount",
            "net_rate",
            "net_amount",
            "base_rate",
            "base_amount",
            "base_net_rate",
            "base_net_amount",
            "expense_account",
        ],
    )

    item_metadata = _get_item_metadata(purchase_children)
    account_roots = _get_account_roots(purchase_children)
    purchase_lines, purchase_warnings = _build_purchase_lines(
        purchase_parents,
        purchase_children,
        authorized_projects,
        currency,
        item_metadata,
        account_roots,
    )

    timesheet_parents = _get_timesheet_parents(company, from_date, to_date)
    timesheet_parent_names = [
        _text(_row_value(row, "name"))
        for row in timesheet_parents
        if _text(_row_value(row, "name"))
    ]
    timesheet_children = _get_child_rows(
        "Timesheet Detail",
        timesheet_parent_names,
        [
            "name",
            "parent",
            "idx",
            "project",
            "from_time",
            "to_time",
            "activity_type",
            "hours",
            "costing_amount",
            "base_costing_amount",
            "description",
        ],
        filters=_timesheet_detail_filters(from_date, to_date),
    )
    labor_lines, labor_warnings = _build_labor_lines(
        timesheet_parents,
        timesheet_children,
        authorized_projects,
        currency,
    )

    warnings = _unique_warnings(purchase_warnings + labor_warnings)
    return {
        "company": company,
        "currency": currency,
        "projects": project_names,
        "purchase_lines": purchase_lines,
        "labor_lines": labor_lines,
        "warnings": warnings,
    }


def _get_purchase_parents(
    company: str,
    from_date: str | date | datetime | None,
    to_date: str | date | datetime | None,
) -> list[Any]:
    filters: dict[str, Any] = {"company": company, "docstatus": 1}
    if from_date is not None and to_date is not None:
        filters["posting_date"] = [
            "between",
            [_date_value(from_date), _date_value(to_date)],
        ]
    elif from_date is not None:
        filters["posting_date"] = [">=", _date_value(from_date)]
    elif to_date is not None:
        filters["posting_date"] = ["<=", _date_value(to_date)]
    return _paged_get_list(
        "Purchase Invoice",
        filters,
        _available_fields(
            "Purchase Invoice",
            [
                "name",
                "company",
                "posting_date",
                "currency",
                "conversion_rate",
                "is_return",
                "return_against",
                "supplier",
                "project",
            ],
        ),
        order_by="posting_date asc, name asc",
    )


def _get_timesheet_parents(
    company: str,
    from_date: str | date | datetime | None,
    to_date: str | date | datetime | None,
) -> list[Any]:
    filters: dict[str, Any] = {"company": company, "docstatus": 1}
    # Include a parent whenever its interval overlaps the requested period.
    # This preserves a Timesheet that starts before the window and ends inside
    # it; detail rows are bounded separately by from_time below.
    if from_date is not None:
        filters["end_date"] = [">=", _date_value(from_date)]
    if to_date is not None:
        filters["start_date"] = ["<=", _date_value(to_date)]
    return _paged_get_list(
        "Timesheet",
        filters,
        _available_fields(
            "Timesheet",
            [
                "name",
                "company",
                "start_date",
                "end_date",
                "parent_project",
                "currency",
                "exchange_rate",
            ],
        ),
        order_by="start_date asc, name asc",
    )


def _timesheet_detail_filters(
    from_date: str | date | datetime | None,
    to_date: str | date | datetime | None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if from_date is not None:
        filters["from_time"] = [">=", _time_bound(from_date)]
    if to_date is not None:
        filters["from_time"] = ["<=", _time_bound(to_date, end=True)] if from_date is None else [
            "between",
            [_time_bound(from_date), _time_bound(to_date, end=True)],
        ]
    return filters


def _get_child_rows(
    doctype: str,
    parent_names: list[str],
    fields: list[str],
    *,
    filters: Mapping[str, Any] | None = None,
) -> list[Any]:
    if not parent_names:
        return []
    child_filters: dict[str, Any] = {"parent": ["in", parent_names]}
    child_filters.update(filters or {})
    return _paged_get_all(
        doctype,
        child_filters,
        fields,
        order_by="parent asc, idx asc",
    )


def _get_item_metadata(children: Iterable[Any]) -> dict[str, dict[str, Any]]:
    codes = sorted(
        {
            code
            for child in children
            if (code := _text(_row_value(child, "item_code")))
        }
    )
    if not codes:
        return {}
    rows = _paged_get_list(
        "Item",
        {"name": ["in", codes]},
        ["name", "item_name", "item_group", "stock_uom"],
        order_by="name asc",
    )
    return {
        _text(_row_value(row, "name")): {
            "item_name": _row_value(row, "item_name"),
            "item_group": _row_value(row, "item_group"),
            "stock_uom": _row_value(row, "stock_uom"),
        }
        for row in rows
        if _text(_row_value(row, "name"))
    }


def _get_account_roots(children: Iterable[Any]) -> dict[str, str]:
    accounts = sorted(
        {
            account
            for child in children
            if (account := _text(_row_value(child, "expense_account")))
        }
    )
    if not accounts:
        return {}
    rows = _paged_get_list(
        "Account",
        {"name": ["in", accounts]},
        ["name", "root_type"],
        order_by="name asc",
    )
    return {
        _text(_row_value(row, "name")): _text(_row_value(row, "root_type")) or ""
        for row in rows
        if _text(_row_value(row, "name"))
    }


def _build_purchase_lines(
    parents: Iterable[Any],
    children: Iterable[Any],
    projects: set[str],
    currency: str | None,
    item_metadata: Mapping[str, Mapping[str, Any]],
    account_roots: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    parent_by_name = {
        _text(_row_value(parent, "name")): parent
        for parent in parents
        if _text(_row_value(parent, "name"))
    }
    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    unassigned = 0
    duplicate_ids = 0
    seen_ids: set[str] = set()

    for child in children:
        parent_name = _text(_row_value(child, "parent"))
        parent = parent_by_name.get(parent_name)
        if parent is None:
            continue
        child_project = _text(_row_value(child, "project"))
        parent_project = _text(_row_value(parent, "project"))
        project = child_project or parent_project
        if not project:
            unassigned += 1
            continue
        if project not in projects:
            continue

        source_id = _text(_row_value(child, "name"))
        if not source_id or source_id in seen_ids:
            duplicate_ids += 1
            continue
        seen_ids.add(source_id)

        item_code = _text(_row_value(child, "item_code")) or None
        item = item_metadata.get(item_code or "", {})
        item_name = _text(_row_value(child, "item_name")) or _text(item.get("item_name")) or None
        item_group = _text(_row_value(child, "item_group")) or _text(item.get("item_group")) or None
        stock_uom = _text(_row_value(child, "stock_uom")) or _text(item.get("stock_uom")) or None
        flags: list[str] = []
        normalized_code = (item_code or "").strip().lower()
        if not item_code or normalized_code in _GENERIC_ITEM_CODES:
            flags.append("generic_item")
        if not stock_uom:
            flags.append("missing_stock_uom")

        is_return = bool(_row_value(parent, "is_return"))
        quantity, quantity_flags = _stock_quantity(child, is_return)
        flags.extend(quantity_flags)
        amount, amount_flags = _company_amount(child, parent, currency, is_return)
        flags.extend(amount_flags)
        unit_cost = _unit_cost(child, quantity, amount, currency, parent)
        if unit_cost is None:
            flags.append("missing_unit_cost")

        account = _text(_row_value(child, "expense_account"))
        root_type = account_roots.get(account or "")
        if root_type and root_type.lower() != "expense":
            flags.append("non_expense_account")
            if root_type.lower() in _NON_EXPENSE_ROOT_TYPES:
                flags.append("financial_account_review")

        lines.append(
            {
                "source_id": source_id,
                "invoice": parent_name,
                "posting_date": _date_value(_row_value(parent, "posting_date")),
                "project": project,
                "supplier": _text(_row_value(parent, "supplier")) or None,
                "item_code": item_code,
                "item_name": item_name,
                "description": _plain_text(_row_value(child, "description")),
                "item_group": item_group,
                "stock_uom": stock_uom,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "amount": amount,
                "is_return": is_return,
                "return_against": _text(_row_value(parent, "return_against")) or None,
                **({"flags": _unique_flags(flags)} if flags else {}),
            }
        )

    if unassigned:
        warnings.append(f"{unassigned} submitted purchase line(s) had no explicit project and were excluded.")
    if duplicate_ids:
        warnings.append(f"{duplicate_ids} duplicate purchase line source id(s) were excluded.")
    if any("non_expense_account" in line.get("flags", []) for line in lines):
        warnings.append("Some purchase lines use non-expense accounts and need review before BOM use.")
    if any("unconverted_currency" in line.get("flags", []) for line in lines):
        warnings.append("Some purchase lines could not be converted to company currency.")
    if any(
        set(line.get("flags", []))
        & {"invalid_conversion_factor", "unknown_stock_conversion", "malformed_quantity", "malformed_stock_quantity"}
        for line in lines
    ):
        warnings.append("Some purchase lines have unknown stock-unit quantities or conversion factors.")
    if any("missing_company_amount" in line.get("flags", []) for line in lines):
        warnings.append("Some purchase lines have no usable company-currency amount.")
    return lines, warnings


def _build_labor_lines(
    parents: Iterable[Any],
    children: Iterable[Any],
    projects: set[str],
    company_currency: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    parent_by_name = {
        _text(_row_value(parent, "name")): parent
        for parent in parents
        if _text(_row_value(parent, "name"))
    }
    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    unassigned = 0
    unknown_costs = 0
    seen_ids: set[str] = set()

    for child in children:
        parent_name = _text(_row_value(child, "parent"))
        parent = parent_by_name.get(parent_name)
        if parent is None:
            continue
        project = _text(_row_value(child, "project")) or _text(_row_value(parent, "parent_project"))
        if not project:
            unassigned += 1
            continue
        if project not in projects:
            continue
        source_id = _text(_row_value(child, "name"))
        if not source_id or source_id in seen_ids:
            continue
        seen_ids.add(source_id)
        costing_amount, cost_unknown = _labor_costing_amount(child, parent, company_currency)
        if cost_unknown:
            unknown_costs += 1
        lines.append(
            {
                "source_id": source_id,
                "timesheet": parent_name,
                "project": project,
                "activity_type": _text(_row_value(child, "activity_type")) or None,
                "hours": _json_number(_row_value(child, "hours")),
                "costing_amount": costing_amount,
                "description": _plain_text(_row_value(child, "description")),
            }
        )
    if unassigned:
        warnings.append(f"{unassigned} submitted timesheet detail row(s) had no explicit project and were excluded.")
    if unknown_costs:
        warnings.append(f"{unknown_costs} submitted timesheet detail row(s) had no company-currency labor cost.")
    return lines, warnings


def _labor_costing_amount(
    row: Any,
    parent: Any,
    company_currency: str | None,
) -> tuple[int | float | None, bool]:
    base_cost = _row_value(row, "base_costing_amount")
    if base_cost is not None:
        amount = _json_number(base_cost)
        return amount, amount is None

    raw_cost = _row_value(row, "costing_amount")
    if raw_cost is None:
        return None, True
    amount = _numeric(raw_cost)
    if amount is None:
        return None, True
    currency = _text(_row_value(parent, "currency"))
    if currency and company_currency and currency != company_currency:
        exchange_rate = _numeric(_row_value(parent, "exchange_rate"))
        if exchange_rate is None or exchange_rate <= 0:
            return None, True
        amount *= exchange_rate
    return _json_number(amount), False


def _stock_quantity(row: Any, is_return: bool) -> tuple[int | float | None, list[str]]:
    flags: list[str] = []
    raw = _row_value(row, "stock_qty")
    if raw is None:
        qty = _row_value(row, "qty")
        if qty is None:
            return None, ["missing_quantity"]
        qty_number = _numeric(qty)
        if qty_number is None:
            return None, ["malformed_quantity"]
        factor_raw = _row_value(row, "conversion_factor")
        if factor_raw is None:
            uom = _text(_row_value(row, "uom"))
            stock_uom = _text(_row_value(row, "stock_uom"))
            if not uom or not stock_uom or uom != stock_uom:
                return None, ["unknown_stock_conversion"]
            factor_number = 1
        else:
            factor_number = _numeric(factor_raw)
            if factor_number is None or factor_number <= 0:
                return None, ["invalid_conversion_factor"]
        raw = qty_number * factor_number
    number = _json_number(raw)
    if number is None:
        return None, ["malformed_stock_quantity"]
    return (-abs(number) if is_return else number), flags


def _company_amount(
    row: Any,
    parent: Any,
    company_currency: str | None,
    is_return: bool,
) -> tuple[int | float | None, list[str]]:
    flags: list[str] = []
    raw = _first_value(row, "base_net_amount", "base_amount")
    if raw is None:
        raw = _first_value(row, "net_amount", "amount")
        invoice_currency = _text(_row_value(parent, "currency"))
        conversion_rate = _numeric(_row_value(parent, "conversion_rate"))
        if raw is not None and invoice_currency and company_currency and invoice_currency != company_currency:
            if conversion_rate is None:
                flags.append("unconverted_currency")
                return None, flags
            raw = _numeric(raw) * conversion_rate
    if raw is None:
        flags.append("missing_company_amount")
        return None, flags
    number = _json_number(raw)
    if number is None:
        flags.append("missing_company_amount")
        return None, flags
    return (-abs(number) if is_return else number), flags


def _unit_cost(
    row: Any,
    quantity: int | float | None,
    amount: int | float | None,
    company_currency: str | None,
    parent: Any,
) -> int | float | None:
    if amount is not None and quantity not in (None, 0):
        return _json_number(abs(_numeric(amount)) / abs(_numeric(quantity)))

    factor_raw = _row_value(row, "conversion_factor")
    if factor_raw is None:
        uom = _text(_row_value(row, "uom"))
        stock_uom = _text(_row_value(row, "stock_uom"))
        if not uom or not stock_uom or uom != stock_uom:
            return None
        factor = 1
    else:
        factor = _numeric(factor_raw)
        if factor is None or factor <= 0:
            return None
    raw_rate = _first_value(row, "base_net_rate", "base_rate")
    if raw_rate is None:
        raw_rate = _first_value(row, "net_rate", "rate")
        invoice_currency = _text(_row_value(parent, "currency"))
        conversion_rate = _numeric(_row_value(parent, "conversion_rate"))
        if raw_rate is not None and invoice_currency and company_currency and invoice_currency != company_currency:
            if conversion_rate is None:
                return None
            raw_rate = _numeric(raw_rate) * conversion_rate
    if raw_rate is not None:
        rate = _numeric(raw_rate)
        if rate is not None:
            return _json_number(abs(rate) / abs(factor))
    return None


def _paged_get_list(
    doctype: str,
    filters: Mapping[str, Any],
    fields: list[str],
    *,
    order_by: str | None = None,
) -> list[Any]:
    return _paged_query(frappe.get_list, doctype, filters, fields, order_by=order_by)


def _paged_get_all(
    doctype: str,
    filters: Mapping[str, Any],
    fields: list[str],
    *,
    order_by: str | None = None,
) -> list[Any]:
    return _paged_query(frappe.get_all, doctype, filters, fields, order_by=order_by)


def _paged_query(
    query: Any,
    doctype: str,
    filters: Mapping[str, Any],
    fields: list[str],
    *,
    order_by: str | None,
) -> list[Any]:
    rows: list[Any] = []
    start = 0
    previous_signature: tuple[str, ...] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "filters": dict(filters),
            "fields": fields,
            "start": start,
            "page_length": PAGE_SIZE,
        }
        if order_by:
            kwargs["order_by"] = order_by
        page = list(query(doctype, **kwargs) or [])
        if not page:
            break
        signature = tuple(
            _text(_row_value(row, "name")) or repr(row)
            for row in page[:3]
        )
        if start and signature == previous_signature:
            raise RuntimeError(f"{doctype} pagination did not advance")
        rows.extend(page)
        previous_signature = signature
        if len(page) < PAGE_SIZE:
            break
        start += len(page)
    return rows


def _row_value(row: Any, fieldname: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(fieldname)
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(fieldname)
    return getattr(row, fieldname, None)


def _available_fields(doctype: str, fields: list[str]) -> list[str]:
    """Drop optional fields absent from the installed ERPNext schema.

    Timesheet's project link is present in some custom Alumicraft schemas but
    is normally carried only by Timesheet Detail.  Asking Frappe metadata
    avoids an SQL error on sites without that optional parent field; lightweight
    test doubles without ``get_meta`` retain the full requested field list.
    """
    get_meta = getattr(frappe, "get_meta", None)
    if not callable(get_meta):
        return fields
    try:
        meta = get_meta(doctype)
        get_field = getattr(meta, "get_field", None)
        if not callable(get_field):
            return fields
        return [field for field in fields if field == "name" or get_field(field) is not None]
    except Exception:
        return fields


def _first_value(row: Any, *fields: str) -> Any:
    for field in fields:
        value = _row_value(row, field)
        if value is not None:
            return value
    return None


def _required_text(value: Any, fieldname: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{fieldname} is required")
    return text


def _unique_names(values: Iterable[str], fieldname: str) -> list[str]:
    if isinstance(values, str):
        values = [values]
    names: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = _text(value)
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    if not names:
        raise ValueError(f"{fieldname} must contain at least one project")
    return names


def _raise_permission(message: str) -> None:
    error = getattr(frappe, "PermissionError", PermissionError)
    raise error(message)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _text(value) or None


def _time_bound(value: Any, *, end: bool = False) -> str | None:
    """Return an inclusive datetime bound for Timesheet Detail.from_time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return f"{value.isoformat()} {'23:59:59' if end else '00:00:00'}"
    text = _text(value)
    if len(text) == 10:
        return f"{text} {'23:59:59' if end else '00:00:00'}"
    return text or None


def _plain_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return " ".join(html.unescape(_TAG_RE.sub(" ", text)).split())


def _numeric(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Number):
        return float(value) if isinstance(value, Decimal) else value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _json_number(value: Any) -> int | float | None:
    number = _numeric(value)
    if number is None:
        return None
    number = float(number)
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _unique_flags(flags: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(flag for flag in flags if flag))


def _unique_warnings(warnings: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(warning for warning in warnings if warning))
