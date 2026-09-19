import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class Context(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class PortalPageTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.local = Context(flags=Context())
        self.frappe.session = Context(user="manager@example.com")
        self.frappe.Redirect = type("Redirect", (Exception,), {})
        self.frappe.get_cached_value = Mock(return_value="Workshop Manager")
        self.sessions = types.ModuleType("frappe.sessions")
        self.sessions.get_csrf_token = Mock(return_value="session-csrf")
        self.portal = types.ModuleType("alumicraft.bom.portal")
        self.portal.require_access = Mock()
        modules = {"frappe": self.frappe, "frappe.sessions": self.sessions,
                   "alumicraft.bom.portal": self.portal}
        path = Path(__file__).parents[1] / "alumicraft/www/vehicle_bom.py"
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("isolated_bom_page", path)
            self.page = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.page)

    def test_guest_redirects_before_loading_identity_or_csrf(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(self.frappe.Redirect):
            self.page.get_context(Context())
        self.assertEqual(self.frappe.local.flags.redirect_location, "/login?redirect-to=%2Fvehicle-bom")
        self.frappe.get_cached_value.assert_not_called()
        self.sessions.get_csrf_token.assert_not_called()

    def test_authenticated_unauthorized_user_gets_no_page_context(self):
        self.portal.require_access.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.page.get_context(Context())
        self.sessions.get_csrf_token.assert_not_called()
        self.frappe.get_cached_value.assert_not_called()

    def test_authorized_page_is_private_uncached_and_has_session_csrf(self):
        context = self.page.get_context(Context())
        self.portal.require_access.assert_called_once_with()
        self.assertEqual(context.csrf_token, "session-csrf")
        self.assertEqual(context.user_display_name, "Workshop Manager")
        self.assertTrue(self.page.no_cache)
        self.assertTrue(context.no_cache)
        self.assertTrue(self.frappe.local.no_cache)
        self.assertEqual(self.page.sitemap, 0)


if __name__ == "__main__":
    unittest.main()
