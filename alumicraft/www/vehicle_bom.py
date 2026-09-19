"""Direct-entry BOM workspace using the site's existing login session."""

import frappe
from frappe.sessions import get_csrf_token

from alumicraft.bom.portal import require_access

no_cache = 1
sitemap = 0


def get_context(context):
    # Never cache user identity or a CSRF token in a shared website response.
    context.no_cache = 1
    frappe.local.no_cache = True
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=%2Fvehicle-bom"
        raise frappe.Redirect
    require_access()
    context.csrf_token = get_csrf_token()
    context.user_display_name = (
        frappe.get_cached_value("User", frappe.session.user, "full_name") or frappe.session.user
    )
    context.title = "Vehicle BOM · Alumicraft"
    context.show_sidebar = False
    return context
