import frappe


DEFAULT_KIOSK_ROLES = (
    "Kiosk User",
    "Employee Kiosk",
    "Timesheet Kiosk",
    "Alumicraft Kiosk",
    "Employee Self Service",
    "Employee",
)

DEFAULT_TIMESHEET_ROUTE = ("Form", "Timesheet", "new-timesheet")

PRIVILEGED_ROLES = (
    "System Manager",
    "Accounts Manager",
    "Accounts User",
    "HR Manager",
    "HR User",
    "Projects Manager",
    "Projects User",
    "Sales Manager",
    "Sales User",
    "Purchase Manager",
    "Purchase User",
    "Stock Manager",
    "Stock User",
    "Manufacturing Manager",
    "Manufacturing User",
    "Workspace Manager",
)


def _boot_value(bootinfo, key, default=None):
    if isinstance(bootinfo, dict):
        return bootinfo.get(key, default)
    return getattr(bootinfo, key, default)


def _set_boot_value(bootinfo, key, value):
    if isinstance(bootinfo, dict):
        bootinfo[key] = value
    else:
        setattr(bootinfo, key, value)


def _clean_list_item(value):
    return frappe.safe_decode(value).strip()


def _coerce_list(value, default):
    if value in (None, ""):
        return list(default)

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return list(default)

        try:
            parsed = frappe.parse_json(stripped)
        except Exception:
            parsed = None

        if isinstance(parsed, (list, tuple, set)):
            return [
                item for item in (_clean_list_item(item) for item in parsed)
                if item
            ]

        return [part.strip() for part in stripped.split(",") if part.strip()]

    if isinstance(value, (list, tuple, set)):
        return [
            item for item in (_clean_list_item(item) for item in value)
            if item
        ]

    return list(default)


def _kiosk_roles():
    return _coerce_list(
        frappe.conf.get("alumicraft_kiosk_roles"),
        DEFAULT_KIOSK_ROLES,
    )


def _kiosk_target_route():
    route = _coerce_list(
        frappe.conf.get("alumicraft_kiosk_target_route"),
        DEFAULT_TIMESHEET_ROUTE,
    )

    if len(route) < 2:
        return list(DEFAULT_TIMESHEET_ROUTE)

    return route


def _is_kiosk_user():
    user = frappe.session.user
    if user in ("Administrator", "Guest"):
        return False

    roles = set(frappe.get_roles(user) or [])
    if roles.intersection(PRIVILEGED_ROLES):
        return False

    return bool(roles.intersection(_kiosk_roles()))


def _hide_desk_navigation(bootinfo):
    for key, empty_value in (
        ("workspace_sidebar_item", {}),
        ("desktop_icons", []),
        ("module_list", []),
        ("allowed_workspaces", []),
    ):
        if _boot_value(bootinfo, key, None) is not None:
            _set_boot_value(bootinfo, key, empty_value)


def _set_kiosk_boot(bootinfo):
    route = _kiosk_target_route()
    target_doctype = route[1] if len(route) > 1 else "Timesheet"

    _set_boot_value(
        bootinfo,
        "alumicraft_kiosk",
        {
            "enabled": True,
            "roles": _kiosk_roles(),
            "target_route": route,
            "target_doctype": target_doctype,
        },
    )
    _hide_desk_navigation(bootinfo)


def boot_session(bootinfo):
    """Fix Frappe v16 sidebar rendering quirks."""

    # 1. Remove broken sidebar items with null link_to.
    # These cause TypeError in frappe.router.slug which kills the desk.
    sidebar_items = _boot_value(bootinfo, "workspace_sidebar_item", {}) or {}
    for name, sidebar in sidebar_items.items():
        if sidebar.get("items"):
            sidebar["items"] = [
                item for item in sidebar["items"]
                if item.get("type") != "Link"
                or item.get("link_to")
                or item.get("link_type") == "URL"
            ]

    # 2. Fix sidebar item rendering quirks:
    # - Spacer: needs standard=True to bypass TypeLink.make() early-return guard
    # - Section Break: needs indent=1 for icon+label style (vs bare divider)
    for _name, sidebar in sidebar_items.items():
        for item in (sidebar.get("items") or []):
            if item.get("type") == "Spacer":
                item["standard"] = True
            if item.get("type") == "Section Break" and not item.get("indent"):
                item["indent"] = 1

    if _is_kiosk_user():
        _set_kiosk_boot(bootinfo)
