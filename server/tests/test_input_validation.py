"""Every input surface must answer with 4xx, never 5xx.

A validation helper that raises ``ValidationError`` outside the service-level
wrapper used to fall through to the generic 500 handler: a 61-character search
string returned "Something went wrong on Velora's side" instead of "q must be 60
characters or fewer". The app boundary now translates any escaping
``ValidationError`` into a 400, so these tests pin the whole class down.

Each listed path/field was confirmed to return 500 before the fix.
"""

from __future__ import annotations

import unittest

from ..validation import MAX_NOTE, ValidationError, clean_handle
from .harness import Client, VeloraTestCase

LONG = "a" * 200


class SearchQueryLimitsTests(VeloraTestCase):
    """Over-long search strings on public and admin list endpoints."""

    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="q-creator@velora.test",
                                     handle="q-page", page_name="Query Page")
        self.create_post(self.creator, publish=True)
        self.member = self.register("q-member@velora.test", display_name="Query Member")
        self.admin = self.register("q-admin@velora.test", display_name="Query Admin")
        self.make_admin(self.admin, "q-admin@velora.test")
        self.admin.login("q-admin@velora.test", "correct-horse-9")

    def assert_bad_request(self, response, path: str):
        self.assertEqual(response.status, 400, f"{path} -> {response.status} {response.text}")
        error = response.json["error"]
        self.assertEqual(error["code"], "validation_error", f"{path} -> {error}")
        self.assertTrue(error["message"], path)
        self.assertTrue(error.get("field"), path)

    def test_public_discovery_rejects_an_over_long_query(self):
        for query in (LONG, "a" * 61):
            with self.subTest(length=len(query)):
                response = self.member.get(f"/api/creators?q={query}")
                self.assert_bad_request(response, "/api/creators?q=")

    def test_public_discovery_accepts_a_query_at_the_limit(self):
        response = self.member.get("/api/creators?q=" + "a" * 60)
        self.assertEqual(response.status, 200, response.text)

    def test_admin_search_rejects_over_long_queries(self):
        for path in ("/api/admin/users", "/api/admin/creators", "/api/admin/payments",
                     "/api/admin/posts"):
            with self.subTest(path=path):
                response = self.admin.get(f"{path}?q={LONG}")
                self.assert_bad_request(response, f"{path}?q=")

    def test_admin_search_accepts_queries_at_each_limit(self):
        for path, limit in (("/api/admin/users", 60), ("/api/admin/creators", 60),
                            ("/api/admin/payments", 60), ("/api/admin/posts", 60)):
            with self.subTest(path=path):
                response = self.admin.get(f"{path}?q=" + "a" * limit)
                self.assertEqual(response.status, 200, response.text)

    def test_over_long_query_does_not_leak_internal_details(self):
        response = self.member.get(f"/api/creators?q={LONG}")
        text = response.text
        self.assertNotIn("Traceback", text)
        self.assertNotIn("ValidationError", text)
        self.assertNotIn("server/", text)


