import frappe


def boot_session(bootinfo):
    """Fix Frappe v16 sidebar rendering quirks."""

    # 1. Remove broken sidebar items with null link_to.
    # These cause TypeError in frappe.router.slug which kills the desk.
    sidebar_items = getattr(bootinfo, "workspace_sidebar_item", None) or {}
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
