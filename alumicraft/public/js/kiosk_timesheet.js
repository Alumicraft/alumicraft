/**
 * Restrict employee kiosk users to a newly generated Timesheet form.
 *
 * Server-side boot code decides whether kiosk mode is enabled for the session.
 * The early guards in this file run before Frappe starts its Desk router so a
 * forbidden URL is replaced before Frappe can parse, load, render, or log it.
 */
(function () {
	"use strict";

	var VERSION = "20260710-2";
	var STYLE_ID = "alumicraft-kiosk-style";
	var ROUTING = false;
	var ROUTER_PATCHED = false;
	var INTERACTION_GUARDS_INSTALLED = false;
	var BOOTSTRAP_ENFORCE_COUNT = 0;
	var ROUTER_PATCH_ATTEMPTS = 0;

	window.__alumicraft_kiosk_debug = window.__alumicraft_kiosk_debug || {};
	window.__alumicraft_kiosk_debug.version = VERSION;
	window.__alumicraft_kiosk_debug.blocked_routes =
		window.__alumicraft_kiosk_debug.blocked_routes || 0;

	function get_frappe() {
		return window.frappe || {};
	}

	function get_config() {
		var frappe = get_frappe();
		return (frappe.boot && frappe.boot.alumicraft_kiosk) || {};
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

	function slug(value) {
		return normalize(value).replace(/ /g, "-");
	}

	function safe_decode(value) {
		try {
			return decodeURIComponent(value);
		} catch (_error) {
			return value;
		}
	}

	function split_route_value(value) {
		var route = String(value || "");
		var origin = window.location && window.location.origin;

		if (origin && route.indexOf(origin) === 0) {
			route = route.slice(origin.length);
		}

		route = route.split("?")[0].split("#")[0];
		return route
			.split("/")
			.filter(function (part) {
				return part !== "";
			})
			.map(safe_decode);
	}

	function without_desk_prefix(parts) {
		parts = (parts || []).slice();
		if (["desk", "app"].indexOf(normalize(parts[0])) !== -1) {
			parts.shift();
		}
		return parts;
	}

	function is_managed_desk_path(path) {
		var parts = split_route_value(path);
		return ["desk", "app"].indexOf(normalize(parts[0])) !== -1;
	}

	function is_new_target_docname(docname) {
		if (!docname) return false;

		var normalized = normalize(docname);
		var target = normalize(target_new_docname());
		return normalized === target || normalized.indexOf(target + "-") === 0;
	}

	function is_allowed_raw_route(route) {
		route = without_desk_prefix(route || []);
		return (
			route.length === 2 &&
			normalize(route[0]) === slug(target_doctype()) &&
			is_new_target_docname(route[1])
		);
	}

	function is_allowed_path(path) {
		if (!is_managed_desk_path(path)) return false;
		return is_allowed_raw_route(split_route_value(path));
	}

	function is_allowed_route(route) {
		route = route || [];
		if (!route.length) return false;

		if (normalize(route[0]) === "form") {
			return (
				route.length === 3 &&
				normalize(route[1]) === normalize(target_doctype()) &&
				is_new_target_docname(route[2])
			);
		}

		return is_allowed_raw_route(route);
	}

	function route_from_arguments(args) {
		var route = Array.prototype.slice.call(args || []);

		if (route.length === 1 && Array.isArray(route[0])) {
			route = route[0].slice();
		} else if (route.length === 1 && typeof route[0] === "string") {
			route = split_route_value(route[0]);
		}

		return without_desk_prefix(route);
	}

	function is_allowed_route_request(args) {
		return is_allowed_route(route_from_arguments(args));
	}

	function target_path() {
		return (
			"/desk/" +
			encodeURIComponent(slug(target_doctype())) +
			"/" +
			encodeURIComponent(target_new_docname())
		);
	}

	function is_allowed_location() {
		return !!(window.location && is_allowed_path(window.location.pathname));
	}

	function clear_route_side_effects() {
		var frappe = get_frappe();
		frappe.route_options = null;
		frappe.route_hash = null;
		frappe.open_in_new_tab = false;
		frappe.route_flags = {};
	}

	function record_block(source) {
		var debug = window.__alumicraft_kiosk_debug;
		debug.blocked_routes += 1;
		debug.last_block_source = source;
		debug.last_blocked_path = window.location && window.location.pathname;
	}

	function set_route_blocking(blocked) {
		if (!document.body) return;
		document.body.classList.toggle("alumicraft-kiosk-redirecting", !!blocked);
	}

	function replace_forbidden_location(source) {
		if (!is_enabled() || !window.location) return false;
		if (!is_managed_desk_path(window.location.pathname) || is_allowed_location()) {
			return false;
		}

		record_block(source);
		clear_route_side_effects();
		set_route_blocking(true);
		window.history.replaceState(window.history.state, document.title, target_path());
		return true;
	}

	function resolved_promise() {
		return Promise.resolve();
	}

	function finish_routing(result) {
		function finish() {
			ROUTING = false;
			if (is_allowed_location()) {
				set_route_blocking(false);
			}
		}

		if (result && typeof result.then === "function") {
			result.then(finish, finish);
		} else {
			setTimeout(finish, 0);
		}
		return result;
	}

	function render_target_route() {
		var frappe = get_frappe();
		if (ROUTING || !frappe.router || typeof frappe.router.route !== "function") {
			return resolved_promise();
		}

		ROUTING = true;
		set_route_blocking(true);
		return finish_routing(frappe.router.route());
	}

	function block_route_request(source) {
		record_block(source);
		clear_route_side_effects();

		if (replace_forbidden_location(source + ":location")) {
			return render_target_route();
		}

		return resolved_promise();
	}

	function patch_router_guards() {
		if (!is_enabled() || ROUTER_PATCHED) return ROUTER_PATCHED;

		var frappe = get_frappe();
		var router = frappe.router;
		if (
			!router ||
			typeof router.route !== "function" ||
			typeof router.set_route !== "function" ||
			typeof router.push_state !== "function" ||
			typeof frappe.set_route !== "function"
		) {
			return false;
		}

		var original_route = router.route;
		var original_set_route = router.set_route;
		var original_push_state = router.push_state;
		var original_global_set_route = frappe.set_route;
		router.route = function () {
			var replaced = replace_forbidden_location("router.route");
			var result = original_route.apply(this, arguments);
			return replaced ? finish_routing(result) : result;
		};

		router.set_route = function () {
			if (!is_allowed_route_request(arguments)) {
				return block_route_request("router.set_route");
			}
			return original_set_route.apply(this, arguments);
		};

		router.push_state = function (path) {
			if (!is_allowed_path(path)) {
				return block_route_request("router.push_state");
			}
			return original_push_state.apply(this, arguments);
		};

		frappe.set_route = function () {
			if (!is_allowed_route_request(arguments)) {
				return block_route_request("frappe.set_route");
			}
			return original_global_set_route.apply(this, arguments);
		};

		ROUTER_PATCHED = true;
		window.__alumicraft_kiosk_debug.router_guard_installed = true;
		return true;
	}

	function install_router_guards() {
		if (!is_enabled() || patch_router_guards()) return;

		ROUTER_PATCH_ATTEMPTS += 1;
		if (ROUTER_PATCH_ATTEMPTS < 50) {
			setTimeout(install_router_guards, 0);
		}
	}

	function get_internal_anchor(event) {
		var anchor = event.target && event.target.closest && event.target.closest("a[href]");
		if (!anchor) return null;

		var href = anchor.getAttribute("href") || "";
		if (!href || href === "#" || href.indexOf("javascript:") === 0) return null;

		try {
			var url = new URL(href, window.location.href);
			if (url.origin !== window.location.origin) return null;
			return url;
		} catch (_error) {
			return null;
		}
	}

	function prevent_desk_navigation(event) {
		if (!is_enabled()) return;

		var url = get_internal_anchor(event);
		if (!url || !is_managed_desk_path(url.pathname) || is_allowed_path(url.pathname)) {
			return;
		}

		event.preventDefault();
		event.stopPropagation();
		event.stopImmediatePropagation();
		record_block("desk-link");
		clear_route_side_effects();

		if (!is_allowed_location()) {
			replace_forbidden_location("desk-link:location");
			render_target_route();
		}
	}

	function prevent_search_shortcuts(event) {
		if (!is_enabled() || (!event.ctrlKey && !event.metaKey)) return;

		var key = normalize(event.key);
		if (key !== "k" && key !== "g") return;

		event.preventDefault();
		event.stopPropagation();
		event.stopImmediatePropagation();
		window.__alumicraft_kiosk_debug.last_blocked_shortcut = key;
	}

	function install_interaction_guards() {
		if (!is_enabled() || INTERACTION_GUARDS_INSTALLED) return;

		INTERACTION_GUARDS_INSTALLED = true;
		document.addEventListener("click", prevent_desk_navigation, true);
		document.addEventListener("auxclick", prevent_desk_navigation, true);
		document.addEventListener("keydown", prevent_search_shortcuts, true);
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
			"body.alumicraft-kiosk-mode .navbar .search-bar,",
			"body.alumicraft-kiosk-mode .navbar .dropdown-help,",
			"body.alumicraft-kiosk-mode .navbar .dropdown-notifications,",
			"body.alumicraft-kiosk-mode .navbar .dropdown-avatar,",
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
			"body.alumicraft-kiosk-mode.alumicraft-kiosk-redirecting .page-container,",
			"body.alumicraft-kiosk-mode.alumicraft-kiosk-redirecting .layout-main-section-wrapper,",
			"body.alumicraft-kiosk-mode.alumicraft-kiosk-redirecting .layout-main-section {",
			"\tvisibility: hidden !important;",
			"}",
			"body.alumicraft-kiosk-mode .navbar .navbar-home,",
			"body.alumicraft-kiosk-mode .navbar .navbar-home *,",
			"body.alumicraft-kiosk-mode #navbar-breadcrumbs,",
			"body.alumicraft-kiosk-mode #navbar-breadcrumbs *,",
			"body.alumicraft-kiosk-mode .page-head .breadcrumb,",
			"body.alumicraft-kiosk-mode .page-head .breadcrumb *,",
			"body.alumicraft-kiosk-mode .page-head .page-title,",
			"body.alumicraft-kiosk-mode .page-head .page-title * {",
			"\tpointer-events: none !important;",
			"\tcursor: default !important;",
			"}",
		].join("\n");
		document.head.appendChild(style);
	}

	function make_native_header_read_only() {
		["alumicraft-kiosk-logo", "alumicraft-kiosk-page-label"].forEach(function (id) {
			var el = document.getElementById(id);
			if (el) el.remove();
		});

		document
			.querySelectorAll(
				[
					".navbar .navbar-home",
					"#navbar-breadcrumbs a",
					".page-head .breadcrumb a",
					".page-head .page-title a",
				].join(",")
			)
			.forEach(function (el) {
				el.setAttribute("aria-disabled", "true");
				el.setAttribute("tabindex", "-1");
			});
	}

	function apply_chrome_lock() {
		if (!is_enabled() || !document.body) return;

		document.body.classList.add("alumicraft-kiosk-mode");
		inject_styles();
		make_native_header_read_only();
	}

	function enforce_route() {
		if (!is_enabled()) return;

		if (replace_forbidden_location("enforce-route")) {
			render_target_route();
			return;
		}

		var frappe = get_frappe();
		if (!frappe.get_route) return;

		var route = frappe.get_route() || [];
		if (!route.length) return;

		if (is_allowed_route(route)) {
			set_route_blocking(false);
			return;
		}

		record_block("enforce-current-route");
		render_target_route();
	}

	function on_route_change() {
		apply_chrome_lock();
		enforce_route();
	}

	function bootstrap_enforce_route() {
		if (!is_enabled()) return;
		BOOTSTRAP_ENFORCE_COUNT += 1;
		on_route_change();

		if (BOOTSTRAP_ENFORCE_COUNT < 20) {
			setTimeout(bootstrap_enforce_route, 250);
		}
	}

	function init() {
		if (!is_enabled()) return;

		apply_chrome_lock();
		install_interaction_guards();
		install_router_guards();

		var frappe = get_frappe();
		if (frappe.router && typeof frappe.router.on === "function") {
			frappe.router.on("change", on_route_change);
		} else if (window.$) {
			window.$(document).on("page-change", on_route_change);
		}

		bootstrap_enforce_route();
	}

	function install_early_guards() {
		if (!is_enabled()) return;

		apply_chrome_lock();
		install_interaction_guards();
		install_router_guards();
		replace_forbidden_location("initial-load");
	}

	window.__alumicraft_kiosk_debug.is_allowed_path = is_allowed_path;
	window.__alumicraft_kiosk_debug.is_allowed_route = is_allowed_route;
	window.__alumicraft_kiosk_debug.target_path = target_path;

	// This must happen synchronously, before Frappe's document-ready startup.
	install_early_guards();

	if (window.$) {
		window.$(document).ready(init);
	} else {
		document.addEventListener("DOMContentLoaded", init);
	}
})();
