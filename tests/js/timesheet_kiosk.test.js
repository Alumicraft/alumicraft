const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
	path.resolve(__dirname, "../../alumicraft/public/js/timesheet_kiosk.js"),
	"utf8"
);

function makeHarness({ enabled = true, isNew = true } = {}) {
	let handlers;
	const routes = [];
	const readOnlyFields = [];
	const refreshedFields = [];
	const queryCallbacks = {};
	const removed = { employeeOpen: 0, wrapperOpen: 0 };
	const identityRequests = [];
	const linkTitles = {};
	const displayedEmployees = [];
	let employeeInput = "";
	const window = {};
	const frappe = {
		boot: {
			alumicraft_kiosk: {
				company: "Alumicraft",
				enabled,
			},
		},
		call(options) {
			identityRequests.push(options);
			return Promise.resolve({
				message: {
					company: "Alumicraft",
					department: "Fabrication",
					employee_name: "Alice Active",
					name: options.args.employee,
				},
			});
		},
		set_route(route) {
			routes.push(route);
			return Promise.resolve();
		},
		ui: {
			form: {
				on(doctype, registeredHandlers) {
					assert.equal(doctype, "Timesheet");
					handlers = registeredHandlers;
				},
			},
		},
		utils: {
			add_link_title(doctype, name, value) {
				linkTitles[`${doctype}::${name}`] = value;
			},
		},
	};

	vm.runInNewContext(
		script,
		{ clearTimeout, frappe, setTimeout, window },
		{ filename: "timesheet_kiosk.js" }
	);

	const employeeField = {
		df: {},
		label: null,
		last_value: null,
		title_value_map: {},
		$input: {
			val(value) {
				if (arguments.length) {
					employeeInput = value;
					return this;
				}
				return employeeInput;
			},
		},
		$link_open: {
			remove() {
				removed.employeeOpen += 1;
			},
		},
		$wrapper: {
			find(selector) {
				assert.equal(selector, "a.btn-open, .btn-open");
				return {
					remove() {
						removed.wrapperOpen += 1;
					},
				};
			},
		},
		translate_and_set_input_value(label, value) {
			this.title_value_map[label] = value;
			this.$input.val(label);
			displayedEmployees.push([label, value]);
		},
		get_label_value() {
			return this.$input.val();
		},
		set_input_value(value) {
			this.$input.val(value);
		},
	};
	const frm = {
		doc: {
			__run_link_triggers: 1,
			doctype: "Timesheet",
			name: "new-timesheet-test",
		},
		fields_dict: {
			company: { df: {} },
			department: { df: {} },
			employee: employeeField,
			employee_name: { df: {} },
			user: { df: {} },
		},
		is_new() {
			return isNew;
		},
		refresh_field(fieldname) {
			refreshedFields.push(fieldname);
		},
		set_df_property(fieldname, property, value) {
			readOnlyFields.push([fieldname, property, value]);
		},
		set_query(fieldname, callback) {
			queryCallbacks[fieldname] = callback;
		},
	};

	return {
		employeeField,
		displayedEmployees,
		frappe,
		frm,
		handlers,
		identityRequests,
		linkTitles,
		queryCallbacks,
		readOnlyFields,
		refreshedFields,
		removed,
		routes,
		window,
	};
}

test("new kiosk forms use a temporary sentinel, then expose a blank employee selector", () => {
	const harness = makeHarness();

	harness.handlers.before_load(harness.frm);

	assert.equal(harness.frm.doc.employee, "\u2063");
	assert.equal(Boolean(harness.frm.doc.employee), true);
	assert.equal(harness.frm.__alumicraft_employee_bootstrap, true);
	assert.equal(harness.frm.doc.company, "Alumicraft");
	assert.equal(harness.frm.doc.user, null);
	assert.equal(harness.linkTitles["Employee::\u2063"], "\u2063");

	// Frappe's initial forced Link sweep can fire this event before ERPNext's
	// onload. It must not clear or fetch anything during the sentinel window.
	harness.frm.doc.employee_name = "Unchanged during bootstrap";
	harness.handlers.employee(harness.frm);
	assert.equal(harness.frm.doc.employee_name, "Unchanged during bootstrap");

	harness.handlers.onload_post_render(harness.frm);

	assert.equal(harness.frm.doc.employee, null);
	assert.equal(harness.frm.__alumicraft_employee_bootstrap, undefined);
	assert.equal(harness.refreshedFields.includes("employee"), true);
	assert.equal(
		harness.readOnlyFields.some(
			(entry) =>
				entry[0] === "employee" &&
				entry[1] === "read_only" &&
				entry[2] === 0
		),
		true
	);
});

