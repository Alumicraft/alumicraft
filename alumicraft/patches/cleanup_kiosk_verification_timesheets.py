"""Remove the two draft Timesheets created by the kiosk verification run."""

import frappe


KIOSK_USER = "kiosk@drivealumicraft.com"
VERIFICATION_TIMESHEETS = {
    "HR-EMP-00016": {
        "employee": "HR-EMP-00016",
        "description": "CODEX KIOSK SAVE TEST",
    },
    "TS-2026-00897": {
        "employee": "HR-EMP-00015",
        "description": "CODEX KIOSK NAMING TEST",
    },
}


def _matches_verification_timesheet(doc, expected):
    rows = list(getattr(doc, "time_logs", None) or [])
    if (
        getattr(doc, "docstatus", None) != 0
        or getattr(doc, "owner", None) != KIOSK_USER
        or getattr(doc, "employee", None) != expected["employee"]
        or len(rows) != 1
    ):
        return False

    row = rows[0]
    return bool(
        getattr(row, "description", None) == expected["description"]
        and getattr(row, "project", None) == "LOKS21124"
        and getattr(row, "task", None) == "TASK-2025-00091"
        and float(getattr(row, "hours", 0) or 0) == 1.0
    )


def execute():
    for name, expected in VERIFICATION_TIMESHEETS.items():
        if not frappe.db.exists("Timesheet", name):
            continue

        doc = frappe.get_doc("Timesheet", name)
        if not _matches_verification_timesheet(doc, expected):
            continue

        frappe.delete_doc(
            "Timesheet",
            name,
            ignore_permissions=True,
            force=True,
        )
