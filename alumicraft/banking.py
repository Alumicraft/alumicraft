"""Keep a bank's transaction ID unique among active statement lines."""

import frappe
from frappe import _


def validate_bank_transaction_id(doc, method=None):
    bank_account = doc.bank_account
    if not bank_account or doc.transaction_id is None:
        return
    transaction_id = str(doc.transaction_id)
    if not transaction_id.strip():
        return
    if not (
        doc.is_new()
        or doc.has_value_changed("bank_account")
        or doc.has_value_changed("transaction_id")
    ):
        return

    # Serialize checks for this account across simultaneous statement imports.
    frappe.db.get_value("Bank Account", bank_account, "name", for_update=True)
    filters = {
        "bank_account": bank_account,
        "transaction_id": transaction_id,
        "docstatus": ["!=", 2],
    }
    if doc.name:
        filters["name"] = ["!=", doc.name]
    stored_ids = frappe.db.get_values(
        "Bank Transaction", filters, "transaction_id", pluck=True, for_update=True
    )
    if transaction_id in stored_ids:
        frappe.throw(_("This transaction ID already exists for this bank account."))
