/**
 * Prepare a new Timesheet for a kiosk session without reading Employee.
 *
 * ERPNext normally looks up the current user's Employee record when the form
 * opens. Kiosk sessions are intentionally denied that read, so the server puts
 * only the Timesheet identity fields in boot data and this handler applies them
 * before ERPNext's onload handler runs.
 */
(function () {
	"use strict";

	var REDIRECTING_AFTER_SAVE = false;
	var IDENTITY_FIELDS = [
		"employee",
		"employee_name",
		"department",
		"company",
		"user",
	];

	function get_config() {
		return (frappe.boot && frappe.boot.alumicraft_kiosk) || {};
	}

	function is_enabled() {
		return !!get_config().enabled;
	}

	function apply_safe_defaults(frm) {
		if (!is_enabled() || !frm.is_new()) return;

		var defaults = get_config().timesheet_defaults || {};
		IDENTITY_FIELDS.forEach(function (fieldname) {
			if (Object.prototype.hasOwnProperty.call(defaults, fieldname)) {
				frm.doc[fieldname] = defaults[fieldname];
			}
		});

	}

	function lock_identity_fields(frm) {
		if (!is_enabled()) return;

		["employee", "user"].forEach(function (fieldname) {
			if (frm.fields_dict[fieldname]) {
				// Frappe's normal new-document link sweep would otherwise read the
				// underlying Employee/User record. Other link defaults keep their
				// standard validation.
				frm.fields_dict[fieldname].df.ignore_link_validation = 1;
			}
		});

		IDENTITY_FIELDS.forEach(function (fieldname) {
			if (frm.fields_dict[fieldname]) {
				frm.set_df_property(fieldname, "read_only", 1);
			}
		});
	}

	function open_fresh_timesheet() {
		if (!is_enabled() || REDIRECTING_AFTER_SAVE) return;

		REDIRECTING_AFTER_SAVE = true;
		setTimeout(function () {
			frappe.set_route(["Form", "Timesheet", "new-timesheet"]);
			REDIRECTING_AFTER_SAVE = false;
		}, 0);
	}

	frappe.ui.form.on("Timesheet", {
		setup: lock_identity_fields,
		before_load: apply_safe_defaults,
		refresh: lock_identity_fields,
		after_save: open_fresh_timesheet,
		on_submit: open_fresh_timesheet,
	});
})();
