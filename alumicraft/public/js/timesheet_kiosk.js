/**
 * Keep the shared terminal on a blank Timesheet without exposing Employee or
 * User documents. Employees remain selectable through one server-controlled
 * projection that returns only active employee IDs and names.
 */
(function () {
	"use strict";

	if (window.__alumicraft_timesheet_kiosk_loaded__) return;
	window.__alumicraft_timesheet_kiosk_loaded__ = true;

	var EMPLOYEE_SENTINEL = "\u2063";
	var EMPLOYEE_SEARCH_QUERY =
		"alumicraft.permissions.search_kiosk_employees";
	var EMPLOYEE_IDENTITY_METHOD =
		"alumicraft.permissions.get_kiosk_employee_identity";
	var DERIVED_FIELDS = ["employee_name", "department"];
	var REDIRECTING_AFTER_SAVE = false;

	function get_config() {
		return (frappe.boot && frappe.boot.alumicraft_kiosk) || {};
	}

	function is_enabled() {
		return !!get_config().enabled;
	}

	function refresh_field(frm, fieldname) {
		if (typeof frm.refresh_field === "function") {
			frm.refresh_field(fieldname);
		}
	}

	function set_directly(frm, fieldname, value) {
		if (frm.doc[fieldname] === value) return;
		frm.doc[fieldname] = value;
		refresh_field(frm, fieldname);
	}

	function remove_employee_open_button(frm) {
		var field = frm.fields_dict.employee;
		if (!field) return;

		if (field.$link_open && typeof field.$link_open.remove === "function") {
			field.$link_open.remove();
		}
		if (field.$wrapper && typeof field.$wrapper.find === "function") {
			field.$wrapper.find("a.btn-open, .btn-open").remove();
		}
	}

	function show_employee_label(frm, employee, employee_name) {
		if (!employee || !employee_name) return;

		if (frappe.utils && typeof frappe.utils.add_link_title === "function") {
			frappe.utils.add_link_title("Employee", employee, employee_name);
		}

		var field = frm.fields_dict.employee;
		if (
			field &&
			typeof field.translate_and_set_input_value === "function"
		) {
			field.translate_and_set_input_value(employee_name, employee);
		}
	}

	function configure_fields(frm) {
		if (!is_enabled()) return;

		var employee = frm.fields_dict.employee;
		if (employee) {
			employee.df.ignore_link_validation = 1;
			employee.df.only_select = 1;
		}

		var user = frm.fields_dict.user;
		if (user) {
			user.df.ignore_link_validation = 1;
		}

		if (typeof frm.set_query === "function") {
			frm.set_query("employee", function () {
				return { query: EMPLOYEE_SEARCH_QUERY };
			});
		} else if (employee) {
			employee.get_query = function () {
				return { query: EMPLOYEE_SEARCH_QUERY };
			};
		}

		if (typeof frm.set_df_property === "function") {
			frm.set_df_property("employee", "read_only", frm.is_new() ? 0 : 1);
			["company", "employee_name", "department", "user"].forEach(
				function (fieldname) {
					if (frm.fields_dict[fieldname]) {
						frm.set_df_property(fieldname, "read_only", 1);
					}
				}
			);
			if (frm.fields_dict.user) {
				frm.set_df_property("user", "hidden", 1);
			}
		}

		remove_employee_open_button(frm);
		if (frm.doc.employee && frm.__alumicraft_employee_label) {
			show_employee_label(
				frm,
				frm.doc.employee,
				frm.__alumicraft_employee_label
			);
		}
	}

	function apply_shared_terminal_defaults(frm) {
		if (!is_enabled() || !frm.is_new()) return;

		set_directly(frm, "company", get_config().company || null);
		set_directly(frm, "user", null);

		if (!frm.doc.employee) {
			// ERPNext normally looks up Employee by the current login during
			// onload. A visually empty, temporary truthy value makes that stock
			// handler skip the forbidden lookup. It is removed after rendering.
			if (frappe.utils && typeof frappe.utils.add_link_title === "function") {
				frappe.utils.add_link_title(
					"Employee",
					EMPLOYEE_SENTINEL,
					EMPLOYEE_SENTINEL
				);
			}
			frm.doc.employee = EMPLOYEE_SENTINEL;
			frm.__alumicraft_employee_bootstrap = true;
		}
	}

	function finish_employee_bootstrap(frm) {
		if (!is_enabled() || !frm.__alumicraft_employee_bootstrap) return;

		if (frm.doc.employee === EMPLOYEE_SENTINEL) {
			frm.doc.employee = null;
		}
		delete frm.__alumicraft_employee_bootstrap;
		refresh_field(frm, "employee");
		remove_employee_open_button(frm);
	}

	function employee_changed(frm) {
		if (
			!is_enabled() ||
			frm.__alumicraft_employee_bootstrap ||
			frm.doc.employee === EMPLOYEE_SENTINEL
		) {
			return;
		}

		var selected_employee = frm.doc.employee;
		frm.__alumicraft_employee_request =
			(frm.__alumicraft_employee_request || 0) + 1;
		var request_number = frm.__alumicraft_employee_request;

		// Clear stale values immediately if the operator changes their selection.
		DERIVED_FIELDS.forEach(function (fieldname) {
			set_directly(frm, fieldname, null);
		});
		delete frm.__alumicraft_employee_label;
		set_directly(frm, "company", get_config().company || null);
		set_directly(frm, "user", null);
		remove_employee_open_button(frm);

		if (!selected_employee) return;

		return frappe
			.call({
				method: EMPLOYEE_IDENTITY_METHOD,
				type: "POST",
				args: { employee: selected_employee },
			})
			.then(function (response) {
				var identity = (response && response.message) || {};
				if (
					frm.__alumicraft_employee_request !== request_number ||
					frm.doc.employee !== selected_employee ||
					identity.name !== selected_employee
				) {
					return;
				}

				frm.__alumicraft_employee_label = identity.employee_name;
				set_directly(frm, "employee_name", identity.employee_name || null);
				set_directly(frm, "department", identity.department || null);
				set_directly(frm, "company", identity.company || null);
				show_employee_label(
					frm,
					selected_employee,
					identity.employee_name
				);
				remove_employee_open_button(frm);
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
		setup: configure_fields,
		before_load: function (frm) {
			configure_fields(frm);
			apply_shared_terminal_defaults(frm);
		},
		onload_post_render: finish_employee_bootstrap,
		refresh: function (frm) {
			configure_fields(frm);
			if (is_enabled() && frm.is_new()) {
				set_directly(frm, "company", get_config().company || null);
				set_directly(frm, "user", null);
			}
		},
		employee: employee_changed,
		after_save: open_fresh_timesheet,
		on_submit: open_fresh_timesheet,
	});
})();
