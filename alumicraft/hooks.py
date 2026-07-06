app_name = "alumicraft"
app_title = "Alumicraft"
app_publisher = "Alumicraft"
app_description = "Alumicraft custom Frappe app"
app_email = "dev@alumicraft.local"
app_license = "MIT"

ALUMICRAFT_ASSET_VERSION = "20260706-1"


def versioned_asset(path):
    return f"{path}?v={ALUMICRAFT_ASSET_VERSION}"


# Boot session — applies Frappe v16 sidebar boot-data fixes.
boot_session = "alumicraft.api.boot.boot_session"
extend_bootinfo = "alumicraft.api.boot.boot_session"

# Desk JS — sidebar rendering / active-state / workspace-switch fixes.
app_include_js = [
    versioned_asset("/assets/alumicraft/js/sidebar_fix.js"),
    versioned_asset("/assets/alumicraft/js/kiosk_timesheet.js"),
]