test("employee selection uses only the controlled query and no link validation", () => {
	const harness = makeHarness();

	harness.handlers.before_load(harness.frm);

	assert.equal(harness.employeeField.df.ignore_link_validation, 1);
	assert.equal(harness.employeeField.df.only_select, 1);
	assert.equal(harness.frm.fields_dict.user.df.ignore_link_validation, 1);
	assert.equal(typeof harness.queryCallbacks.employee, "function");
	assert.deepEqual(
		JSON.parse(JSON.stringify(harness.queryCallbacks.employee())),
		{ query: "alumicraft.permissions.search_kiosk_employees" }
	);
	assert.ok(harness.removed.employeeOpen > 0);
	assert.ok(harness.removed.wrapperOpen > 0);
	assert.equal(
		harness.readOnlyFields.some(
			(entry) =>
				entry[0] === "user" && entry[1] === "hidden" && entry[2] === 1
		),
		true
	);
});

test("employee selection fills only Timesheet identity fields and shows the employee name", async () => {
	const harness = makeHarness();

	harness.handlers.before_load(harness.frm);
	harness.handlers.onload_post_render(harness.frm);
	harness.frm.doc.employee = "EMP-0042";
	harness.frm.doc.employee_name = "Previous Employee";
	harness.frm.doc.department = "Previous Department";
	harness.frm.doc.company = "Another Company";
	harness.frm.doc.user = "other@example.com";
	await harness.handlers.employee(harness.frm);

	assert.equal(harness.frm.doc.employee, "EMP-0042");
	assert.equal(harness.frm.doc.employee_name, "Alice Active");
	assert.equal(harness.frm.doc.department, "Fabrication");
	assert.equal(harness.frm.doc.company, "Alumicraft");
	assert.equal(harness.frm.doc.user, null);
	assert.equal(harness.frm.__alumicraft_selected_employee, "EMP-0042");
	assert.equal(harness.employeeField.last_value, "EMP-0042");
	assert.equal(
		harness.employeeField.title_value_map["Alice Active"],
		"EMP-0042"
	);
	assert.equal(harness.identityRequests.length, 1);
	assert.equal(
		harness.identityRequests[0].method,
		"alumicraft.permissions.get_kiosk_employee_identity"
	);
	assert.deepEqual(harness.displayedEmployees.at(-1), [
		"Alice Active",
		"EMP-0042",
	]);
	assert.equal(
		harness.linkTitles["Employee::EMP-0042"],
		"Alice Active"
	);
	assert.ok(harness.refreshedFields.includes("employee_name"));
	assert.ok(harness.refreshedFields.includes("department"));

	harness.frm.doc.company = "Wrong Company";
	harness.frm.doc.user = "someone@example.com";
	harness.handlers.refresh(harness.frm);
	assert.equal(harness.frm.doc.company, "Alumicraft");
	assert.equal(harness.frm.doc.user, null);
	assert.deepEqual(harness.displayedEmployees.at(-1), [
		"Alice Active",
		"EMP-0042",
	]);
});

test("employee ID is pinned before identity lookup and survives an early blur", async () => {
	const harness = makeHarness();
	let resolveIdentity;
	harness.frappe.call = (options) => {
		harness.identityRequests.push(options);
		return new Promise((resolve) => {
			resolveIdentity = resolve;
		});
	};

	harness.handlers.before_load(harness.frm);
	harness.handlers.onload_post_render(harness.frm);
	harness.employeeField.label = "Alice Active";
	harness.employeeField.$input.val("Alice Active");
	harness.frm.doc.employee = "EMP-0042";
	const selection = harness.handlers.employee(harness.frm);

	assert.equal(harness.frm.__alumicraft_selected_employee, "EMP-0042");
	assert.equal(harness.frm.doc.employee, "EMP-0042");
	assert.equal(harness.employeeField.last_value, "EMP-0042");
	assert.equal(
		harness.employeeField.title_value_map["Alice Active"],
		"EMP-0042"
	);

	// Reproduce Frappe writing a blank from the Link input before the request
	// returns. The kiosk handler restores the picker selection synchronously.
	harness.frm.doc.employee = null;
	const blurRecovery = harness.handlers.employee(harness.frm);
	assert.equal(blurRecovery, selection);
	assert.equal(harness.frm.doc.employee, "EMP-0042");
	assert.equal(harness.identityRequests.length, 1);

	resolveIdentity({
		message: {
			company: "Alumicraft",
			department: "Fabrication",
			employee_name: "Alice Active",
			name: "EMP-0042",
		},
	});
	await selection;
	assert.equal(harness.frm.doc.employee, "EMP-0042");
	assert.equal(harness.frm.doc.employee_name, "Alice Active");
});