class SignupInputTests(VeloraTestCase):
    """Attestation and identity fields must never crash the signup handler."""

    def signup(self, **overrides) -> "object":
        payload = {
            "display_name": "Zed North",
            "email": "zed@velora.test",
            "password": "correct-horse-9",
            "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        }
        payload.update(overrides)
        return Client(self.app).post("/api/auth/signup", json_body=payload)

    def test_non_boolean_attestation_is_a_bad_request(self):
        for value in ([], {}, 3.5):
            with self.subTest(value=repr(value)):
                response = self.signup(adult_attestation=value)
                self.assertEqual(response.status, 400, response.text)
                error = response.json["error"]
                self.assertEqual(error["field"], "adult_attestation")
                self.assertIn(error["code"], ("validation_error", "invalid_request"))

    def test_attestation_is_still_required(self):
        for value in (False, None, "false", 0):
            with self.subTest(value=repr(value)):
                response = self.signup(adult_attestation=value)
                self.assertEqual(response.status, 400, response.text)
                self.assertEqual(response.json["error"]["code"], "adult_attestation_required")

    def test_a_truthy_string_attestation_is_accepted(self):
        response = self.signup(adult_attestation="yes")
        self.assertEqual(response.status, 201, response.text)
        self.assertTrue(response.json["created"])

    def test_missing_attestation_key_is_a_bad_request(self):
        payload = {
            "display_name": "Zed North", "email": "zed@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
        }
        response = Client(self.app).post("/api/auth/signup", json_body=payload)
        self.assertEqual(response.status, 400, response.text)
        self.assertEqual(response.json["error"]["code"], "adult_attestation_required")

    def test_over_long_display_name_is_a_bad_request(self):
        response = self.signup(display_name="Z" * 400)
        self.assertEqual(response.status, 400, response.text)
        self.assertTrue(response.json["error"]["field"])

    def test_over_long_password_is_a_bad_request(self):
        response = self.signup(password="a" * 5000, password_confirm="a" * 5000)
        self.assertEqual(response.status, 400, response.text)

    def test_no_500_for_hostile_signup_payloads(self):
        cases = [
            {"display_name": None}, {"email": None}, {"password": None},
            {"password_confirm": "something-else"}, {"display_name": ["a"]},
            {"email": {"a": 1}}, {"password": ["a" * 10]}, {"password": "\x00\x01"},
        ]
        for case in cases:
            with self.subTest(case=case):
                response = self.signup(**case)
                self.assertLess(response.status, 500, f"{case} -> {response.status} {response.text}")


class OrientationPayloadTests(VeloraTestCase):
    """The optional orientation field takes several shapes; none may 500."""

    def setUp(self):
        super().setUp()
        self.member = self.register("orientation@velora.test", display_name="Orientation Person")

    def test_hostile_orientation_payloads_are_rejected_cleanly(self):
        cases = [123, 1.5, [], ["a"], {"value": ["a"]}, {"self_described": {"a": 1}},
                 {"value": "nonsense-preset"}, {"value": "straight", "self_described": "x" * 300},
                 {"value": None}, {"self_described": "x" * 121}]
        for case in cases:
            with self.subTest(case=repr(case)[:60]):
                response = self.member.post("/api/account/orientation", json_body={"orientation": case})
                self.assertLess(response.status, 500, f"{case} -> {response.status} {response.text}")


class ReasonFieldTests(VeloraTestCase):
    """Administrative reasons are length-checked, not crashed on."""

    def setUp(self):
        super().setUp()
        self.member = self.register("reason-member@velora.test", display_name="Reason Member")
        self.admin = self.register("reason-admin@velora.test", display_name="Reason Admin")
        self.make_admin(self.admin, "reason-admin@velora.test")
        self.admin.login("reason-admin@velora.test", "correct-horse-9")
        with self.db.connection() as conn:
            self.member_id = conn.execute(
                "SELECT id FROM users WHERE email = ?", ("reason-member@velora.test",)).fetchone()["id"]

    def test_over_long_reason_is_a_bad_request(self):
        response = self.admin.post("/api/admin/notes", json_body={
            "target_type": "user", "target_id": self.member_id, "body": "n" * (MAX_NOTE + 1)})
        self.assertEqual(response.status, 400, response.text)
        self.assertEqual(response.json["error"]["code"], "validation_error")
        self.assertIn("fewer", response.json["error"]["message"])

    def test_an_empty_note_is_a_bad_request(self):
        response = self.admin.post("/api/admin/notes", json_body={
            "target_type": "user", "target_id": self.member_id, "body": "   "})
        self.assertEqual(response.status, 400, response.text)
        self.assertEqual(response.json["error"]["code"], "validation_error")

    def test_normal_reason_is_stored(self):
        response = self.admin.post("/api/admin/notes", json_body={
            "target_type": "user", "target_id": self.member_id,
            "body": "Reviewed the report with the member."})
        self.assertEqual(response.status, 201, response.text)


class HandleRulesTests(unittest.TestCase):
    """The advertised rule is 3-30 characters, lowercase letters/numbers/-/_, no edge punctuation."""

    def test_accepted_handles(self):
        for handle in ("abc", "c-five", "a1b", "my_page", "Sample-Creator", "x" * 30, "7-days"):
            self.assertEqual(clean_handle(handle), handle.lower(), handle)

    def test_rejected_handles(self):
        for handle in ("ab", "x" * 31, "-abc", "abc-", "_abc", "a b c", "a--b", "a__b", "a.b", "admin", "", "é-é-é"):
            with self.assertRaises(ValidationError, msg=repr(handle)):
                clean_handle(handle)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
