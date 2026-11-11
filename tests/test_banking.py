import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


class DuplicateTransaction(Exception):
    pass


@pytest.fixture
def validate(monkeypatch):
    frappe = ModuleType("frappe")
    frappe._ = str
    frappe.db = SimpleNamespace()
    frappe.throw = lambda message: pytest.fail(message)
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.delitem(sys.modules, "alumicraft.banking", raising=False)
    return importlib.import_module("alumicraft.banking").validate_bank_transaction_id, frappe


def bank_line(transaction_id, *, existing=False, changed=()):
    return SimpleNamespace(
        name="BANK-TX-TEST" if existing else None,
        bank_account="Checking",
        transaction_id=transaction_id,
        is_new=lambda: not existing,
        has_value_changed=lambda fieldname: fieldname in changed,
    )


def test_duplicate_source_id_locks_only_its_bank_account(validate):
    check, frappe = validate
    calls = []

    def lock(doctype, name, field, *, for_update):
        calls.append(("lock", doctype, name, field, for_update))

    def find(doctype, filters, field, *, pluck, for_update):
        calls.append(("find", doctype, filters, field, pluck, for_update))
        return ["statement-001"]

    def reject(message):
        raise DuplicateTransaction(message)

    frappe.db.get_value = lock
    frappe.db.get_values = find
    frappe.throw = reject
    with pytest.raises(DuplicateTransaction, match="already exists for this bank account"):
        check(bank_line("statement-001"))

    assert calls == [
        ("lock", "Bank Account", "Checking", "name", True),
        (
            "find",
            "Bank Transaction",
            {"bank_account": "Checking", "transaction_id": "statement-001", "docstatus": ["!=", 2]},
            "transaction_id",
            True,
            True,
        ),
    ]


def test_existing_statement_id_is_checked_only_when_changed(validate):
    check, frappe = validate
    check(bank_line(None))
    check(bank_line("statement-001", existing=True))

    frappe.db.get_value = lambda *args, **kwargs: "Checking"

    def find(doctype, filters, field, **kwargs):
        assert filters["name"] == ["!=", "BANK-TX-TEST"]
        return ["STATEMENT-001"]

    frappe.db.get_values = find
    check(bank_line("statement-001", existing=True, changed=("transaction_id",)))


def test_numeric_id_matches_the_value_stored_in_a_data_field(validate):
    check, frappe = validate
    frappe.db.get_value = lambda *args, **kwargs: "Checking"
    frappe.db.get_values = lambda *args, **kwargs: ["0"]

    def reject(message):
        raise DuplicateTransaction(message)

    frappe.throw = reject
    with pytest.raises(DuplicateTransaction):
        check(bank_line(0))