test("save hooks restore the canonical Employee ID after control state is lost", async () => {
	const harness = makeHarness();

	harness.handlers.before_load(harness.frm);
	harness.handlers.onload_post_render(harness.frm);
	harness.frm.doc.employee = "EMP-0042";
	await harness.handlers.employee(harness.frm);

	for (const eventName of ["validate", "before_save", "before_submit"]) {
		harness.frm.doc.employee = null;
		harness.employeeField.last_value = null;
		harness.employeeField.title_value_map = {};
		harness.employeeField.$input.val("");

		await harness.handlers[eventName](harness.frm);

		assert.equal(harness.frm.doc.employee, "EMP-0042");
		assert.equal(harness.employeeField.last_value, "EMP-0042");
		assert.equal(
			harness.employeeField.title_value_map["Alice Active"],
			"EMP-0042"
		);
	}
});

test("a slower employee response cannot overwrite a newer selection", async () => {
	const harness = makeHarness();
	const pending = [];
	harness.frappe.call = (options) =>
		new Promise((resolve) => pending.push({ options, resolve }));

	harness.handlers.before_load(harness.frm);
	harness.handlers.onload_post_render(harness.frm);

	harness.frm.doc.employee = "EMP-OLD";
	const oldRequest = harness.handlers.employee(harness.frm);
	harness.frm.doc.employee = "EMP-NEW";
	const newRequest = harness.handlers.employee(harness.frm);

	pending[1].resolve({
		message: {
			company: "Alumicraft",
			department: "Assembly",
			employee_name: "New Employee",
			name: "EMP-NEW",
		},
	});
	await newRequest;
	pending[0].resolve({
		message: {
			company: "Alumicraft",
			department: "Fabrication",
			employee_name: "Old Employee",
			name: "EMP-OLD",
		},
	});
	await oldRequest;

	assert.equal(harness.frm.doc.employee, "EMP-NEW");
	assert.equal(harness.frm.doc.employee_name, "New Employee");
	assert.equal(harness.frm.doc.department, "Assembly");
	assert.deepEqual(harness.displayedEmployees.at(-1), [
		"New Employee",
		"EMP-NEW",
	]);
});

test("a saved kiosk entry opens one fresh Timesheet", async () => {
	const harness = makeHarness();

	harness.handlers.after_save();
	harness.handlers.after_save();
	await new Promise((resolve) => setTimeout(resolve, 5));

	assert.deepEqual(JSON.parse(JSON.stringify(harness.routes)), [
		["Form", "Timesheet", "new-timesheet"],
	]);
});

test("normal users keep standard Timesheet behavior", async () => {
	const harness = makeHarness({ enabled: false });
	const original = { ...harness.frm.doc };

	harness.handlers.setup(harness.frm);
	harness.handlers.before_load(harness.frm);
	harness.handlers.onload_post_render(harness.frm);
	harness.handlers.refresh(harness.frm);
	harness.handlers.employee(harness.frm);
	harness.handlers.after_save();
	await new Promise((resolve) => setTimeout(resolve, 5));

	assert.deepEqual(harness.frm.doc, original);
	assert.deepEqual(harness.readOnlyFields, []);
	assert.deepEqual(harness.refreshedFields, []);
	assert.deepEqual(harness.queryCallbacks, {});
	assert.deepEqual(harness.removed, { employeeOpen: 0, wrapperOpen: 0 });
	assert.deepEqual(harness.identityRequests, []);
	assert.deepEqual(harness.routes, []);
});
