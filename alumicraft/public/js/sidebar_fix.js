/**
 * Frappe v16 Sidebar Fix
 *
 * 1. Applies sidebar item filters as frappe.route_options on click
 *    (Frappe ignores the `filters` field during navigation)
 * 2. Corrects active-state highlighting for duplicate-doctype items
 * 3. Prevents unwanted workspace switching when navigating to a DocType
 *    that lives in another module's workspace
 * 4. On hard-refresh, picks the correct workspace based on which sidebar
 *    actually lists the current doctype (preferring last-used)
 */
(function () {
	"use strict";

	var _initialized = false;
	var _last_clicked = null;

	// ── Helpers ────────────────────────────────────────────────────────

	function parse_filters(str) {
		if (!str) return [];
		try { var r = JSON.parse(str); return Array.isArray(r) ? r : []; }
		catch (e) { return []; }
	}

	function get_workspace_name() {
		if (frappe.app && frappe.app.sidebar && frappe.app.sidebar.current_workspace) {
			return frappe.app.sidebar.current_workspace;
		}
		var el = document.querySelector(".body-sidebar[data-title]");
		if (el) return el.getAttribute("data-title").toLowerCase();
		return null;
	}

	function get_all_items() {
		var ws = get_workspace_name();
		if (!ws) return [];
		var data = (frappe.boot.workspace_sidebar_item || {})[ws];
		if (!data || !data.items) return [];
		// Flatten top-level + nested items
		var all = [];
		for (var i = 0; i < data.items.length; i++) {
			all.push(data.items[i]);
			var nested = data.items[i].nested_items || [];
			for (var j = 0; j < nested.length; j++) all.push(nested[j]);
		}
		return all;
	}

	function set_active(container) {
		// Frappe v16 uses .active-sidebar on .standard-sidebar-item
		var sb = document.querySelector(".body-sidebar-container");
		if (!sb) return;
		var all = sb.querySelectorAll(".standard-sidebar-item");
		for (var i = 0; i < all.length; i++) all[i].classList.remove("active-sidebar");
		var target = container.querySelector(".standard-sidebar-item") || container;
		target.classList.add("active-sidebar");
	}

	function find_dom_by_label(label) {
		var sb = document.querySelector(".body-sidebar-container");
		if (!sb) return null;
		var els = sb.querySelectorAll(".sidebar-item-container");
		for (var i = 0; i < els.length; i++) {
			var lbl = els[i].querySelector(".sidebar-item-label");
			if (lbl && lbl.textContent.trim() === label) return els[i];
		}
		return null;
	}

	// ── Click handler (capture phase) ──────────────────────────────────

	function on_click(e) {
		var container = e.target.closest(".sidebar-item-container");
		if (!container) return;
		var lbl_el = container.querySelector(".sidebar-item-label");
		if (!lbl_el) return;
		var label = lbl_el.textContent.trim();

		var items = get_all_items();
		var item = null;
		for (var i = 0; i < items.length; i++) {
			if (items[i].label === label) { item = items[i]; break; }
		}
		if (!item || item.type !== "Link") return;

		// Apply filters as route_options before Frappe navigates
		if (item.filters) {
			var filters = parse_filters(item.filters);
			if (filters.length) {
				var opts = {};
				for (var i = 0; i < filters.length; i++) {
					var f = filters[i];
					opts[f[1]] = f[2] === "=" ? f[3] : [f[2], f[3]];
				}
				frappe.route_options = opts;
			}
		}

		// Remember which workspace this doctype was clicked from, so a hard
		// refresh of /app/<doctype> returns to the same workspace.
		if (item.link_to) remember_doctype_workspace(item.link_to);

		// Track click — poll every 200ms for 5s to enforce active state
		// (Frappe rebuilds sidebar DOM during nav; MutationObserver alone fails)
		_last_clicked = { label: label, link_to: item.link_to };
		var _attempts = 0;
		var _poll = setInterval(function () {
			_attempts++;
			if (_attempts > 25 || !_last_clicked) { clearInterval(_poll); return; }
			var el = find_dom_by_label(_last_clicked.label);
			if (!el) return;
			var inner = el.querySelector(".standard-sidebar-item");
			if (inner && !inner.classList.contains("active-sidebar")) {
				set_active(el);
			}
		}, 200);
		setTimeout(function () { _last_clicked = null; }, 5000);
	}

	// ── Active state on route change ───────────────────────────────────

	function fix_active() {
		if (_last_clicked) {
			var dt = (typeof cur_list !== "undefined" && cur_list) ? cur_list.doctype : null;
			if (dt && dt === _last_clicked.link_to) {
				var el = find_dom_by_label(_last_clicked.label);
				if (el) { set_active(el); return; }
			}
			_last_clicked = null;
		}

		if (typeof cur_list === "undefined" || !cur_list || !cur_list.doctype) return;
		var items = get_all_items();
		var matches = [];
		for (var i = 0; i < items.length; i++) {
			if (items[i].type === "Link" && items[i].link_to === cur_list.doctype)
				matches.push(items[i]);
		}
		if (matches.length <= 1) return;

		var cur_filters = [];
		try { cur_filters = cur_list.filter_area.get(); } catch (e) { return; }

		var best = null, best_score = -1;
		for (var i = 0; i < matches.length; i++) {
			var mf = parse_filters(matches[i].filters);
			if (!mf.length && !cur_filters.length) {
				if (0 > best_score) { best_score = 0; best = matches[i]; }
				continue;
			}
			if (!mf.length) continue;
			var score = 0, ok = true;
			for (var j = 0; j < mf.length; j++) {
				var f = mf[j], found = false;
				for (var k = 0; k < cur_filters.length; k++) {
					var cf = cur_filters[k];
					if (cf[1] === f[1] && cf[2] === f[2] && String(cf[3]) === String(f[3]))
					{ found = true; break; }
				}
				if (found) score++; else { ok = false; break; }
			}
			if (ok && score > best_score) { best_score = score; best = matches[i]; }
		}
		if (best) {
			var el = find_dom_by_label(best.label);
			if (el) set_active(el);
		}
	}

	function fix_active_retry(n) {
		if (n <= 0) return;
		if (typeof cur_list !== "undefined" && cur_list && cur_list.filter_area) fix_active();
		else setTimeout(function () { fix_active_retry(n - 1); }, 200);
	}

	// ── Prevent unwanted workspace switching ───────────────────────────
	// Frappe's set_workspace_sidebar uses URL slugs (e.g. "home-build-request")
	// to look up sidebars by `link_to` (e.g. "Home Build Request"). The slug
	// match never fires, so it falls through to module-based switching.
	// Patch it to only run that logic when explicitly navigating to a workspace.

	function patch_workspace_switch() {
		if (!frappe.app || !frappe.app.sidebar) return false;
		var sb = frappe.app.sidebar;
		if (sb._sidebar_fix_patched) return true;
		if (typeof sb.set_workspace_sidebar !== "function") return false;
		if (typeof sb.setup !== "function") return false;

		var original = sb.set_workspace_sidebar.bind(sb);
		var original_setup = sb.setup.bind(sb);

		sb.set_workspace_sidebar = function (router) {
			try {
				var route = frappe.get_route() || [];
				var map = frappe.boot.workspace_sidebar_item || {};
				var slug = "";

				if (route.length === 1) {
					slug = (route[0] || "").toLowerCase();
				} else if (route.length >= 2 && (route[0] || "").toLowerCase() === "workspaces") {
					return original(router);
				}

				var is_workspace_nav = slug && !!map[slug];

				if (is_workspace_nav || !sb.sidebar_title) {
					return original(router);
				}

				// Otherwise keep the user on their current workspace —
				// just refresh which sidebar item is highlighted.
				sb.set_active_workspace_item();
			} catch (e) {
				console.log("Sidebar fix patch error:", e);
				return original(router);
			}
		};

		// Safety net: Frappe also invokes sidebar.setup(workspace) from
		// other paths. If any fire during a doctype navigation, block the
		// rebuild and just refresh highlighting instead.
		sb.setup = function (workspace_title) {
			try {
				var route = frappe.get_route() || [];
				var is_doctype_view =
					route.indexOf("List") !== -1 ||
					route.indexOf("Form") !== -1 ||
					route.indexOf("query-report") !== -1 ||
					route.indexOf("dashboard-view") !== -1 ||
					route.indexOf("Tree") !== -1;

				if (is_doctype_view && sb.sidebar_title &&
					workspace_title !== sb.sidebar_title) {
					if (typeof sb.set_active_workspace_item === "function") {
						sb.set_active_workspace_item();
					}
					return;
				}
			} catch (e) {
				console.log("Sidebar fix setup-patch error:", e);
			}
			return original_setup(workspace_title);
		};

		sb._sidebar_fix_patched = true;
		sb._sidebar_fix_original_setup = original_setup;
		return true;
	}

	function try_patch_workspace_switch(n) {
		if (n <= 0) return;
		if (!patch_workspace_switch()) {
			setTimeout(function () { try_patch_workspace_switch(n - 1); }, 300);
		}
	}

	// ── Pick the right workspace on (hard) refresh ─────────────────────

	function find_candidate_workspaces(entity) {
		var map = frappe.boot.workspace_sidebar_item || {};
		var out = [];
		Object.keys(map).forEach(function (key) {
			var data = map[key];
			if (!data || !data.items) return;
			var matched = false;
			for (var i = 0; i < data.items.length && !matched; i++) {
				var item = data.items[i];
				if (item && item.link_to === entity) { matched = true; break; }
				var nested = (item && item.nested_items) || [];
				for (var j = 0; j < nested.length; j++) {
					if (nested[j] && nested[j].link_to === entity) { matched = true; break; }
				}
			}
			if (matched) out.push(data.label || key);
		});
		return out;
	}

	// Per-doctype workspace memory: when the user clicks a sidebar item
	// linking to doctype D from workspace W, remember {D: W}. On hard
	// refresh of /app/<D>, prefer that workspace over the global last-used.
	var DOCTYPE_MAP_KEY = "sidebar_fix_doctype_workspace";
	var GLOBAL_KEY = "sidebar_fix_last_workspace";

	function read_doctype_map() {
		try {
			var raw = localStorage.getItem(DOCTYPE_MAP_KEY);
			if (!raw) return {};
			var parsed = JSON.parse(raw);
			return parsed && typeof parsed === "object" ? parsed : {};
		} catch (e) { return {}; }
	}

	function remember_doctype_workspace(doctype) {
		if (!doctype) return;
		try {
			var ws = frappe.app && frappe.app.sidebar && frappe.app.sidebar.sidebar_title;
			if (!ws) return;
			var map = read_doctype_map();
			if (map[doctype] === ws) return;
			map[doctype] = ws;
			localStorage.setItem(DOCTYPE_MAP_KEY, JSON.stringify(map));
		} catch (e) {}
	}

	function pick_correct_workspace() {
		try {
			var route = frappe.get_route() || [];
			if (route.length < 2) return null; // workspace URL; trust Frappe
			var entity = route[1];
			if (!entity) return null;

			var candidates = find_candidate_workspaces(entity);
			if (!candidates.length) return null;
			if (candidates.length === 1) return candidates[0];

			// Per-doctype hint wins over the global last-used.
			var map = read_doctype_map();
			if (map[entity] && candidates.indexOf(map[entity]) !== -1) {
				return map[entity];
			}

			var last = null;
			try { last = localStorage.getItem(GLOBAL_KEY); } catch (e) {}
			if (last && candidates.indexOf(last) !== -1) return last;
			return candidates[0];
		} catch (e) {
			return null;
		}
	}

	function save_last_workspace() {
		try {
			if (frappe.app && frappe.app.sidebar && frappe.app.sidebar.sidebar_title) {
				localStorage.setItem(GLOBAL_KEY, frappe.app.sidebar.sidebar_title);
			}
		} catch (e) {}
	}

	function fix_initial_workspace() {
		if (!frappe.app || !frappe.app.sidebar) return;
		var sb = frappe.app.sidebar;
		var correct = pick_correct_workspace();
		if (!correct) return;
		if (sb.sidebar_title === correct) return;
		var setup = sb._sidebar_fix_original_setup;
		if (typeof setup !== "function") return;
		try { setup(correct); } catch (e) { console.log("Sidebar fix initial-workspace error:", e); }
	}

	function fix_initial_workspace_retry(n) {
		if (n <= 0) return;
		if (frappe.app && frappe.app.sidebar && frappe.app.sidebar._sidebar_fix_patched) {
			fix_initial_workspace();
		} else {
			setTimeout(function () { fix_initial_workspace_retry(n - 1); }, 300);
		}
	}

	// ── Init ───────────────────────────────────────────────────────────

	function init() {
		var sb = document.querySelector(".body-sidebar-container");
		if (!sb) return false;
		if (_initialized) return true;
		_initialized = true;

		try_patch_workspace_switch(20);
		fix_initial_workspace_retry(20);
		$(window).on("beforeunload", save_last_workspace);

		sb.addEventListener("click", on_click, true);

		// MutationObserver: when Frappe sets active-sidebar on the wrong item,
		// override it if we have a _last_clicked target.
		var _overriding = false;
		var observer = new MutationObserver(function () {
			if (!_last_clicked || _overriding) return;
			var correct = find_dom_by_label(_last_clicked.label);
			if (!correct) return;
			var inner = correct.querySelector(".standard-sidebar-item") || correct;
			if (inner.classList.contains("active-sidebar")) return;
			_overriding = true;
			set_active(correct);
			setTimeout(function () { _overriding = false; }, 50);
		});
		observer.observe(sb, { attributes: true, attributeFilter: ["class"], subtree: true });

		var on_route = function () {
			setTimeout(function () { fix_active_retry(5); }, 300);
			setTimeout(save_last_workspace, 500);
		};
		if (frappe.router && typeof frappe.router.on === "function")
			frappe.router.on("change", on_route);
		else
			$(document).on("page-change", on_route);

		setTimeout(function () { fix_active_retry(5); }, 300);
		return true;
	}

	function try_init(n) {
		if (n <= 0) return;
		if (!init()) setTimeout(function () { try_init(n - 1); }, 500);
	}

	$(document).ready(function () { try_init(10); });
})();
