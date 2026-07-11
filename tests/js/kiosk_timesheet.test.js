const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
	path.resolve(__dirname, "../../alumicraft/public/js/kiosk_timesheet.js"),
	"utf8"
);

function makeHarness({ enabled = true, pathname = "/desk/timesheet/new-timesheet" } = {}) {
	const origin = "https://backdesk.drivealumicraft.com";
	const listeners = {};
	const replaceCalls = [];
	const classes = new Set();
	const insertedElements = new Map();
	const calls = {
		globalSetRoute: 0,
		pushState: 0,
		route: 0,
		routePaths: [],
		setRoute: 0,
	};

	const location = {
		hostname: "backdesk.drivealumicraft.com",
		origin,
		pathname,
		search: "",
		hash: "",
	};
	Object.defineProperty(location, "href", {
		get() {
			return origin + location.pathname + location.search + location.hash;
		},
	});

	function setLocation(value) {
		const url = new URL(value, origin);
		location.pathname = url.pathname;
		location.search = url.search;
		location.hash = url.hash;
	}

	const document = {
		title: "Alumicraft",
		body: {
			classList: {
				add(name) {
					classes.add(name);
				},
				toggle(name, force) {
					if (force) classes.add(name);
					else classes.delete(name);
				},
			},
		},
		head: {
			appendChild(element) {
				insertedElements.set(element.id, element);
			},
		},
		addEventListener(name, callback) {
			listeners[name] = listeners[name] || [];
			listeners[name].push(callback);
		},
		createElement(tagName) {
			return { tagName, id: "", textContent: "" };
		},
		getElementById(id) {
			return insertedElements.get(id) || null;
		},
		querySelectorAll() {
			return [];
		},
	};

	let currentRoute = ["Form", "Timesheet", "new-timesheet"];
	const router = {
		on() {},
		push_state() {
			calls.pushState += 1;
		},
		route() {
			calls.route += 1;
			calls.routePaths.push(location.pathname);
			return Promise.resolve();
		},
		set_route() {
			calls.setRoute += 1;
			return Promise.resolve();
		},
	};
	const frappe = {
		boot: {
			alumicraft_kiosk: {
				enabled,
				target_doctype: "Timesheet",
				target_route: ["Form", "Timesheet", "new-timesheet"],
			},
		},
		get_route() {
			return currentRoute;
		},
		open_in_new_tab: false,
		route_flags: {},
		route_hash: null,
		route_options: null,
		router,
	};
	frappe.set_route = function () {
		calls.globalSetRoute += 1;
		return frappe.router.set_route.apply(frappe.router, arguments);
	};

	const history = {
		state: { test: true },
		replaceState(state, _title, value) {
			this.state = state;
			replaceCalls.push(value);
			setLocation(value);
		},
	};
	const window = {
		frappe,
		history,
		location,
	};
	const context = {
		Promise,
		URL,
		clearTimeout,
		console,
		decodeURIComponent,
		document,
		encodeURIComponent,
		history,
		setTimeout,
		window,
	};

	const originals = {
		globalSetRoute: frappe.set_route,
		pushState: router.push_state,
		route: router.route,
		setRoute: router.set_route,
	};

	vm.runInNewContext(script, context, { filename: "kiosk_timesheet.js" });

	return {
		calls,
		classes,
		document,
		frappe,
		listeners,
		location,
		originals,
		replaceCalls,
		router,
		setCurrentRoute(route) {
			currentRoute = route;
		},
		setLocation,
		window,
	};
}

test("normal users keep the untouched Frappe router", () => {
	const harness = makeHarness({ enabled: false, pathname: "/desk/user" });

	assert.equal(harness.location.pathname, "/desk/user");
	assert.deepEqual(harness.replaceCalls, []);
	assert.equal(harness.router.route, harness.originals.route);
	assert.equal(harness.router.set_route, harness.originals.setRoute);
	assert.equal(harness.router.push_state, harness.originals.pushState);
	assert.equal(harness.frappe.set_route, harness.originals.globalSetRoute);
	assert.equal(harness.listeners.keydown, undefined);
});

test("a forbidden hard-load URL is replaced before Frappe routes it", () => {
	const harness = makeHarness({ pathname: "/desk/employee/EMP-0001" });

	assert.equal(harness.location.pathname, "/desk/timesheet/new-timesheet");
	assert.deepEqual(harness.replaceCalls, ["/desk/timesheet/new-timesheet"]);
	assert.equal(harness.calls.route, 0);
	assert.equal(harness.window.__alumicraft_kiosk_debug.router_guard_installed, true);
});

