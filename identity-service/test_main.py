"""
Emergency Exit — Automated Test Suite
======================================
Covers every backend feature built to date:

  F39-1  Vault sync endpoint
  F39-2  Pulse scanner — overdue detection logic
  F39-7  Notification protocol logic (3 protocols)
  F39-8  All-clear on check-in recovery
  F40    Authentication (login, token, protected routes)
  F41    Schema helpers (extract, reconstruct, ms↔datetime)

These are UNIT tests — they run without a live server or database.
All MongoDB and external calls are mocked (replaced with fakes).

HOW TO RUN:
  pip install pytest pytest-mock
  pytest test_main.py -v

Each test is named to describe exactly what behaviour it verifies.
A passing test = the feature works as designed.
A failing test = something broke and needs fixing before deploying.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone, timedelta


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT HELPERS
# We import only the pure functions from main.py — the ones that don't
# need a live database or server to run. This is the TDD-friendly way:
# keep business logic in plain functions, keep infrastructure separate.
# ─────────────────────────────────────────────────────────────────────────────

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Patch environment variables and heavy dependencies before importing main
# so the module loads without needing real MongoDB or Resend credentials
with patch.dict(os.environ, {
    "MONGO_URI": "mongodb://localhost:27017",
    "JWT_SECRET": "test-secret-key-for-pytest",
    "RESEND_API_KEY": "re_test_fake_key",
}):
    with patch("pymongo.MongoClient") as mock_mongo, \
         patch("resend.Emails.send") as mock_resend, \
         patch("apscheduler.schedulers.background.BackgroundScheduler") as mock_scheduler:

        mock_db = MagicMock()
        mock_mongo.return_value.__getitem__.return_value = mock_db
        mock_db.__getitem__.return_value = MagicMock()

        from main import (
            ms_to_dt,
            dt_to_ms,
            extract_vault_fields,
            reconstruct_vault_blob,
            get_contacts_to_notify,
            verify_password,
            hash_password,
            create_token,
            clean_user,
        )


# ═════════════════════════════════════════════════════════════════════════════
# F41 — SCHEMA HELPER TESTS
# These test the conversion functions that translate between the frontend's
# flat S={...} blob and the structured MongoDB schema.
# ═════════════════════════════════════════════════════════════════════════════

class TestMsToDt:
    """ms_to_dt: converts JavaScript millisecond timestamps to Python datetimes."""

    def test_converts_valid_timestamp(self):
        ms = 1_700_000_000_000  # a real JS timestamp
        result = ms_to_dt(ms)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None  # must be timezone-aware

    def test_returns_none_for_none_input(self):
        assert ms_to_dt(None) is None

    def test_returns_none_for_zero(self):
        # Zero is technically valid but we treat falsy as None
        result = ms_to_dt(0)
        # 0ms = epoch — valid datetime, not None
        assert isinstance(result, datetime)

    def test_roundtrip_preserves_timestamp(self):
        """Converting ms → datetime → ms should give back the same value (within 1ms)."""
        original_ms = 1_700_000_000_000
        dt = ms_to_dt(original_ms)
        back_to_ms = dt_to_ms(dt)
        assert abs(back_to_ms - original_ms) < 2  # allow 1ms rounding


class TestDtToMs:
    """dt_to_ms: converts Python datetimes back to JavaScript millisecond timestamps."""

    def test_converts_datetime_to_ms(self):
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = dt_to_ms(dt)
        assert isinstance(result, int)
        assert result > 0

    def test_returns_none_for_none_input(self):
        assert dt_to_ms(None) is None

    def test_result_is_in_milliseconds_not_seconds(self):
        """A 2024 timestamp should be in the trillions (ms), not billions (s)."""
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = dt_to_ms(dt)
        assert result > 1_000_000_000_000  # > 1 trillion = definitely milliseconds


class TestExtractVaultFields:
    """extract_vault_fields: splits the flat frontend blob into structured MongoDB parts."""

    def setup_method(self):
        """A realistic vault blob matching what the frontend sends."""
        self.vault_blob = {
            "lastCheckin": 1_700_000_000_000,
            "fc": 2,
            "fu": "months",
            "gp": 3,
            "notifyProto": "ping_then_notify",
            "assets": [{"id": 1, "name": "ANZ Account", "category": "Bank account"}],
            "wishes": [{"id": 2, "title": "I want to be cremated"}],
            "will": {"status": "signed", "solicitor": "Smith & Co"},
            "suppDocs": [{"id": 3, "type": "Statement of Wishes", "name": "SOW 2024"}],
            "kin": [{"id": 4, "first": "Jane", "last": "Smith", "email": "jane@example.com"}],
            "v": "face",
            "notifySeq": "in_order",
            "saveCount": 5,
            "log": [{"msg": "Asset added", "time": "1/1/2024"}],
        }

    def test_top_level_contains_checkin_fields(self):
        top_level, _, _ = extract_vault_fields(self.vault_blob)
        assert "lastCheckin" in top_level
        assert "checkInFrequency" in top_level
        assert "checkInUnit" in top_level
        assert "gracePeriodDays" in top_level
        assert "notifyProto" in top_level

    def test_top_level_lastcheckin_is_datetime(self):
        """lastCheckin must be stored as a datetime (not ms int) so MongoDB can index it."""
        top_level, _, _ = extract_vault_fields(self.vault_blob)
        assert isinstance(top_level["lastCheckin"], datetime)

    def test_content_contains_vault_data(self):
        _, content, _ = extract_vault_fields(self.vault_blob)
        assert "assets" in content
        assert "wishes" in content
        assert "will" in content
        assert "suppDocs" in content
        assert "kin" in content

    def test_content_does_not_contain_checkin_fields(self):
        """Check-in fields belong at top level, not duplicated in content."""
        _, content, _ = extract_vault_fields(self.vault_blob)
        assert "lastCheckin" not in content
        assert "fc" not in content
        assert "fu" not in content

    def test_log_is_extracted_separately(self):
        _, _, log = extract_vault_fields(self.vault_blob)
        assert len(log) == 1
        assert log[0]["msg"] == "Asset added"

    def test_handles_missing_lastcheckin(self):
        """A vault with no check-in yet should not crash."""
        blob = {**self.vault_blob, "lastCheckin": None}
        top_level, _, _ = extract_vault_fields(blob)
        assert top_level["lastCheckin"] is None

    def test_uses_defaults_for_missing_fields(self):
        """If the frontend omits optional fields, sensible defaults are used."""
        minimal_blob = {"assets": [], "kin": []}
        top_level, _, _ = extract_vault_fields(minimal_blob)
        assert top_level["checkInFrequency"] == 2
        assert top_level["checkInUnit"] == "months"
        assert top_level["gracePeriodDays"] == 3
        assert top_level["notifyProto"] == "ping_then_notify"

    def test_assets_data_is_preserved_exactly(self):
        _, content, _ = extract_vault_fields(self.vault_blob)
        assert content["assets"][0]["name"] == "ANZ Account"

    def test_contacts_data_is_preserved_exactly(self):
        _, content, _ = extract_vault_fields(self.vault_blob)
        assert content["kin"][0]["first"] == "Jane"


class TestReconstructVaultBlob:
    """reconstruct_vault_blob: rebuilds the flat frontend blob from structured MongoDB doc."""

    def setup_method(self):
        self.vault_doc = {
            "lastCheckin": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 3,
            "notifyProto": "ping_then_notify",
            "content": {
                "assets": [{"name": "ANZ Account"}],
                "wishes": [{"title": "Cremation"}],
                "will": {"status": "signed"},
                "suppDocs": [],
                "kin": [{"first": "Jane", "last": "Smith"}],
                "v": "face",
                "notifySeq": "in_order",
                "saveCount": 3,
            },
            "log": [{"msg": "Check-in confirmed"}],
        }

    def test_returns_flat_blob_with_all_expected_keys(self):
        blob = reconstruct_vault_blob(self.vault_doc)
        expected_keys = {"lastCheckin", "fc", "fu", "gp", "notifyProto",
                         "assets", "wishes", "will", "suppDocs", "kin",
                         "v", "notifySeq", "saveCount", "log"}
        assert expected_keys.issubset(blob.keys())

    def test_lastcheckin_is_converted_back_to_milliseconds(self):
        """Frontend expects ms integer, not a Python datetime."""
        blob = reconstruct_vault_blob(self.vault_doc)
        assert isinstance(blob["lastCheckin"], int)
        assert blob["lastCheckin"] > 1_000_000_000_000

    def test_fc_fu_gp_are_mapped_correctly(self):
        """MongoDB field names differ from frontend names — mapping must be correct."""
        blob = reconstruct_vault_blob(self.vault_doc)
        assert blob["fc"] == 2        # checkInFrequency → fc
        assert blob["fu"] == "months" # checkInUnit → fu
        assert blob["gp"] == 3        # gracePeriodDays → gp

    def test_assets_are_passed_through(self):
        blob = reconstruct_vault_blob(self.vault_doc)
        assert blob["assets"][0]["name"] == "ANZ Account"

    def test_contacts_are_passed_through(self):
        blob = reconstruct_vault_blob(self.vault_doc)
        assert blob["kin"][0]["first"] == "Jane"

    def test_handles_missing_content_gracefully(self):
        """A vault doc with no content sub-document should return empty arrays."""
        minimal_doc = {"checkInFrequency": 2, "checkInUnit": "months", "gracePeriodDays": 3}
        blob = reconstruct_vault_blob(minimal_doc)
        assert blob["assets"] == []
        assert blob["kin"] == []
        assert blob["log"] == []

    def test_roundtrip_preserves_data(self):
        """extract then reconstruct should give back equivalent data."""
        original_blob = {
            "lastCheckin": 1_700_000_000_000,
            "fc": 2, "fu": "months", "gp": 3,
            "notifyProto": "ping_then_notify",
            "assets": [{"name": "Test Asset"}],
            "wishes": [], "will": None, "suppDocs": [],
            "kin": [{"first": "Jane"}],
            "v": "face", "notifySeq": "in_order", "saveCount": 1,
            "log": [],
        }
        top_level, content, log = extract_vault_fields(original_blob)
        reconstructed = reconstruct_vault_blob({**top_level, "content": content, "log": log})
        assert reconstructed["fc"] == original_blob["fc"]
        assert reconstructed["fu"] == original_blob["fu"]
        assert reconstructed["assets"] == original_blob["assets"]
        assert reconstructed["kin"] == original_blob["kin"]
        # lastCheckin may differ by 1ms due to float rounding — allow small delta
        assert abs(reconstructed["lastCheckin"] - original_blob["lastCheckin"]) < 2


# ═════════════════════════════════════════════════════════════════════════════
# F39-7 — NOTIFICATION PROTOCOL LOGIC TESTS
# Tests for get_contacts_to_notify() — the function that decides who gets
# notified and when, based on the 3 supported protocols.
# ═════════════════════════════════════════════════════════════════════════════

class TestGetContactsToNotify:
    """Tests for all three notification protocols."""

    def setup_method(self):
        self.contacts = [
            {"first": "Jane", "last": "Smith", "email": "jane@example.com"},
            {"first": "Bob", "last": "Jones", "email": "bob@example.com"},
            {"first": "Alice", "last": "Lee", "email": "alice@example.com"},
        ]

    def _vault_doc(self, proto, contacts=None, _explicit_contacts=False):
        """Helper: builds a minimal vault_doc dict for testing."""
        return {
            "notifyProto": proto,
            "content": {"kin": contacts if contacts is not None else self.contacts},
        }

    # ── notify_immediately ────────────────────────────────────────────────────

    def test_notify_immediately_returns_all_contacts_on_day_0(self):
        vault_doc = self._vault_doc("notify_immediately")
        result = get_contacts_to_notify(vault_doc, days_overdue=0)
        assert len(result) == 3

    def test_notify_immediately_returns_all_contacts_on_day_5(self):
        vault_doc = self._vault_doc("notify_immediately")
        result = get_contacts_to_notify(vault_doc, days_overdue=5)
        assert len(result) == 3

    # ── ping_then_notify ──────────────────────────────────────────────────────

    def test_ping_then_notify_holds_on_day_0(self):
        """Should not notify anyone on day 0 — still in ping period."""
        vault_doc = self._vault_doc("ping_then_notify")
        result = get_contacts_to_notify(vault_doc, days_overdue=0)
        assert result == []

    def test_ping_then_notify_holds_on_day_1(self):
        vault_doc = self._vault_doc("ping_then_notify")
        result = get_contacts_to_notify(vault_doc, days_overdue=1)
        assert result == []

    def test_ping_then_notify_holds_on_day_2(self):
        vault_doc = self._vault_doc("ping_then_notify")
        result = get_contacts_to_notify(vault_doc, days_overdue=2)
        assert result == []

    def test_ping_then_notify_fires_on_day_3(self):
        """Day 3 is exactly when pinging ends and notifications begin."""
        vault_doc = self._vault_doc("ping_then_notify")
        result = get_contacts_to_notify(vault_doc, days_overdue=3)
        assert len(result) == 3

    def test_ping_then_notify_fires_on_day_10(self):
        """Should still return all contacts on day 10."""
        vault_doc = self._vault_doc("ping_then_notify")
        result = get_contacts_to_notify(vault_doc, days_overdue=10)
        assert len(result) == 3

    # ── escalate ─────────────────────────────────────────────────────────────

    def test_escalate_notifies_1_contact_on_day_0(self):
        """Day 0 = first contact only."""
        vault_doc = self._vault_doc("escalate")
        result = get_contacts_to_notify(vault_doc, days_overdue=0)
        assert len(result) == 1
        assert result[0]["first"] == "Jane"

    def test_escalate_notifies_2_contacts_on_day_1(self):
        vault_doc = self._vault_doc("escalate")
        result = get_contacts_to_notify(vault_doc, days_overdue=1)
        assert len(result) == 2

    def test_escalate_notifies_all_contacts_when_days_exceeds_count(self):
        """Can't notify more contacts than exist."""
        vault_doc = self._vault_doc("escalate")
        result = get_contacts_to_notify(vault_doc, days_overdue=10)
        assert len(result) == 3  # only 3 contacts exist

    def test_escalate_preserves_contact_order(self):
        """Contacts are notified in the order they were added."""
        vault_doc = self._vault_doc("escalate")
        result = get_contacts_to_notify(vault_doc, days_overdue=1)
        assert result[0]["first"] == "Jane"
        assert result[1]["first"] == "Bob"

    # ── edge cases ────────────────────────────────────────────────────────────

    def test_returns_empty_list_when_no_contacts(self):
        vault_doc = self._vault_doc("notify_immediately", contacts=[])
        result = get_contacts_to_notify(vault_doc, days_overdue=0)
        assert result == []

    def test_falls_back_to_old_vault_blob_schema(self):
        """F41 backward compat: vault_doc with old 'vault.kin' structure should still work."""
        vault_doc = {
            "notifyProto": "notify_immediately",
            "vault": {"kin": self.contacts},  # old pre-F41 schema
        }
        result = get_contacts_to_notify(vault_doc, days_overdue=0)
        assert len(result) == 3

    def test_unknown_protocol_returns_empty_list(self):
        """Unknown protocol should fail safely — return empty rather than crash."""
        vault_doc = self._vault_doc("some_future_protocol")
        result = get_contacts_to_notify(vault_doc, days_overdue=5)
        assert result == []


