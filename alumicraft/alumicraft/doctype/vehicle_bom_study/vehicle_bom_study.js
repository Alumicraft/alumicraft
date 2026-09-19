const SOURCE_CONFIG_FIELDS = [
	"title",
	"company",
	"standard_description",
	"mode",
	"projects",
	"from_date",
	"to_date",
];

const ESTIMATE_FIELDS = [
	"material_allowance",
	"overhead_allowance",
	"target_margin",
];

const ACTIVE_STATUSES = ["Queued", "Running"];

function settings_link() {
	return frappe.utils.get_form_link(
		"Vehicle BOM Settings",
		"Vehicle BOM Settings",
		true,
		__("Vehicle BOM Settings")
	);
}

function is_current_form(frm) {
	const route = frappe.get_route && frappe.get_route();
	return (
		frm.$wrapper &&
		frm.$wrapper.is(":visible") &&
		route &&
		route[0] === "Form" &&
		route[1] === "Vehicle BOM Study" &&
		route[2] === frm.doc.name
	);
}

function clear_status_timer(frm) {
	if (frm.__vehicle_bom_status_timer) {
		clearInterval(frm.__vehicle_bom_status_timer);
		frm.__vehicle_bom_status_timer = null;
	}
}

function schedule_status_refresh(frm) {
	clear_status_timer(frm);
	if (!ACTIVE_STATUSES.includes(frm.doc.status)) return;

	frm.__vehicle_bom_status_timer = setInterval(() => {
		if (!is_current_form(frm)) {
			clear_status_timer(frm);
			return;
		}
		frm.reload_doc();
	}, 10000);
}

function set_intro(frm) {
	const settings = settings_link();
	const status = frm.doc.status || "Draft";
	let message;
	let indicator = "blue";

	switch (status) {
		case "Queued":
			message = __(
				"Queued: the background job will collect a submitted historical snapshot. No live BOM, price, invoice, or timesheet is changed."
			);
			break;
		case "Running":
			message = __(
				"Running: the study is processing historical evidence in the background. You can return here while it runs."
			);
			break;
		case "Needs Review":
			message = __(
				"Needs Review: approve or exclude the suggested material and labor lines. Historical purchases are evidence, never proof of installed quantity. Suggested retail is calculated only when both materials and labor have active lines, every active line is reviewed, and each active line has Cost Known checked. Otherwise 0 means unavailable, not free."
			);
			break;
		case "Failed":
			indicator = "red";
			message = __(
				"Failed: review the error below and use Resume when the issue is resolved."
			);
			break;
		default:
			message = __(
				"Draft: save this study before building it. The snapshot is procurement evidence and does not guarantee installed quantities. Optional Jev settings are available in {0}.",
				[settings]
			);
	}

	frm.set_intro(message, indicator);
}

function lock_snapshot_inputs(frm) {
	const source_locked = frm.doc.status && frm.doc.status !== "Draft";
	const estimate_locked = ACTIVE_STATUSES.includes(frm.doc.status);
	SOURCE_CONFIG_FIELDS.forEach((fieldname) => {
		frm.set_df_property(fieldname, "read_only", source_locked ? 1 : 0);
		frm.refresh_field(fieldname);
	});
	ESTIMATE_FIELDS.forEach((fieldname) => {
		frm.set_df_property(fieldname, "read_only", estimate_locked ? 1 : 0);
		frm.refresh_field(fieldname);
	});
}

function set_action_dirty_state(button, frm) {
	if (!button) return;
	const disabled = frm.is_dirty() || frm.is_new();
	button.prop("disabled", disabled);
	button.attr(
		"title",
		disabled ? __("Save the study before starting or resuming it.") : ""
	);
}

function start_study(frm, action_label) {
	if (frm.is_dirty() || frm.is_new()) {
		frappe.msgprint(__("Save the study before {0}.", [action_label.toLowerCase()]));
		return;
	}

	frappe
		.call({
			method: "alumicraft.bom.service.start",
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: __("Queueing study…"),
		})
		.then(() => {
			frappe.show_alert({
				message: __("Study queued. It will continue in the background."),
				indicator: "blue",
			});
			frm.reload_doc();
		});
}

function recover_study(frm) {
	if (frm.is_dirty() || frm.is_new()) {
		frappe.msgprint(__("Save the study before recovering it."));
		return;
	}

	frappe
		.call({
			method: "alumicraft.bom.service.recover",
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: __("Checking the background job…"),
		})
		.then(() => frm.reload_doc());
}

function add_start_button(frm, label) {
	const button = frm.add_custom_button(__(label), () => start_study(frm, label));
	set_action_dirty_state(button, frm);
	return button;
}

frappe.ui.form.on("Vehicle BOM Study", {
	refresh(frm) {
		clear_status_timer(frm);
		set_intro(frm);
		lock_snapshot_inputs(frm);

		const action_buttons = [];
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			action_buttons.push(add_start_button(frm, "Build Draft"));
		}
		if (frm.doc.status === "Failed" && !frm.is_new()) {
			action_buttons.push(add_start_button(frm, "Resume"));
		}
		if (ACTIVE_STATUSES.includes(frm.doc.status) && !frm.is_new()) {
			const recover_button = frm.add_custom_button(
				__("Recover Interrupted Run"),
				() => recover_study(frm)
			);
			set_action_dirty_state(recover_button, frm);
			action_buttons.push(recover_button);
		}
		if (frm.doc.status === "Needs Review" && !frm.is_new()) {
			frm.add_custom_button(__("Export CSV"), () => {
				if (frm.is_dirty() || frm.is_new()) {
					frappe.msgprint(__("Save the study before exporting CSV."));
					return;
				}
				const url =
					"/api/method/alumicraft.bom.service.export_csv?name=" +
					encodeURIComponent(frm.doc.name);
				window.open(url, "_blank");
			});
		}

		frm.$wrapper
			.off("change.vehicle_bom_study input.vehicle_bom_study")
			.on(
				"change.vehicle_bom_study input.vehicle_bom_study",
				"input, textarea, select",
				() => action_buttons.forEach((button) => set_action_dirty_state(button, frm))
			);

		schedule_status_refresh(frm);
	},

	before_unload(frm) {
		clear_status_timer(frm);
	},
});

frappe.ui.form.on("Vehicle BOM Material", {
	unit_cost(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (Number(row.unit_cost) > 0 && !row.cost_known) {
			frappe.model.set_value(cdt, cdn, "cost_known", 1);
		}
	},
});

frappe.ui.form.on("Vehicle BOM Labor", {
	hourly_cost(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (Number(row.hourly_cost) > 0 && !row.cost_known) {
			frappe.model.set_value(cdt, cdn, "cost_known", 1);
		}
	},
});
