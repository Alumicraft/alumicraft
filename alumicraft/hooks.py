app_name = "alumicraft"
app_title = "Alumicraft"
app_publisher = "Alumicraft"
app_description = "Alumicraft custom Frappe app"
app_email = "dev@alumicraft.local"
app_license = "MIT"

ALUMICRAFT_ASSET_VERSION = "20260710-4"


def versioned_asset(path):
    return f"{path}?v={ALUMICRAFT_ASSET_VERSION}"


# Boot session — applies Frappe v16 sidebar boot-data fixes.
boot_session = "alumicraft.api.boot.boot_session"
extend_bootinfo = "alumicraft.api.boot.boot_session"

# Desk JS — sidebar rendering / active-state / workspace-switch fixes.
app_include_js = [
    versioned_asset("/assets/alumicraft/js/sidebar_fix.js"),
    versioned_asset("/assets/alumicraft/js/kiosk_timesheet.js"),
    versioned_asset("/assets/alumicraft/js/timesheet_kiosk.js"),
]

# Keep the existing Timesheet Field Behavior weekly-summary call quiet without
# allowing it to query saved Timesheet names for the shared kiosk account.
override_whitelisted_methods = {
    "frappe.client.get_list": "alumicraft.permissions.guarded_client_get_list",
}

# Timesheet kiosk support is an app asset rather than ``doctype_js`` so a new
# deployment cannot be hidden behind Frappe's cached Timesheet FormMeta.

# Server-side kiosk boundary. The browser guard prevents accidental
# navigation; these hooks prevent direct REST/RPC access to the underlying
# Employee/User records and bind Timesheet writes to the kiosk login.
before_request = ["alumicraft.permissions.before_request"]

permission_query_conditions = {
    "Employee": "alumicraft.permissions.deny_protected_doctype_query",
    "User": "alumicraft.permissions.deny_protected_doctype_query",
    "Timesheet": "alumicraft.permissions.timesheet_query_conditions",
}

has_permission = {
    "Employee": "alumicraft.permissions.protected_doctype_has_permission",
    "User": "alumicraft.permissions.protected_doctype_has_permission",
    "Timesheet": "alumicraft.permissions.timesheet_has_permission",
}

doc_events = {
    "Timesheet": {
        "before_validate": "alumicraft.permissions.validate_kiosk_timesheet",
        "before_cancel": "alumicraft.permissions.deny_kiosk_timesheet_mutation",
        "before_discard": "alumicraft.permissions.deny_kiosk_timesheet_mutation",
        "before_rename": "alumicraft.permissions.deny_kiosk_timesheet_mutation",
        "before_update_after_submit": (
            "alumicraft.permissions.deny_kiosk_timesheet_mutation"
        ),
        "on_trash": "alumicraft.permissions.deny_kiosk_timesheet_mutation",
    },
}
