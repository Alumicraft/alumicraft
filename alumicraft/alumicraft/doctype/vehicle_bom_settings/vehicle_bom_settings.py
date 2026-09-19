import frappe
from frappe import _
from frappe.model.document import Document


class VehicleBOMSettings(Document):
	def validate(self):
		self._validate_range("batch_size", 1, 50)
		self._validate_range("concurrency", 1, 8)
		self._validate_range("max_requests", 1, 10000)
		self._validate_range("timeout", 1, 120)
		self._validate_range("confidence_threshold", 0, 1)

	def _validate_range(self, fieldname, minimum, maximum):
		value = self.get(fieldname)
		if value is None or value == "":
			return

		try:
			numeric_value = float(value)
		except (TypeError, ValueError):
			frappe.throw(_("{0} must be a number.").format(self.meta.get_label(fieldname)))

		if not minimum <= numeric_value <= maximum:
			frappe.throw(
				_("{0} must be between {1} and {2}.").format(
					self.meta.get_label(fieldname), minimum, maximum
				)
			)
