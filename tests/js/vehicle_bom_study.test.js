const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const scriptPath = path.resolve(
	__dirname,
		"../../alumicraft/alumicraft/doctype/vehicle_bom_study/vehicle_bom_study.js"
);
const schemaPath = path.resolve(
	__dirname,
		"../../alumicraft/alumicraft/doctype/vehicle_bom_study/vehicle_bom_study.json"
);
const materialSchemaPath = path.resolve(
	__dirname,
		"../../alumicraft/alumicraft/doctype/vehicle_bom_material/vehicle_bom_material.json"
);

function loadHandlers() {
	const handlers = {};
	let timerId = 0;
	const frappe = {
		ui: {
			form: {
				on(doctype, events) {
					handlers[doctype] = events;
				},
			},
		},
		utils: {
			get_form_link(_doctype, name) {
				return `<a href="/desk/${name}">${name}</a>`;
			},
		},
		get_route() {
			return ["Form", "Vehicle BOM Study", "BOM-STUDY-00001"];
		},
		msgprint() {},
		show_alert() {},
	};

	vm.runInNewContext(
		fs.readFileSync(scriptPath, "utf8"),
		{
			frappe,
			clearInterval() {},
			console,
			__(value) {
				return value;
			},
			setInterval() {
				return ++timerId;
			},
			window: {},
		},
		{ filename: scriptPath }
	);
	return handlers;
}

function makeForm({ status, dirty = false }) {
	const properties = {};
	const buttons = [];
	const messages = [];
	return {
		doc: { name: "BOM-STUDY-00001", status },
		properties,
		buttons,
		messages,
		$wrapper: {
			is() {
				return true;
			},
			off() {
				return this;
			},
			on() {
				return this;
			},
		},
		is_dirty() {
			return dirty;
		},
		is_new() {
			return false;
		},
		set_intro() {},
		set_df_property(fieldname, property, value) {
			properties[fieldname] = properties[fieldname] || {};
			properties[fieldname][property] = value;
		},
		refresh_field() {},
		add_custom_button(label, handler) {
			const button = {
				label,
				handler,
				prop() {
					return this;
				},
				attr() {
					return this;
				},
			};
			buttons.push(button);
			return button;
		},
	};
}

test("uses a plain expression naming series for BOM studies", () => {
	const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
	assert.equal(schema.autoname, "BOM-STUDY-.#####");
});

test("new material review rows start as Unknown classifications", () => {
	const fields = Object.fromEntries(
		JSON.parse(fs.readFileSync(materialSchemaPath, "utf8")).fields.map((field) => [
			field.fieldname,
			field,
		])
	);
	assert.equal(fields.assembly.default, "Unknown");
	assert.equal(fields.purpose.default, "Unknown");
});

test("keeps costing controls editable during review", () => {
	const handlers = loadHandlers();
	const frm = makeForm({ status: "Needs Review" });
	handlers["Vehicle BOM Study"].refresh(frm);

	assert.equal(frm.properties.title.read_only, 1);
	assert.equal(frm.properties.projects.read_only, 1);
	assert.equal(frm.properties.material_allowance.read_only, 0);
	assert.equal(frm.properties.overhead_allowance.read_only, 0);
	assert.equal(frm.properties.target_margin.read_only, 0);
});

test("locks costing controls while a study is active", () => {
	const handlers = loadHandlers();
	const frm = makeForm({ status: "Running" });
	handlers["Vehicle BOM Study"].refresh(frm);

	assert.equal(frm.properties.title.read_only, 1);
	assert.equal(frm.properties.material_allowance.read_only, 1);
	assert.equal(frm.properties.overhead_allowance.read_only, 1);
	assert.equal(frm.properties.target_margin.read_only, 1);
});

test("does not export unsaved review edits", () => {
	const handlers = loadHandlers();
	const frm = makeForm({ status: "Needs Review", dirty: true });
	handlers["Vehicle BOM Study"].refresh(frm);

	const exportButton = frm.buttons.find((button) => button.label === "Export CSV");
	assert.ok(exportButton);
	assert.doesNotThrow(() => exportButton.handler());
});