test("Back or Forward replaces a forbidden URL before the original router sees it", async () => {
	const harness = makeHarness();
	harness.setLocation("/desk/user/view/list");

	await harness.router.route();

	assert.equal(harness.location.pathname, "/desk/timesheet/new-timesheet");
	assert.equal(harness.calls.route, 1);
	assert.deepEqual(harness.calls.routePaths, ["/desk/timesheet/new-timesheet"]);
	assert.equal(harness.replaceCalls.at(-1), "/desk/timesheet/new-timesheet");
});

test("set_route blocks forbidden requests and allows only new Timesheet forms", async () => {
	const harness = makeHarness();
	harness.frappe.open_in_new_tab = true;
	harness.frappe.route_options = { user: "kiosk@example.com" };

	await harness.frappe.set_route(["List", "User", "List"]);

	assert.equal(harness.calls.globalSetRoute, 0);
	assert.equal(harness.calls.setRoute, 0);
	assert.equal(harness.frappe.open_in_new_tab, false);
	assert.equal(harness.frappe.route_options, null);
	assert.equal(harness.location.pathname, "/desk/timesheet/new-timesheet");

	await harness.frappe.set_route(["Form", "Timesheet", "new-timesheet-abc123"]);

	assert.equal(harness.calls.globalSetRoute, 1);
	assert.equal(harness.calls.setRoute, 1);
});

test("direct push_state cannot add a forbidden history entry", async () => {
	const harness = makeHarness();

	await harness.router.push_state("/desk/employee");
	assert.equal(harness.calls.pushState, 0);

	harness.router.push_state("/desk/timesheet/new-timesheet-generated");
	assert.equal(harness.calls.pushState, 1);
});

test("Cmd/Ctrl+K and Cmd/Ctrl+G are stopped in capture phase", () => {
	const harness = makeHarness();
	const keydown = harness.listeners.keydown[0];
	const calls = { immediate: 0, prevent: 0, stop: 0 };
	const event = {
		ctrlKey: true,
		key: "G",
		metaKey: false,
		preventDefault() {
			calls.prevent += 1;
		},
		stopImmediatePropagation() {
			calls.immediate += 1;
		},
		stopPropagation() {
			calls.stop += 1;
		},
	};

	keydown(event);
	assert.deepEqual(calls, { immediate: 1, prevent: 1, stop: 1 });
	assert.equal(harness.window.__alumicraft_kiosk_debug.last_blocked_shortcut, "g");

	keydown({ ...event, ctrlKey: false, key: "K", metaKey: true });
	assert.deepEqual(calls, { immediate: 2, prevent: 2, stop: 2 });
});

test("forbidden links, including new-tab gestures, are stopped before Frappe", () => {
	const harness = makeHarness();
	const click = harness.listeners.click[0];
	const calls = { immediate: 0, prevent: 0, stop: 0 };
	const anchor = {
		getAttribute() {
			return "/desk/employee/EMP-0001";
		},
	};
	const event = {
		metaKey: true,
		target: {
			closest() {
				return anchor;
			},
		},
		preventDefault() {
			calls.prevent += 1;
		},
		stopImmediatePropagation() {
			calls.immediate += 1;
		},
		stopPropagation() {
			calls.stop += 1;
		},
	};

	click(event);

	assert.deepEqual(calls, { immediate: 1, prevent: 1, stop: 1 });
	assert.equal(harness.calls.route, 0);
});

test("only generated/new Timesheet route shapes are allowed", () => {
	const harness = makeHarness();
	const debug = harness.window.__alumicraft_kiosk_debug;

	assert.equal(debug.is_allowed_path("/desk/timesheet/new-timesheet"), true);
	assert.equal(debug.is_allowed_path("/desk/timesheet/new-timesheet-ydluavtjrr"), true);
	assert.equal(debug.is_allowed_path("/desk/timesheet/TS-0001"), false);
	assert.equal(debug.is_allowed_path("/desk/employee/EMP-0001"), false);
	assert.equal(debug.is_allowed_route(["Form", "Timesheet", "new-timesheet-x"]), true);
	assert.equal(debug.is_allowed_route(["List", "Timesheet", "List"]), false);
});