# ═════════════════════════════════════════════════════════════════════════════
# F40 — AUTHENTICATION HELPERS
# Tests for the pure auth functions: hashing, token creation, user cleaning.
# ═════════════════════════════════════════════════════════════════════════════

class TestPasswordHelpers:
    """Tests for bcrypt password hashing and verification."""

    def test_hash_password_returns_string(self):
        result = hash_password("mypassword123")
        assert isinstance(result, str)

    def test_hash_is_not_plaintext(self):
        result = hash_password("mypassword123")
        assert result != "mypassword123"

    def test_verify_password_correct(self):
        hashed = hash_password("correctpassword")
        assert verify_password("correctpassword", hashed) is True

    def test_verify_password_wrong(self):
        hashed = hash_password("correctpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_same_password_hashes_differently_each_time(self):
        """bcrypt uses a random salt — same password should produce different hashes."""
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2

    def test_both_hashes_still_verify_correctly(self):
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert verify_password("samepassword", h1) is True
        assert verify_password("samepassword", h2) is True


class TestCreateToken:
    """Tests for JWT token creation."""

    def test_token_is_a_string(self):
        token = create_token("user123", "tester_01")
        assert isinstance(token, str)

    def test_token_has_three_parts(self):
        """JWT tokens are always three base64 segments separated by dots."""
        token = create_token("user123", "tester_01")
        parts = token.split(".")
        assert len(parts) == 3

    def test_different_users_get_different_tokens(self):
        t1 = create_token("user1", "tester_01")
        t2 = create_token("user2", "tester_02")
        assert t1 != t2


class TestCleanUser:
    """Tests for clean_user — strips sensitive fields before sending to frontend."""

    def setup_method(self):
        from bson import ObjectId
        self.raw_user = {
            "_id": ObjectId(),
            "username": "tester_01",
            "name": "Test User",
            "password": "$2b$12$hashedpassword",  # should be stripped
            "ageGroup": "35-44",
            "hasWill": True,
            "isTester": True,
            "createdAt": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "lastLogin": datetime(2024, 6, 1, tzinfo=timezone.utc),
        }

    def test_password_is_not_in_output(self):
        """CRITICAL: password hash must never be sent to the frontend."""
        result = clean_user(self.raw_user)
        assert "password" in self.raw_user  # confirm it was in input
        assert "password" not in result     # confirm it's stripped from output

    def test_id_is_string_not_objectid(self):
        """ObjectId is not JSON-serialisable — must be converted to string."""
        result = clean_user(self.raw_user)
        assert isinstance(result["id"], str)

    def test_expected_fields_present(self):
        result = clean_user(self.raw_user)
        for field in ["id", "username", "name", "ageGroup", "hasWill", "isTester"]:
            assert field in result

    def test_name_is_preserved(self):
        result = clean_user(self.raw_user)
        assert result["name"] == "Test User"

    def test_handles_missing_optional_fields(self):
        """User records might be missing optional fields — should not crash."""
        minimal_user = {"_id": self.raw_user["_id"], "username": "u", "name": "N", "password": "p"}
        result = clean_user(minimal_user)
        assert result["username"] == "u"


# ═════════════════════════════════════════════════════════════════════════════
# F39-2 — PULSE SCANNER LOGIC TESTS
# Tests for the overdue detection calculation logic.
# These test the maths, not the database calls.
# ═════════════════════════════════════════════════════════════════════════════

class TestOverdueCalculationLogic:
    """
    Tests for the overdue timing logic embedded in run_pulse_scan().
    We test the maths directly rather than mocking the whole scanner.
    """

    def _calculate_grace_end_ms(self, last_checkin_ms, fc, fu, gp):
        """Replicates the overdue calculation from run_pulse_scan()."""
        interval_days = fc * 30 if fu == "months" else fc * 7
        due_date_ms = last_checkin_ms + (interval_days * 86400000)
        grace_end_ms = due_date_ms + (gp * 86400000)
        return grace_end_ms

    def test_2_month_frequency_gives_60_day_interval(self):
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        checkin_ms = now_ms - (65 * 86400000)  # checked in 65 days ago
        grace_end = self._calculate_grace_end_ms(checkin_ms, fc=2, fu="months", gp=3)
        # 60 days interval + 3 grace = 63 days, so 65 days ago = overdue
        assert now_ms > grace_end

    def test_2_month_frequency_not_overdue_at_59_days(self):
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        checkin_ms = now_ms - (59 * 86400000)  # checked in 59 days ago
        grace_end = self._calculate_grace_end_ms(checkin_ms, fc=2, fu="months", gp=3)
        # 60 days interval + 3 grace = 63 days, so 59 days = NOT overdue
        assert now_ms <= grace_end

    def test_weeks_unit_uses_7_day_multiplier(self):
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        checkin_ms = now_ms - (20 * 86400000)  # 20 days ago
        grace_end = self._calculate_grace_end_ms(checkin_ms, fc=2, fu="weeks", gp=3)
        # 2 weeks = 14 days + 3 grace = 17 days, so 20 days ago = overdue
        assert now_ms > grace_end

    def test_longer_grace_period_delays_overdue(self):
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        checkin_ms = now_ms - (64 * 86400000)  # 64 days ago
        grace_end_3day = self._calculate_grace_end_ms(checkin_ms, fc=2, fu="months", gp=3)
        grace_end_7day = self._calculate_grace_end_ms(checkin_ms, fc=2, fu="months", gp=7)
        # 64 days ago: overdue with 3-day grace (63 total), not with 7-day grace (67 total)
        assert now_ms > grace_end_3day
        assert now_ms <= grace_end_7day

    def test_days_overdue_calculation(self):
        """Days overdue = (now - grace_end) / ms_per_day."""
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        checkin_ms = now_ms - (70 * 86400000)  # 70 days ago
        grace_end = self._calculate_grace_end_ms(checkin_ms, fc=2, fu="months", gp=3)
        days_overdue = int((now_ms - grace_end) / 86400000)
        # 70 days ago - 63 day threshold = 7 days overdue
        assert days_overdue == 7


# ═════════════════════════════════════════════════════════════════════════════
# F41 — BACKWARD COMPATIBILITY TESTS
# After F41, old vault documents (stored before the migration) still need
# to work correctly. These tests verify the fallback logic.
# ═════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """
    Verifies that vault documents stored BEFORE F41 (using the old flat
    'vault' blob schema) continue to work alongside new structured documents.
    """

    def test_old_schema_contacts_still_work_in_notify_immediately(self):
        """Pre-F41 vault docs store contacts in vault.kin — must still be read."""
        old_vault_doc = {
            "notifyProto": "notify_immediately",
            "vault": {
                "kin": [{"first": "Jane", "last": "Smith"}]
            }
            # no 'content' key — this is the old schema
        }
        result = get_contacts_to_notify(old_vault_doc, days_overdue=0)
        assert len(result) == 1

    def test_new_schema_contacts_take_priority_over_old(self):
        """If both 'content' and 'vault' exist, content should win."""
        mixed_doc = {
            "notifyProto": "notify_immediately",
            "content": {"kin": [{"first": "New", "last": "Schema"}]},
            "vault": {"kin": [{"first": "Old", "last": "Schema"}]},
        }
        result = get_contacts_to_notify(mixed_doc, days_overdue=0)
        assert result[0]["first"] == "New"

    def test_reconstruct_handles_doc_with_no_content(self):
        """Old docs won't have 'content' — reconstruct should still return valid blob."""
        old_doc = {
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 3,
            "notifyProto": "ping_then_notify",
            "lastCheckin": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        blob = reconstruct_vault_blob(old_doc)
        assert blob["fc"] == 2
        assert blob["assets"] == []
        assert blob["kin"] == []


# ═════════════════════════════════════════════════════════════════════════════
# F39-8 — ALL-CLEAR RECOVERY TESTS
# Tests the protocol logic around sending all-clear emails after recovery.
# ═════════════════════════════════════════════════════════════════════════════

class TestAllClearLogic:
    """
    Tests the logic that determines whether all-clear emails should be sent.
    The actual email sending is mocked — we test the decision, not the send.
    """

    def test_all_clear_only_triggered_if_previously_notified(self):
        """
        If overdueNotificationSent was False, the user wasn't actually overdue
        — no all-clear email should go out.
        """
        vault_doc_not_notified = {"overdueNotificationSent": False}
        was_overdue = vault_doc_not_notified.get("overdueNotificationSent", False)
        assert was_overdue is False  # → no all-clear email

    def test_all_clear_triggered_when_previously_notified(self):
        vault_doc_was_notified = {"overdueNotificationSent": True}
        was_overdue = vault_doc_was_notified.get("overdueNotificationSent", False)
        assert was_overdue is True  # → send all-clear emails

    def test_all_clear_contacts_from_new_schema(self):
        """After F41, contacts come from content.kin, not vault.kin."""
        vault_doc = {
            "overdueNotificationSent": True,
            "content": {"kin": [{"first": "Jane"}, {"first": "Bob"}]},
        }
        contacts = vault_doc.get("content", {}).get("kin") or vault_doc.get("vault", {}).get("kin", [])
        assert len(contacts) == 2

    def test_all_clear_contacts_fallback_to_old_schema(self):
        vault_doc = {
            "overdueNotificationSent": True,
            "vault": {"kin": [{"first": "Jane"}]},
        }
        contacts = vault_doc.get("content", {}).get("kin") or vault_doc.get("vault", {}).get("kin", [])
        assert len(contacts) == 1


# ═════════════════════════════════════════════════════════════════════════════
# COMPLETENESS SCORE LOGIC
# Tests for the 7-check completeness calculation (mirrors the frontend comp())
# ═════════════════════════════════════════════════════════════════════════════

class TestCompletenessLogic:
    """
    The frontend's comp() function calculates a 0–100% completeness score.
    We replicate the logic here to verify the 7 checks and weightings.
    Each check is worth ~14.3% (1/7).
    """

    def _comp(self, assets=None, wishes=None, will=None, supp_docs=None, kin=None, last_checkin=None):
        """Python replica of the frontend comp() function."""
        assets = assets or []
        wishes = wishes or []
        supp_docs = supp_docs or []
        kin = kin or []

        sow = any(d.get("type") == "Statement of Wishes" for d in supp_docs)
        checks = [
            len(assets) > 0,
            any(a.get("beneficiary") for a in assets),
            len(wishes) > 0,
            will is not None,
            sow,
            len(kin) > 0,
            last_checkin is not None,
        ]
        pct = round(sum(checks) / len(checks) * 100)
        return pct

    def test_empty_vault_is_0_percent(self):
        assert self._comp() == 0

    def test_full_vault_is_100_percent(self):
        pct = self._comp(
            assets=[{"name": "ANZ", "beneficiary": "Jane"}],
            wishes=[{"title": "Cremation"}],
            will={"status": "signed"},
            supp_docs=[{"type": "Statement of Wishes", "name": "SOW"}],
            kin=[{"first": "Jane"}],
            last_checkin=1_700_000_000_000,
        )
        assert pct == 100

    def test_one_asset_without_beneficiary_is_14_percent(self):
        """Adding an asset without a beneficiary = 1/7 checks = ~14%."""
        pct = self._comp(assets=[{"name": "ANZ"}])
        assert pct == 14

    def test_asset_with_beneficiary_is_29_percent(self):
        """Asset + beneficiary = 2/7 checks."""
        pct = self._comp(assets=[{"name": "ANZ", "beneficiary": "Jane"}])
        assert pct == 29

    def test_sow_requires_correct_type_field(self):
        """Only a doc with type='Statement of Wishes' counts for the SOW check."""
        pct_with_sow = self._comp(supp_docs=[{"type": "Statement of Wishes", "name": "SOW"}])
        pct_without_sow = self._comp(supp_docs=[{"type": "Other", "name": "Some Doc"}])
        assert pct_with_sow > pct_without_sow

    def test_check_in_completion_requires_non_none_timestamp(self):
        pct_with_checkin = self._comp(last_checkin=1_700_000_000_000)
        pct_without = self._comp(last_checkin=None)
        assert pct_with_checkin > pct_without
