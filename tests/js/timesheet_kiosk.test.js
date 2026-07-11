const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
	path.resolve(__dirname, "../../alumicraft/public/js/timesheet_kiosk.js"),
	"utf8"
);

function makeHarness({ enabled = true } = {}) {
	let handlers;
	const routes = [];
	const frappe = {
		boot: {
			alumicraft_kiosk: {
				enabled,
				timesheet_defaults: {
					company: "Alumicraft",
					department: "Fabrication",
					employee: "EMP-0001",
					employee_name: "Terminal Operator",
					user: "kiosk@drivealumicraft.com",
				},
			},
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
	};

	vm.runInNewContext(
		script,
		{ clearTimeout, frappe, setTimeout },
		{ filename: "timesheet_kiosk.js" }
	);

	const readOnlyFields = [];
	const frm = {
		doc: {
			__run_link_triggers: 1,
			doctype: "Timesheet",
			name: "new-timesheet-test",
		},
		fields_dict: {
			company: { df: {} },
			department: { df: {} },
			employee: { df: {} },
			employee_name: { df: {} },
			user: { df: {} },
		},
		is_new() {
			return true;
		},
		set_df_property(fieldname, property, value) {
			readOnlyFields.push([fieldname, property, value]);
		},
	};

	return { frappe, frm, handlers, readOnlyFields, routes };
}

test("kiosk new forms receive safe boot defaults before ERPNext onload", () => {
	const harness = makeHarness();

	harness.handlers.before_load(harness.frm);

	assert.equal(harness.frm.doc.employee, "EMP-0001");
	assert.equal(harness.frm.doc.company, "Alumicraft");
	assert.equal(harness.frm.doc.employee_name, "Terminal Operator");
	assert.equal(harness.frm.doc.department, "Fabrication");
	assert.equal(harness.frm.doc.user, "kiosk@drivealumicraft.com");
	assert.equal(harness.frm.doc.__run_link_triggers, 1);
});

test("kiosk identity fields are read-only", () => {
	const harness = makeHarness();

	harness.handlers.setup(harness.frm);

	assert.equal(harness.frm.fields_dict.employee.df.ignore_link_validation, 1);
	assert.equal(harness.frm.fields_dict.user.df.ignore_link_validation, 1);
	assert.deepEqual(harness.readOnlyFields, [
		["employee", "read_only", 1],
		["employee_name", "read_only", 1],
		["department", "read_only", 1],
		["company", "read_only", 1],
		["user", "read_only", 1],
	]);
});

test("a saved kiosk entry opens a fresh Timesheet", async () => {
	const harness = makeHarness();

	harness.handlers.after_save();
	await new Promise((resolve) => setTimeout(resolve, 5));

	assert.deepEqual(JSON.parse(JSON.stringify(harness.routes)), [
		["Form", "Timesheet", "new-timesheet"],
	]);
});

test("normal users keep standard Timesheet behavior", async () => {
	const harness = makeHarness({ enabled: false });
	const original = { ...harness.frm.doc };

	harness.handlers.before_load(harness.frm);
	harness.handlers.setup(harness.frm);
	harness.handlers.refresh(harness.frm);
	harness.handlers.after_save();
	await new Promise((resolve) => setTimeout(resolve, 5));

	assert.deepEqual(harness.frm.doc, original);
	assert.deepEqual(harness.readOnlyFields, []);
	assert.deepEqual(harness.routes, []);
});
