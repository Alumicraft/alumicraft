/**
 * Restrict employee kiosk users to the Timesheet form.
 *
 * Server-side boot code decides whether kiosk mode is enabled for the session.
 * This file then keeps the Desk shell focused on the configured target form.
 */
(function () {
	"use strict";

	var VERSION = "20260706-1";
	var STYLE_ID = "alumicraft-kiosk-style";
	var ROUTING = false;

	window.__alumicraft_kiosk_debug = window.__alumicraft_kiosk_debug || {};
	window.__alumicraft_kiosk_debug.version = VERSION;

	function get_config() {
		return (window.frappe && frappe.boot && frappe.boot.alumicraft_kiosk) || {};
	}

	function is_enabled() {
		return !!get_config().enabled;
	}

	function target_route() {
		var route = get_config().target_route || ["Form", "Timesheet", "new-timesheet"];
		return route.slice ? route.slice() : ["Form", "Timesheet", "new-timesheet"];
	}

	function target_doctype() {
		return get_config().target_doctype || target_route()[1] || "Timesheet";
	}

	function target_new_docname() {
		return target_route()[2] || "new-timesheet";
	}

	function normalize(value) {
		return String(value || "").toLowerCase();
	}

	function is_new_target_docname(docname) {
		if (!docname) return true;

		var normalized = normalize(docname);
		var target = normalize(target_new_docname());
		return normalized === target || normalized.indexOf(target + "-") === 0;
	}

	function is_allowed_route(route) {
		route = route || [];
		if (!route.length) return false;

		var view = normalize(route[0]);
		var doctype = route[1];

		return (
			view === "form" &&
			doctype === target_doctype() &&
			is_new_target_docname(route[2])
		);
	}

	function route_to_target() {
		if (ROUTING || !window.frappe || !frappe.set_route) return;

		ROUTING = true;
		frappe.set_route.apply(frappe, target_route());
		setTimeout(function () {
			ROUTING = false;
		}, 300);
	}

	function enforce_route() {
		if (!is_enabled() || !window.frappe || !frappe.get_route) return;
		if (!is_allowed_route(frappe.get_route() || [])) route_to_target();
	}

	function prevent_desk_navigation(event) {
		if (!is_enabled()) return;

		var anchor = event.target.closest && event.target.closest("a[href]");
		if (!anchor) return;

		var href = anchor.getAttribute("href") || "";
		if (!href || href === "#" || href.indexOf("javascript:") === 0) return;
		if (
			href.indexOf("/app/timesheet/" + target_new_docname()) !== -1 ||
			href.indexOf(window.location.origin + "/app/timesheet/" + target_new_docname()) !== -1
		) return;

		if (
			href.indexOf("/app") === 0 ||
			href.indexOf("/desk") === 0 ||
			href.indexOf(window.location.origin + "/app") === 0 ||
			href.indexOf(window.location.origin + "/desk") === 0
		) {
			event.preventDefault();
			event.stopImmediatePropagation();
			route_to_target();
		}
	}

	function inject_styles() {
		if (document.getElementById(STYLE_ID)) return;

		var style = document.createElement("style");
		style.id = STYLE_ID;
		style.textContent = [
			"body.alumicraft-kiosk-mode .body-sidebar,",
			"body.alumicraft-kiosk-mode .body-sidebar-container,",
			"body.alumicraft-kiosk-mode .desk-sidebar,",
			"body.alumicraft-kiosk-mode .layout-side-section,",
			"body.alumicraft-kiosk-mode .list-sidebar,",
			"body.alumicraft-kiosk-mode .form-sidebar,",
			"body.alumicraft-kiosk-mode #navbar-breadcrumbs,",
			"body.alumicraft-kiosk-mode .navbar .navbar-home,",
			"body.alumicraft-kiosk-mode .navbar .app-logo,",
			"body.alumicraft-kiosk-mode .navbar .search-bar,",
			"body.alumicraft-kiosk-mode .navbar .dropdown-help,",
			"body.alumicraft-kiosk-mode .navbar .dropdown-notifications,",
			"body.alumicraft-kiosk-mode .navbar .dropdown-avatar,",
			"body.alumicraft-kiosk-mode .page-head .breadcrumb,",
			"body.alumicraft-kiosk-mode .page-head .page-icon-group,",
			"body.alumicraft-kiosk-mode .page-head .menu-btn-group,",
			"body.alumicraft-kiosk-mode .page-head .form-links,",
			"body.alumicraft-kiosk-mode .page-head .list-paging-area {",
			"\tdisplay: none !important;",
			"}",
			"body.alumicraft-kiosk-mode .layout-main-section-wrapper,",
			"body.alumicraft-kiosk-mode .layout-main-section {",
			"\twidth: 100% !important;",
			"\tmax-width: 100% !important;",
			"}",
			"body.alumicraft-kiosk-mode .page-container {",
			"\tpadding-left: 0 !important;",
			"}",
		].join("\n");
		document.head.appendChild(style);
	}

	function apply_chrome_lock() {
		if (!is_enabled() || !document.body) return;

		document.body.classList.add("alumicraft-kiosk-mode");
		inject_styles();
	}

	function on_route_change() {
		setTimeout(function () {
			apply_chrome_lock();
			enforce_route();
		}, 50);
	}

	function init() {
		if (!is_enabled()) return;

		apply_chrome_lock();
		document.addEventListener("click", prevent_desk_navigation, true);

		if (frappe.router && typeof frappe.router.on === "function") {
			frappe.router.on("change", on_route_change);
		} else if (window.$) {
			$(document).on("page-change", on_route_change);
		}

		on_route_change();
	}

	if (window.$) {
		$(document).ready(init);
	} else {
		document.addEventListener("DOMContentLoaded", init);
	}
})();
