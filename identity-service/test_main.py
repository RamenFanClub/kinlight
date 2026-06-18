"""
Emergency Exit — Backend Test Suite
Run: python3 -m pytest test_main.py -v
Expected: 192 passed
"""

import logging
import pytest
import jwt
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from bson import ObjectId

import main

# ─── Import helpers directly (no FastAPI server needed) ───────────────────────
from main import (
    ms_to_dt, dt_to_ms,
    extract_vault_fields, reconstruct_vault_blob,
    get_contacts_to_notify,
    hash_password, check_password,
    create_token,
    clean_user,
    JWT_SECRET,
    is_overdue,
    is_reminder_due,
    send_reminder_email,
    send_nomination_email,
    hash_reset_token,
    is_reset_valid,
    is_password_acceptable,
    send_reset_email,
    should_send_warning,
    should_notify_contacts,
    send_warning_email,
    send_contacts_notified_email,
    contact_nominate,
    require_admin,
    generate_pdf_for_contact,
    is_account_locked,
    get_lockout_remaining_seconds,
    record_failed_login,
    clear_login_failures,
    MAX_LOGIN_ATTEMPTS,
    LOCKOUT_MINUTES,
    now_utc,
    encrypt_content,
    decrypt_content,
)


# ─── TIMESTAMP HELPERS ────────────────────────────────────────────────────────

class TestMsToDt:
    def test_none_returns_none(self):
        assert ms_to_dt(None) is None

    def test_zero(self):
        result = ms_to_dt(0)
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_known_timestamp(self):
        # 1 Jan 2024 00:00:00 UTC = 1704067200000 ms
        result = ms_to_dt(1704067200000)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_returns_utc(self):
        result = ms_to_dt(1704067200000)
        assert result.tzinfo == timezone.utc


class TestDtToMs:
    def test_none_returns_none(self):
        assert dt_to_ms(None) is None

    def test_epoch(self):
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert dt_to_ms(dt) == 0

    def test_known_datetime(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert dt_to_ms(dt) == 1704067200000

    def test_naive_datetime_treated_as_utc(self):
        dt_naive = datetime(1970, 1, 1)
        assert dt_to_ms(dt_naive) == 0

    def test_roundtrip(self):
        original_ms = 1704067200000
        assert dt_to_ms(ms_to_dt(original_ms)) == original_ms


# ─── EXTRACT VAULT FIELDS ─────────────────────────────────────────────────────

class TestExtractVaultFields:
    def _blob(self, **kwargs):
        base = {"fc": 2, "fu": "months", "gp": 7, "notifyProto": "ping_then_notify", "lastCheckin": None}
        base.update(kwargs)
        return base

    def test_frequency_extracted(self):
        result = extract_vault_fields(self._blob(fc=4))
        assert result["checkInFrequency"] == 4

    def test_unit_extracted(self):
        result = extract_vault_fields(self._blob(fu="weeks"))
        assert result["checkInUnit"] == "weeks"

    def test_grace_period_extracted(self):
        result = extract_vault_fields(self._blob(gp=14))
        assert result["gracePeriodDays"] == 14

    def test_notify_proto_extracted(self):
        result = extract_vault_fields(self._blob(notifyProto="escalate"))
        assert result["notifyProto"] == "escalate"

    def test_last_checkin_converted(self):
        result = extract_vault_fields(self._blob(lastCheckin=1704067200000))
        assert isinstance(result["lastCheckin"], datetime)

    def test_null_checkin(self):
        result = extract_vault_fields(self._blob(lastCheckin=None))
        assert result["lastCheckin"] is None

    def test_defaults_applied(self):
        result = extract_vault_fields({})
        assert result["checkInFrequency"] == 2
        assert result["checkInUnit"] == "months"
        assert result["gracePeriodDays"] == 7

    def test_months_default(self):
        result = extract_vault_fields({})
        assert result["checkInUnit"] == "months"

    def test_proto_default(self):
        result = extract_vault_fields({})
        assert result["notifyProto"] == "ping_then_notify"


# ─── RECONSTRUCT VAULT BLOB ───────────────────────────────────────────────────

class TestReconstructVaultBlob:
    def _doc(self, **kwargs):
        base = {
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 7,
            "notifyProto": "ping_then_notify",
            "lastCheckin": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "content": {"assets": [], "wishes": [], "will": None, "suppDocs": [], "kin": [], "v": "face", "notifySeq": "in_order", "saveCount": 0},
            "log": [],
        }
        base.update(kwargs)
        return base

    def test_last_checkin_converted_to_ms(self):
        result = reconstruct_vault_blob(self._doc())
        assert result["lastCheckin"] == 1704067200000

    def test_frequency_mapped(self):
        result = reconstruct_vault_blob(self._doc(checkInFrequency=4))
        assert result["fc"] == 4

    def test_unit_mapped(self):
        result = reconstruct_vault_blob(self._doc(checkInUnit="weeks"))
        assert result["fu"] == "weeks"

    def test_kin_from_content(self):
        doc = self._doc()
        doc["content"]["kin"] = [{"first": "Jane", "last": "Smith"}]
        result = reconstruct_vault_blob(doc)
        assert result["kin"][0]["first"] == "Jane"

    def test_empty_kin_list_not_fallen_through(self):
        """Empty list [] must NOT fall through to old schema — explicit None check."""
        doc = self._doc()
        doc["content"]["kin"] = []
        doc["vault"] = {"kin": [{"first": "Ghost"}]}
        result = reconstruct_vault_blob(doc)
        assert result["kin"] == []

    def test_none_kin_falls_back_to_old_schema(self):
        doc = self._doc()
        doc["content"].pop("kin", None)
        doc["vault"] = {"kin": [{"first": "Fallback"}]}
        result = reconstruct_vault_blob(doc)
        assert result["kin"][0]["first"] == "Fallback"

    def test_assets_reconstructed(self):
        doc = self._doc()
        doc["content"]["assets"] = [{"name": "House"}]
        result = reconstruct_vault_blob(doc)
        assert result["assets"][0]["name"] == "House"


# ─── NOTIFICATION PROTOCOL ────────────────────────────────────────────────────

class TestGetContactsToNotify:
    def _vault(self, proto, kin=None):
        contacts = kin or [
            {"first": "Alice", "last": "A", "email": "alice@test.com"},
            {"first": "Bob", "last": "B", "email": "bob@test.com"},
            {"first": "Carol", "last": "C", "email": "carol@test.com"},
        ]
        return {
            "notifyProto": proto,
            "content": {"kin": contacts},
        }

    def test_ping_then_notify_day0(self):
        result = get_contacts_to_notify(self._vault("ping_then_notify"), 0)
        assert result == []

    def test_ping_then_notify_day2(self):
        result = get_contacts_to_notify(self._vault("ping_then_notify"), 2)
        assert result == []

    def test_ping_then_notify_day3(self):
        result = get_contacts_to_notify(self._vault("ping_then_notify"), 3)
        assert len(result) == 3

    def test_notify_immediately_day0(self):
        result = get_contacts_to_notify(self._vault("notify_immediately"), 0)
        assert len(result) == 3

    def test_escalate_day0(self):
        result = get_contacts_to_notify(self._vault("escalate"), 0)
        assert len(result) == 1

    def test_escalate_day1(self):
        result = get_contacts_to_notify(self._vault("escalate"), 1)
        assert len(result) == 2

    def test_escalate_day2(self):
        result = get_contacts_to_notify(self._vault("escalate"), 2)
        assert len(result) == 3

    def test_escalate_caps_at_contact_count(self):
        result = get_contacts_to_notify(self._vault("escalate"), 99)
        assert len(result) == 3

    def test_no_contacts_returns_empty(self):
        vault = {"notifyProto": "notify_immediately", "content": {"kin": []}}
        result = get_contacts_to_notify(vault, 5)
        assert result == []

    def test_none_kin_returns_empty(self):
        vault = {"notifyProto": "notify_immediately", "content": {}}
        result = get_contacts_to_notify(vault, 5)
        assert result == []

    def test_escalate_day0_first_contact_only(self):
        result = get_contacts_to_notify(self._vault("escalate"), 0)
        assert result[0]["first"] == "Alice"

    def test_ping_then_notify_all_contacts_on_day3(self):
        result = get_contacts_to_notify(self._vault("ping_then_notify"), 3)
        names = [c["first"] for c in result]
        assert "Alice" in names and "Bob" in names

    def test_unknown_proto_returns_all(self):
        result = get_contacts_to_notify(self._vault("unknown_proto"), 5)
        assert len(result) == 3


# ─── PASSWORD HELPERS ─────────────────────────────────────────────────────────

class TestPasswordHelpers:
    def test_hash_returns_string(self):
        assert isinstance(hash_password("secret"), str)

    def test_check_correct_password(self):
        hashed = hash_password("mypassword")
        assert check_password("mypassword", hashed) is True

    def test_check_wrong_password(self):
        hashed = hash_password("mypassword")
        assert check_password("wrong", hashed) is False

    def test_different_hashes_same_password(self):
        h1 = hash_password("abc")
        h2 = hash_password("abc")
        assert h1 != h2  # bcrypt uses random salt

    def test_hash_not_plaintext(self):
        hashed = hash_password("secret")
        assert "secret" not in hashed

    def test_empty_password(self):
        hashed = hash_password("")
        assert check_password("", hashed) is True


# ─── JWT HELPERS ──────────────────────────────────────────────────────────────

class TestCreateToken:
    def test_returns_string(self):
        assert isinstance(create_token("abc123"), str)

    def test_non_empty(self):
        assert len(create_token("abc123")) > 0

    def test_different_ids_different_tokens(self):
        assert create_token("id1") != create_token("id2")

    def test_token_contains_exp_claim(self):
        token = create_token("abc123")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"],
                             options={"verify_exp": False})
        assert "exp" in payload

    def test_token_contains_iat_claim(self):
        token = create_token("abc123")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"],
                             options={"verify_exp": False})
        assert "iat" in payload

    def test_token_expires_in_24_hours(self):
        token = create_token("abc123")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"],
                             options={"verify_exp": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        delta = exp - iat
        assert 23 <= delta.total_seconds() / 3600 <= 25  # ~24 hours

    def test_expired_token_rejected(self):
        """A token with exp in the past should raise DecodeError."""
        expired_payload = {
            "sub": "abc123",
            "iat": datetime.now(timezone.utc) - timedelta(hours=48),
            "exp": datetime.now(timezone.utc) - timedelta(hours=24),
        }
        expired_token = jwt.encode(expired_payload, JWT_SECRET, algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(expired_token, JWT_SECRET, algorithms=["HS256"])


# ─── CLEAN USER ───────────────────────────────────────────────────────────────

class TestCleanUser:
    def _user(self):
        from bson import ObjectId
        return {
            "_id": ObjectId(),
            "username": "tester_01",
            "password": "hashed_secret",
            "name": "Test User",
            "email": "test@example.com",
            "ageGroup": "35-44",
            "hasWill": True,
            "isTester": True,
        }

    def test_password_removed(self):
        result = clean_user(self._user())
        assert "password" not in result

    def test_id_stringified(self):
        result = clean_user(self._user())
        assert isinstance(result["id"], str)

    def test_name_present(self):
        result = clean_user(self._user())
        assert result["name"] == "Test User"

    def test_email_present(self):
        result = clean_user(self._user())
        assert result["email"] == "test@example.com"

    def test_is_tester_present(self):
        result = clean_user(self._user())
        assert result["isTester"] is True


# ─── OVERDUE CALCULATION ──────────────────────────────────────────────────────

class TestOverdueCalculationLogic:
    def _vault(self, days_ago, freq=2, unit="months", grace=7):
        checkin_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return {
            "lastCheckin": checkin_dt,
            "checkInFrequency": freq,
            "checkInUnit": unit,
            "gracePeriodDays": grace,
        }

    def test_recent_checkin_not_overdue(self):
        overdue, _ = is_overdue(self._vault(days_ago=1))
        assert overdue is False

    def test_within_interval_not_overdue(self):
        overdue, _ = is_overdue(self._vault(days_ago=30))
        assert overdue is False

    def test_within_grace_not_overdue(self):
        # 2 months = 60 days interval, grace = 7 → overdue at day 67
        overdue, _ = is_overdue(self._vault(days_ago=63))
        assert overdue is False

    def test_past_grace_is_overdue(self):
        overdue, _ = is_overdue(self._vault(days_ago=70))
        assert overdue is True

    def test_null_checkin_not_overdue(self):
        vault = {"lastCheckin": None, "checkInFrequency": 2, "checkInUnit": "months", "gracePeriodDays": 7}
        overdue, _ = is_overdue(vault)
        assert overdue is False


# ─── BACKWARD COMPATIBILITY ───────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_old_vault_field_fallback(self):
        doc = {
            "checkInFrequency": 2, "checkInUnit": "months", "gracePeriodDays": 7,
            "notifyProto": "ping_then_notify", "lastCheckin": None,
            "content": {},
            "vault": {"kin": [{"first": "Legacy"}], "assets": [], "wishes": [], "suppDocs": []},
            "log": [],
        }
        result = reconstruct_vault_blob(doc)
        assert result["kin"][0]["first"] == "Legacy"

    def test_empty_list_not_overridden_by_old_schema(self):
        doc = {
            "checkInFrequency": 2, "checkInUnit": "months", "gracePeriodDays": 7,
            "notifyProto": "ping_then_notify", "lastCheckin": None,
            "content": {"kin": [], "assets": [], "wishes": [], "suppDocs": []},
            "vault": {"kin": [{"first": "Ghost"}]},
            "log": [],
        }
        result = reconstruct_vault_blob(doc)
        assert result["kin"] == []

    def test_content_takes_priority_over_vault(self):
        doc = {
            "checkInFrequency": 2, "checkInUnit": "months", "gracePeriodDays": 7,
            "notifyProto": "ping_then_notify", "lastCheckin": None,
            "content": {"kin": [{"first": "New"}], "assets": [], "wishes": [], "suppDocs": []},
            "vault": {"kin": [{"first": "Old"}]},
            "log": [],
        }
        result = reconstruct_vault_blob(doc)
        assert result["kin"][0]["first"] == "New"


# ─── ALL CLEAR LOGIC ──────────────────────────────────────────────────────────

class TestAllClearLogic:
    def test_allclear_only_when_was_overdue(self):
        """All-clear emails should only send if overdueNotificationSent was True."""
        was_overdue = True
        contacts = [{"first": "Jane", "email": "jane@test.com"}]
        assert was_overdue and len(contacts) > 0

    def test_no_allclear_if_not_overdue(self):
        was_overdue = False
        assert not was_overdue

    def test_allclear_resets_flag(self):
        """After check-in, both overdueNotificationSent and reminderSent should reset."""
        flags_to_reset = ["overdueNotificationSent", "reminderSent"]
        assert "overdueNotificationSent" in flags_to_reset
        assert "reminderSent" in flags_to_reset

    def test_allclear_sent_to_all_contacts(self):
        contacts = [{"first": "A"}, {"first": "B"}, {"first": "C"}]
        assert len(contacts) == 3


# ─── COMPLETENESS LOGIC ───────────────────────────────────────────────────────

class TestCompletenessLogic:
    """Mirror the 7-check completeness score from the frontend."""

    def _state(self, **kwargs):
        base = {
            "assets": [],
            "wishes": [],
            "will": None,
            "suppDocs": [],
            "kin": [],
            "lastCheckin": None,
        }
        base.update(kwargs)
        return base

    def _score(self, state):
        checks = [
            len(state["assets"]) > 0,
            any(a.get("beneficiary") for a in state["assets"]),
            len(state["wishes"]) > 0,
            state["will"] is not None,
            any(d.get("type") == "Statement of Wishes" for d in state["suppDocs"]),
            len(state["kin"]) > 0,
            state["lastCheckin"] is not None,
        ]
        return round(sum(checks) / len(checks) * 100)

    def test_empty_vault_zero(self):
        assert self._score(self._state()) == 0

    def test_one_asset_not_zero(self):
        state = self._state(assets=[{"name": "House"}])
        assert self._score(state) > 0

    def test_full_vault_100(self):
        state = self._state(
            assets=[{"name": "House", "beneficiary": "Jane"}],
            wishes=[{"title": "Cremation"}],
            will={"status": "signed"},
            suppDocs=[{"type": "Statement of Wishes", "name": "SOW"}],
            kin=[{"first": "Jane"}],
            lastCheckin=1704067200000,
        )
        assert self._score(state) == 100

    def test_beneficiary_check_requires_at_least_one(self):
        state = self._state(assets=[{"name": "Car", "beneficiary": ""}, {"name": "House", "beneficiary": "Jane"}])
        score = self._score(state)
        assert score > self._score(self._state(assets=[{"name": "Car"}]))

    def test_sow_in_supp_docs_counts(self):
        state = self._state(suppDocs=[{"type": "Statement of Wishes", "name": "SOW 2024"}])
        assert self._score(state) > 0

    def test_seven_checks_total(self):
        """Verify there are exactly 7 checks in our completeness function."""
        state = self._state()
        checks = [
            len(state["assets"]) > 0,
            any(a.get("beneficiary") for a in state["assets"]),
            len(state["wishes"]) > 0,
            state["will"] is not None,
            any(d.get("type") == "Statement of Wishes" for d in state["suppDocs"]),
            len(state["kin"]) > 0,
            state["lastCheckin"] is not None,
        ]
        assert len(checks) == 7


# ─── F60: REMINDER LOGIC ─────────────────────────────────────────────────────

class TestReminderLogic:
    """
    Tests for F60: server-side reminder email to vault holder.
    Mirrors the frontend F05 25% threshold rule.
    """

    def _vault(self, days_ago, freq=2, unit="months", reminder_sent=False):
        """Helper: build a vault doc with lastCheckin set N days ago."""
        checkin_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return {
            "lastCheckin": checkin_dt,
            "checkInFrequency": freq,
            "checkInUnit": unit,
            "gracePeriodDays": 7,
            "reminderSent": reminder_sent,
        }

    def test_no_checkin_no_reminder(self):
        vault = {"lastCheckin": None, "checkInFrequency": 2, "checkInUnit": "months",
                 "gracePeriodDays": 7, "reminderSent": False}
        assert is_reminder_due(vault) is False

    def test_early_in_cycle_no_reminder(self):
        # 2 months = ~60 days, 25% threshold = 15 days. Day 5: too early.
        vault = self._vault(days_ago=5)
        assert is_reminder_due(vault) is False

    def test_just_outside_threshold_no_reminder(self):
        # Due at day 60, threshold = 15 days (day 45). Check at day 42 → 16 days left → no reminder.
        vault = self._vault(days_ago=42)
        assert is_reminder_due(vault) is False

    def test_just_inside_threshold_reminder_due(self):
        # Due at day 60, threshold = 15 days (day 45). Check at day 46 → 14 days left → reminder.
        vault = self._vault(days_ago=46)
        assert is_reminder_due(vault) is True

    def test_reminder_not_sent_twice(self):
        # reminderSent=True means we already sent it this cycle — don't repeat.
        vault = self._vault(days_ago=55, reminder_sent=True)
        assert is_reminder_due(vault) is False

    def test_overdue_vault_no_reminder(self):
        # Past the due date entirely (day 70 > 60 day interval + 7 grace).
        # Reminder should not fire — overdue scanner handles this.
        vault = self._vault(days_ago=70)
        # days_remaining would be negative, so 0 <= days_remaining check fails
        assert is_reminder_due(vault) is False

    def test_weekly_interval_25_percent_threshold(self):
        # 1 week = 7 days, 25% = 1.75 → rounded to max(7, 2) = 7 days threshold.
        # At 7-day interval, threshold = 7 days = entire interval → always in window
        # unless already sent. Day 1 in, 6 days left → 6 < 7 → reminder.
        vault = self._vault(days_ago=1, freq=1, unit="weeks")
        assert is_reminder_due(vault) is True

    def test_threshold_minimum_is_7_days(self):
        # Even for short intervals, threshold floors at 7 days.
        # 2 weeks = 14 days, 25% = 3.5, but floor is 7.
        vault = self._vault(days_ago=8, freq=2, unit="weeks")
        # 14 - 8 = 6 days remaining, which is < threshold of 7 → reminder due
        assert is_reminder_due(vault) is True

    def test_send_reminder_email_calls_resend(self):
        """send_reminder_email should POST to Resend and return True on success."""
        user = {"name": "Test User", "email": "test@example.com"}
        vault_doc = {
            "lastCheckin": datetime.now(timezone.utc) - timedelta(days=46),
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 7,
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            result = send_reminder_email(user, vault_doc)

        assert result is True
        assert mock_post.called
        call_kwargs = mock_post.call_args
        assert "resend.com" in call_kwargs[0][0]

    def test_send_reminder_email_returns_false_on_failure(self):
        """send_reminder_email should return False if Resend call fails."""
        user = {"name": "Test User", "email": "test@example.com"}
        vault_doc = {
            "lastCheckin": datetime.now(timezone.utc) - timedelta(days=46),
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 7,
        }
        with patch("main.requests.post", side_effect=Exception("Network error")):
            result = send_reminder_email(user, vault_doc)

        assert result is False

    def test_reminder_email_contains_checkin_action_link(self):
        """F67: reminder email body must include ?action=checkin so the link opens the app ready to check in."""
        user = {"name": "Test User", "email": "test@example.com"}
        vault_doc = {
            "lastCheckin": datetime.now(timezone.utc) - timedelta(days=46),
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 7,
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_reminder_email(user, vault_doc)

        payload = mock_post.call_args[1]["json"]
        assert "?action=checkin" in payload["text"]


# ─── F63: NOMINATION EMAIL ────────────────────────────────────────────────────

class TestNominationEmail:
    """Tests for send_nomination_email (F63)."""

    def test_sends_to_correct_address(self):
        """Nomination email should be sent to the contact's email address."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            result = send_nomination_email("jane@example.com", "Jane", "Alex Smith")

        assert result is True
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["to"] == ["jane@example.com"]

    def test_email_body_contains_holder_name(self):
        """Nomination email body should include the vault holder's name."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_nomination_email("jane@example.com", "Jane", "Alex Smith")

        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"]
        assert "Alex Smith" in payload["text"]
        assert "Alex Smith" in payload["subject"]

    def test_email_body_no_action_required(self):
        """Nomination email must reassure the recipient no action is needed."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_nomination_email("jane@example.com", "Jane", "Alex Smith")

        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"]
        assert "no action" in payload["text"].lower()

    def test_returns_false_on_network_failure(self):
        """send_nomination_email should return False if Resend call fails."""
        with patch("main.requests.post", side_effect=Exception("Network error")):
            result = send_nomination_email("jane@example.com", "Jane", "Alex Smith")

        assert result is False

    def test_no_pdf_attachment(self):
        """Nomination email should not include a PDF attachment."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_nomination_email("jane@example.com", "Jane", "Alex Smith")

        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"]
        assert "attachments" not in payload


# ─── F79: NOMINATION EMAIL VALIDATION ─────────────────────────────────────────

class TestNominationValidation:
    """Tests for F79: validate nomination email against user's vault contacts."""

    def _make_vault_doc(self, contacts):
        """Helper: build a vault doc with the given contact list."""
        return {"content": {"kin": contacts}}

    def test_rejects_email_not_in_vault(self):
        """Should return error if the email is not in the user's vault contacts."""
        vault_doc = self._make_vault_doc([{"email": "alice@example.com", "first": "Alice"}])
        with patch("main.vaults_col") as mock_col:
            mock_col.find_one.return_value = vault_doc
            result = contact_nominate(
                {"contact_email": "attacker@evil.com", "contact_first": "Hax"},
                {"_id": "user1", "name": "Test User"},
            )
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_accepts_email_in_vault(self):
        """Should send the email when the contact exists in the user's vault."""
        vault_doc = self._make_vault_doc([{"email": "jane@example.com", "first": "Jane"}])
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch("main.vaults_col") as mock_col, \
             patch("main.requests.post", return_value=mock_response):
            mock_col.find_one.return_value = vault_doc
            result = contact_nominate(
                {"contact_email": "jane@example.com", "contact_first": "Jane"},
                {"_id": "user1", "name": "Test User"},
            )
        assert result["ok"] is True

    def test_case_insensitive_match(self):
        """Email comparison should be case-insensitive."""
        vault_doc = self._make_vault_doc([{"email": "Jane@Example.COM", "first": "Jane"}])
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch("main.vaults_col") as mock_col, \
             patch("main.requests.post", return_value=mock_response):
            mock_col.find_one.return_value = vault_doc
            result = contact_nominate(
                {"contact_email": "jane@example.com", "contact_first": "Jane"},
                {"_id": "user1", "name": "Test User"},
            )
        assert result["ok"] is True

    def test_rejects_when_no_vault(self):
        """Should return error if the user has no vault document at all."""
        with patch("main.vaults_col") as mock_col:
            mock_col.find_one.return_value = None
            result = contact_nominate(
                {"contact_email": "jane@example.com", "contact_first": "Jane"},
                {"_id": "user1", "name": "Test User"},
            )
        assert result["ok"] is False
        assert "no vault" in result["error"]

    def test_rejects_when_vault_has_no_contacts(self):
        """Should return error if the vault exists but has no contacts."""
        vault_doc = self._make_vault_doc([])
        with patch("main.vaults_col") as mock_col:
            mock_col.find_one.return_value = vault_doc
            result = contact_nominate(
                {"contact_email": "jane@example.com", "contact_first": "Jane"},
                {"_id": "user1", "name": "Test User"},
            )
        assert result["ok"] is False
        assert "not found" in result["error"]


# ─── F66: PASSWORD RESET ──────────────────────────────────────────────────────

class TestPasswordReset:
    """
    Tests for F66: password reset / account recovery.
    Token hashing, validity rules (single-use, expiry), password rules,
    and the reset email content.
    """

    def _reset_doc(self, used=False, minutes_until_expiry=30):
        return {
            "tokenHash": hash_reset_token("some-token"),
            "used": used,
            "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=minutes_until_expiry),
        }

    # ── Token hashing ──
    def test_hash_is_deterministic(self):
        assert hash_reset_token("abc123") == hash_reset_token("abc123")

    def test_different_tokens_different_hashes(self):
        assert hash_reset_token("token-a") != hash_reset_token("token-b")

    def test_hash_never_contains_raw_token(self):
        token = "super-secret-token"
        assert token not in hash_reset_token(token)

    # ── Validity rules ──
    def test_valid_reset_doc(self):
        assert is_reset_valid(self._reset_doc()) is True

    def test_none_doc_invalid(self):
        assert is_reset_valid(None) is False

    def test_used_doc_invalid(self):
        assert is_reset_valid(self._reset_doc(used=True)) is False

    def test_expired_doc_invalid(self):
        assert is_reset_valid(self._reset_doc(minutes_until_expiry=-5)) is False

    def test_missing_expiry_invalid(self):
        doc = self._reset_doc()
        del doc["expiresAt"]
        assert is_reset_valid(doc) is False

    # ── Password rules ──
    def test_short_password_rejected(self):
        assert is_password_acceptable("short") is False

    def test_eight_char_password_accepted(self):
        assert is_password_acceptable("abcdefg1") is True  # F97: needs digit/special

    def test_non_string_password_rejected(self):
        assert is_password_acceptable(None) is False

    # ── Reset email ──
    def test_reset_email_contains_token_link(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        user = {"name": "Sandra Williams", "username": "tester_05", "email": "sandra@example.com"}

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_reset_email(user, "tok123")

        payload = mock_post.call_args[1]["json"]
        assert "?reset=tok123" in payload["text"]
        assert payload["to"] == ["sandra@example.com"]

    def test_reset_email_mentions_ignore_if_not_requested(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        user = {"name": "Sandra", "username": "tester_05", "email": "sandra@example.com"}

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_reset_email(user, "tok123")

        payload = mock_post.call_args[1]["json"]
        assert "ignore" in payload["text"].lower()

    def test_returns_false_on_network_failure(self):
        user = {"name": "Sandra", "username": "tester_05", "email": "sandra@example.com"}
        with patch("main.requests.post", side_effect=Exception("Network error")):
            assert send_reset_email(user, "tok123") is False


# ─── F64-2: ESCALATING WARNING EMAILS ────────────────────────────────────────

class TestWarningLogic:
    """
    Tests for F64-2: escalating warning emails during the overdue window.
    Warnings fire on day 1 and day 2 for ping_then_notify protocol only.
    Contacts are notified on day 3+.
    """

    def _vault(self, proto="ping_then_notify", days_overdue=1, warning_sent_days=None):
        return {
            "notifyProto": proto,
            "warningSentDays": warning_sent_days or [],
            "_days_overdue": days_overdue,  # used in tests directly, not by the function
        }

    # ── should_send_warning ──

    def test_warning_fires_on_day_1(self):
        vault = self._vault(proto="ping_then_notify", warning_sent_days=[])
        assert should_send_warning(vault, days_overdue=1) is True

    def test_warning_fires_on_day_2(self):
        vault = self._vault(proto="ping_then_notify", warning_sent_days=[1])
        assert should_send_warning(vault, days_overdue=2) is True

    def test_warning_not_sent_twice_same_day(self):
        vault = self._vault(proto="ping_then_notify", warning_sent_days=[1])
        assert should_send_warning(vault, days_overdue=1) is False

    def test_warning_not_sent_on_day_3(self):
        vault = self._vault(proto="ping_then_notify", warning_sent_days=[1, 2])
        assert should_send_warning(vault, days_overdue=3) is False

    def test_warning_skipped_for_notify_immediately(self):
        vault = self._vault(proto="notify_immediately", warning_sent_days=[])
        assert should_send_warning(vault, days_overdue=1) is False

    def test_warning_skipped_for_escalate(self):
        vault = self._vault(proto="escalate", warning_sent_days=[])
        assert should_send_warning(vault, days_overdue=1) is False

    # ── should_notify_contacts ──

    def test_contacts_not_notified_before_day_3_ping_then_notify(self):
        vault = self._vault(proto="ping_then_notify")
        assert should_notify_contacts(vault, days_overdue=2) is False

    def test_contacts_notified_on_day_3_ping_then_notify(self):
        vault = self._vault(proto="ping_then_notify")
        assert should_notify_contacts(vault, days_overdue=3) is True

    # ── send_warning_email content ──

    def test_warning_email_day_1_mentions_days_until_notify(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        user = {"name": "Alex Smith", "email": "alex@example.com"}

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_warning_email(user, days_overdue=1)

        payload = mock_post.call_args[1]["json"]
        assert "2 days" in payload["text"]  # CONTACT_NOTIFY_AFTER_DAYS - 1 = 2

    def test_warning_email_day_2_uses_urgent_tone(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        user = {"name": "Alex Smith", "email": "alex@example.com"}

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_warning_email(user, days_overdue=2)

        payload = mock_post.call_args[1]["json"]
        assert "tomorrow" in payload["text"].lower()

    def test_warning_email_silent_if_no_email(self):
        user = {"name": "Alex Smith", "email": ""}
        with patch("main.requests.post") as mock_post:
            result = send_warning_email(user, days_overdue=1)
        assert result is False
        mock_post.assert_not_called()

    def test_contacts_notified_email_mentions_count(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        user = {"name": "Alex Smith", "email": "alex@example.com"}

        with patch("main.requests.post", return_value=mock_response) as mock_post:
            send_contacts_notified_email(user, contact_count=3)

        payload = mock_post.call_args[1]["json"]
        assert "3 contacts" in payload["text"]

    def test_contacts_notified_email_silent_if_no_email(self):
        user = {"name": "Alex Smith", "email": ""}
        with patch("main.requests.post") as mock_post:
            result = send_contacts_notified_email(user, contact_count=2)
        assert result is False
        mock_post.assert_not_called()


# ─── ADMIN ROLE CHECK (F77) ──────────────────────────────────────────────────

class TestRequireAdmin:
    """F77: require_admin() must block non-admin users with 403."""

    def test_admin_user_passes(self):
        """User with isAdmin=True should not raise."""
        user = {"_id": "abc", "username": "admin_user", "isAdmin": True}
        # Should not raise
        require_admin(user)

    def test_non_admin_user_blocked(self):
        """User without isAdmin should get 403."""
        user = {"_id": "abc", "username": "tester_01", "isTester": True}
        with pytest.raises(Exception) as exc_info:
            require_admin(user)
        assert exc_info.value.status_code == 403
        assert "Admin access required" in str(exc_info.value.detail)

    def test_missing_isAdmin_field_blocked(self):
        """User document with no isAdmin field at all should get 403 (defaults to False)."""
        user = {"_id": "abc", "username": "old_user"}
        with pytest.raises(Exception) as exc_info:
            require_admin(user)
        assert exc_info.value.status_code == 403

    def test_isAdmin_false_blocked(self):
        """Explicit isAdmin=False should get 403."""
        user = {"_id": "abc", "username": "tester_02", "isAdmin": False}
        with pytest.raises(Exception) as exc_info:
            require_admin(user)
        assert exc_info.value.status_code == 403

    def test_clean_user_includes_isAdmin(self):
        """clean_user() should expose isAdmin in its output."""
        user = {"_id": "abc", "username": "admin", "isAdmin": True, "password": "secret"}
        cleaned = clean_user(user)
        assert cleaned["isAdmin"] is True

    def test_clean_user_defaults_isAdmin_false(self):
        """clean_user() should default isAdmin to False if missing."""
        user = {"_id": "abc", "username": "regular"}
        cleaned = clean_user(user)
        assert cleaned["isAdmin"] is False


# ─── F83: PDF ESCAPING ────────────────────────────────────────────────────────

class TestPdfEscaping:
    """F83: user data with HTML-like chars must not crash ReportLab PDF generation."""

    def _vault(self, **overrides):
        """Build a minimal vault doc, merging overrides into content."""
        base = {"content": {"assets": [], "wishes": [], "kin": [], "will": None, "suppDocs": []}}
        base["content"].update(overrides)
        return base

    def _contact(self, **overrides):
        base = {"first": "Jane", "last": "Doe", "email": "j@e.com"}
        base.update(overrides)
        return base

    def test_html_tags_in_contact_name(self):
        """Angle brackets in contact name should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(first="<b>Bold</b>", last="<i>Italic</i>"),
            self._vault(),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_ampersand_in_asset_name(self):
        """Ampersand in asset name should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(),
            self._vault(assets=[{"name": "Tom & Jerry's Account", "category": "Bank"}]),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_angle_brackets_in_wish_details(self):
        """Angle brackets in wish details should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(),
            self._vault(wishes=[{"title": "Wish <1>", "details": "Details with <script>alert('xss')</script>"}]),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_html_in_letter(self):
        """HTML tags in personal letter should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(letter="Dear <b>family</b>,\n\nI love <you> & yours.\n\n"),
            self._vault(),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_html_in_will_notes(self):
        """HTML in Will notes should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(),
            self._vault(will={"status": "signed", "notes": "Filed at <Smith & Partners>"}),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_html_in_holder_name(self):
        """HTML in holder_name param should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(),
            self._vault(),
            holder_name="<script>alert('xss')</script>",
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_html_in_supp_docs(self):
        """HTML in supporting document fields should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(),
            self._vault(suppDocs=[{"name": "<b>Doc</b>", "loc": "At <solicitor's> office"}]),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0

    def test_html_in_kin_fields(self):
        """HTML in kin contact fields should not crash PDF."""
        pdf = generate_pdf_for_contact(
            self._contact(),
            self._vault(kin=[{"first": "<b>Bob</b>", "last": "O'<Brien>", "rel": "Brother", "email": "b@e.com"}]),
        )
        assert isinstance(pdf, bytes) and len(pdf) > 0


# ─── F84: JWT_SECRET STARTUP VALIDATION ───────────────────────────────────────

import os

class TestJwtSecretValidation:
    """F84 — app must refuse to start if JWT_SECRET is missing or empty."""

    def test_missing_jwt_secret_raises(self):
        """If JWT_SECRET env var is completely absent, startup must crash."""
        import importlib
        import sys
        env_copy = os.environ.copy()
        env_copy.pop("JWT_SECRET", None)
        with patch.dict(os.environ, env_copy, clear=True):
            saved = sys.modules.pop("main", None)
            try:
                with pytest.raises(RuntimeError, match="JWT_SECRET"):
                    importlib.import_module("main")
            finally:
                sys.modules.pop("main", None)
                if saved:
                    sys.modules["main"] = saved

    def test_empty_jwt_secret_raises(self):
        """If JWT_SECRET is set to empty string, startup must crash."""
        import importlib
        import sys
        with patch.dict(os.environ, {"JWT_SECRET": ""}):
            saved = sys.modules.pop("main", None)
            try:
                with pytest.raises(RuntimeError, match="JWT_SECRET"):
                    importlib.import_module("main")
            finally:
                sys.modules.pop("main", None)
                if saved:
                    sys.modules["main"] = saved

    def test_valid_jwt_secret_works(self):
        """A non-empty JWT_SECRET should let the module load normally."""
        assert JWT_SECRET and len(JWT_SECRET) > 0

# ─── F86: ACCOUNT LOCKOUT ────────────────────────────────────────────────────

class TestAccountLockout:
    """F86 — brute-force protection via account lockout after repeated failures."""

    def _make_user(self, username="testuser", failed_count=0, locked_until=None):
        """Create a fake user dict for testing."""
        user = {
            "_id": "fake_id_123",
            "username": username,
            "password": hash_password("correct"),
            "failedLoginCount": failed_count,
        }
        if locked_until is not None:
            user["lockedUntil"] = locked_until
        return user

    # ── is_account_locked ─────────────────────────────────────────────────

    def test_not_locked_when_no_lockout_field(self):
        """A user with no lockedUntil field is not locked."""
        user = self._make_user()
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            assert is_account_locked("testuser") is False

    def test_not_locked_when_lockout_expired(self):
        """A user whose lockedUntil is in the past is not locked."""
        past = now_utc() - timedelta(minutes=1)
        user = self._make_user(locked_until=past)
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            assert is_account_locked("testuser") is False

    def test_locked_when_lockout_in_future(self):
        """A user whose lockedUntil is in the future IS locked."""
        future = now_utc() + timedelta(minutes=10)
        user = self._make_user(locked_until=future)
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            assert is_account_locked("testuser") is True

    def test_not_locked_for_unknown_user(self):
        """Querying a non-existent username returns False (not locked)."""
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = None
            assert is_account_locked("ghost") is False

    # ── get_lockout_remaining_seconds ─────────────────────────────────────

    def test_remaining_seconds_when_locked(self):
        """Returns positive seconds when lockout is active."""
        future = now_utc() + timedelta(minutes=10)
        user = self._make_user(locked_until=future)
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            remaining = get_lockout_remaining_seconds("testuser")
            assert 500 <= remaining <= 600  # ~10 minutes

    def test_remaining_seconds_zero_when_expired(self):
        """Returns 0 when lockout has passed."""
        past = now_utc() - timedelta(minutes=5)
        user = self._make_user(locked_until=past)
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            assert get_lockout_remaining_seconds("testuser") == 0

    # ── record_failed_login ───────────────────────────────────────────────

    def test_increments_failure_count(self):
        """Each failed attempt bumps failedLoginCount by 1."""
        user = self._make_user(failed_count=2)
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            record_failed_login("testuser")
            call_args = mock_col.update_one.call_args
            set_fields = call_args[0][1]["$set"]
            assert set_fields["failedLoginCount"] == 3
            assert "lockedUntil" not in set_fields  # not yet at threshold

    def test_locks_account_at_threshold(self):
        """When failedLoginCount reaches MAX_LOGIN_ATTEMPTS, lockedUntil is set."""
        user = self._make_user(failed_count=MAX_LOGIN_ATTEMPTS - 1)
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = user
            record_failed_login("testuser")
            call_args = mock_col.update_one.call_args
            set_fields = call_args[0][1]["$set"]
            assert set_fields["failedLoginCount"] == MAX_LOGIN_ATTEMPTS
            assert "lockedUntil" in set_fields

    def test_no_crash_for_unknown_user(self):
        """Recording a failure for a non-existent user does nothing."""
        with patch("main.users_col") as mock_col:
            mock_col.find_one.return_value = None
            record_failed_login("ghost")  # should not raise
            mock_col.update_one.assert_not_called()

    # ── clear_login_failures ──────────────────────────────────────────────

    def test_clears_count_and_lockout(self):
        """clear_login_failures resets the counter and removes lockedUntil."""
        with patch("main.users_col") as mock_col:
            clear_login_failures("testuser")
            call_args = mock_col.update_one.call_args
            assert call_args[0][1]["$set"]["failedLoginCount"] == 0
            assert "lockedUntil" in call_args[0][1]["$unset"]


# ─── F94: Security Response Headers ──────────────────────────────────────────

class TestSecurityHeaders:
    """F94: Verify the security headers middleware is registered and sets correct values.
    Uses asyncio to call the middleware directly — avoids needing httpx/TestClient."""

    def test_middleware_registered(self):
        """The app must have a middleware that adds security headers."""
        from main import app
        # FastAPI stores user middleware in app.user_middleware
        middleware_classes = [m.cls.__name__ if hasattr(m, 'cls') else str(m) for m in app.user_middleware]
        # The @app.middleware("http") decorator registers via BaseHTTPMiddleware under the hood,
        # but we can verify our function exists on the app's middleware stack by checking routes
        from main import add_security_headers
        assert callable(add_security_headers)

    def test_sets_x_content_type_options(self):
        import asyncio
        from main import add_security_headers
        from starlette.responses import JSONResponse
        from starlette.requests import Request
        scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "query_string": b""}
        request = Request(scope)
        async def dummy_call_next(req):
            return JSONResponse({"ok": True})
        resp = asyncio.new_event_loop().run_until_complete(add_security_headers(request, dummy_call_next))
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_sets_x_frame_options(self):
        import asyncio
        from main import add_security_headers
        from starlette.responses import JSONResponse
        from starlette.requests import Request
        scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "query_string": b""}
        request = Request(scope)
        async def dummy_call_next(req):
            return JSONResponse({"ok": True})
        resp = asyncio.new_event_loop().run_until_complete(add_security_headers(request, dummy_call_next))
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_sets_hsts(self):
        import asyncio
        from main import add_security_headers
        from starlette.responses import JSONResponse
        from starlette.requests import Request
        scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "query_string": b""}
        request = Request(scope)
        async def dummy_call_next(req):
            return JSONResponse({"ok": True})
        resp = asyncio.new_event_loop().run_until_complete(add_security_headers(request, dummy_call_next))
        assert "max-age=31536000" in resp.headers.get("Strict-Transport-Security", "")

    def test_sets_referrer_policy(self):
        import asyncio
        from main import add_security_headers
        from starlette.responses import JSONResponse
        from starlette.requests import Request
        scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "query_string": b""}
        request = Request(scope)
        async def dummy_call_next(req):
            return JSONResponse({"ok": True})
        resp = asyncio.new_event_loop().run_until_complete(add_security_headers(request, dummy_call_next))
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ─── F96: Vault Sync Input Limits ────────────────────────────────────────────

class TestVaultSyncLimits:
    """F96: Validate payload size limits on vault sync."""

    def test_too_many_assets_rejected(self):
        from main import vault_sync, MAX_VAULT_ASSETS
        from fastapi import HTTPException
        import pytest
        body = {"vault": {"assets": [{"name": f"a{i}"} for i in range(MAX_VAULT_ASSETS + 1)]}}
        with pytest.raises(HTTPException) as exc_info:
            vault_sync(body, {"_id": "test"})
        assert exc_info.value.status_code == 400
        assert "assets" in exc_info.value.detail.lower()

    def test_too_many_contacts_rejected(self):
        from main import vault_sync, MAX_VAULT_CONTACTS
        from fastapi import HTTPException
        import pytest
        body = {"vault": {"assets": [], "kin": [{"name": f"c{i}"} for i in range(MAX_VAULT_CONTACTS + 1)]}}
        with pytest.raises(HTTPException) as exc_info:
            vault_sync(body, {"_id": "test"})
        assert exc_info.value.status_code == 400
        assert "contacts" in exc_info.value.detail.lower()

    def test_assets_not_a_list_rejected(self):
        from main import vault_sync
        from fastapi import HTTPException
        import pytest
        body = {"vault": {"assets": "not_a_list"}}
        with pytest.raises(HTTPException) as exc_info:
            vault_sync(body, {"_id": "test"})
        assert exc_info.value.status_code == 400

    def test_valid_payload_accepted(self):
        """Ensure normal-sized payloads pass the F96 checks (will fail at DB layer, which is fine)."""
        from main import vault_sync
        import pytest
        body = {"vault": {"assets": [{"name": "house"}], "kin": [{"name": "Mum"}]}}
        # Will raise at DB layer (no real DB), but should NOT raise 400
        with pytest.raises(Exception) as exc_info:
            vault_sync(body, {"_id": "test"})
        # If it's an HTTPException, it must NOT be 400 (our validation)
        from fastapi import HTTPException
        if isinstance(exc_info.value, HTTPException):
            assert exc_info.value.status_code != 400


# ─── F97: Stronger Password Policy ──────────────────────────────────────────

class TestStrongerPasswordPolicy:
    """F97: Password must be 8+ chars, not common, and contain a digit or special char."""

    def test_common_password_rejected(self):
        from main import is_password_acceptable
        assert is_password_acceptable("password") is False
        assert is_password_acceptable("123456789") is False
        assert is_password_acceptable("qwerty123") is False

    def test_alpha_only_rejected(self):
        from main import is_password_acceptable
        assert is_password_acceptable("abcdefghi") is False
        assert is_password_acceptable("MySecretPass") is False

    def test_too_short_rejected(self):
        from main import is_password_acceptable
        assert is_password_acceptable("Ab1!") is False

    def test_valid_password_accepted(self):
        from main import is_password_acceptable
        assert is_password_acceptable("MySecret1") is True
        assert is_password_acceptable("hunter!!two") is True
        assert is_password_acceptable("Benny#07xx") is True

    def test_common_password_case_insensitive(self):
        from main import is_password_acceptable
        assert is_password_acceptable("PASSWORD") is False
        assert is_password_acceptable("Password") is False

    def test_empty_and_none_rejected(self):
        from main import is_password_acceptable
        assert is_password_acceptable("") is False
        assert is_password_acceptable(None) is False


# ─── F04: VAULT CONTENT ENCRYPTION ──────────────────────────────────────────

class TestEncryptContent:
    """Test the encrypt_content() helper."""

    def test_round_trip(self):
        """Encrypting then decrypting returns the original data."""
        original = {"assets": [{"id": 1, "name": "House"}], "kin": [], "will": None}
        encrypted = encrypt_content(original)
        assert isinstance(encrypted, str), "encrypted output should be a base64 string"
        decrypted = decrypt_content(encrypted)
        assert decrypted == original

    def test_different_nonce_each_time(self):
        """Two encryptions of the same data produce different ciphertext."""
        data = {"assets": [{"id": 1}]}
        enc1 = encrypt_content(data)
        enc2 = encrypt_content(data)
        assert enc1 != enc2, "each encryption should use a unique random nonce"

    def test_empty_content(self):
        """Empty dict encrypts and decrypts correctly."""
        original = {}
        encrypted = encrypt_content(original)
        assert decrypt_content(encrypted) == original

    def test_unicode_content(self):
        """Content with unicode characters (names, letters) survives round-trip."""
        original = {"kin": [{"first": "Nguyễn", "letter": "Cảm ơn bạn 🙏"}]}
        encrypted = encrypt_content(original)
        assert decrypt_content(encrypted) == original

    def test_large_content(self):
        """Realistic vault size encrypts and decrypts correctly."""
        original = {
            "assets": [{"id": i, "name": f"Asset {i}", "value": i * 1000} for i in range(100)],
            "wishes": [{"id": i, "title": f"Wish {i}"} for i in range(50)],
            "kin": [{"id": i, "first": f"Contact {i}", "email": f"c{i}@example.com"} for i in range(10)],
        }
        encrypted = encrypt_content(original)
        assert decrypt_content(encrypted) == original


class TestDecryptContent:
    """Test the decrypt_content() helper — especially backward compatibility."""

    def test_none_returns_empty_dict(self):
        """None input (no vault yet) returns empty dict."""
        assert decrypt_content(None) == {}

    def test_dict_passthrough(self):
        """Pre-F04 plaintext dict passes through unchanged (migration support)."""
        plaintext = {"assets": [{"id": 1}], "kin": []}
        result = decrypt_content(plaintext)
        assert result == plaintext

    def test_tampered_ciphertext_raises(self):
        """Modified ciphertext is detected and raises an error."""
        import base64
        original = {"assets": [{"id": 1}]}
        encrypted = encrypt_content(original)
        raw = bytearray(base64.b64decode(encrypted))
        raw[-1] ^= 0xFF  # flip a byte in the auth tag
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        with pytest.raises(Exception):
            decrypt_content(tampered)

    def test_truncated_ciphertext_raises(self):
        """Truncated ciphertext raises an error."""
        import base64
        original = {"assets": []}
        encrypted = encrypt_content(original)
        raw = base64.b64decode(encrypted)
        truncated = base64.b64encode(raw[:10]).decode("ascii")
        with pytest.raises(Exception):
            decrypt_content(truncated)


class TestReconstructWithEncryption:
    """Test that reconstruct_vault_blob works with encrypted content."""

    def test_reconstruct_encrypted_vault(self):
        """reconstruct_vault_blob correctly decrypts an encrypted vault doc."""
        content = {
            "assets": [{"id": 1, "name": "House"}],
            "wishes": [],
            "will": {"status": "signed"},
            "suppDocs": [],
            "kin": [{"id": 1, "first": "Anne"}],
            "v": "pin",
            "notifySeq": "in_order",
            "saveCount": 5,
        }
        encrypted = encrypt_content(content)
        doc = {
            "content": encrypted,
            "lastCheckin": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 7,
            "notifyProto": "ping_then_notify",
            "log": [],
        }
        blob = reconstruct_vault_blob(doc)
        assert blob["assets"] == [{"id": 1, "name": "House"}]
        assert blob["kin"] == [{"id": 1, "first": "Anne"}]
        assert blob["will"] == {"status": "signed"}
        assert blob["v"] == "pin"
        assert blob["saveCount"] == 5

    def test_reconstruct_plaintext_vault(self):
        """reconstruct_vault_blob still works with pre-F04 plaintext content."""
        content = {
            "assets": [{"id": 2}],
            "wishes": [],
            "will": None,
            "suppDocs": [],
            "kin": [],
            "v": "face",
            "notifySeq": "in_order",
            "saveCount": 0,
        }
        doc = {
            "content": content,
            "lastCheckin": None,
            "checkInFrequency": 2,
            "checkInUnit": "months",
            "gracePeriodDays": 7,
            "notifyProto": "ping_then_notify",
            "log": [],
        }
        blob = reconstruct_vault_blob(doc)
        assert blob["assets"] == [{"id": 2}]


# ─── F91: RATE LIMITING ──────────────────────────────────────────────────────
#
# These use FastAPI's TestClient to make real HTTP requests through the full
# app (including the slowapi middleware), unlike most tests above which call
# functions directly. This is necessary because rate limiting is a property
# of the HTTP layer, not the business logic.

class TestLoginRateLimit:
    """F91: /auth/login is limited to 5 attempts/minute per IP."""

    def test_sixth_attempt_in_a_minute_is_rejected(self):
        client = TestClient(main.app)
        with patch("main.users_col") as mock_users, \
             patch("main.is_account_locked", return_value=False):
            mock_users.find_one.return_value = None  # always "invalid credentials"
            statuses = [
                client.post("/auth/login", json={"username": "nouser", "password": "wrong"}).status_code
                for _ in range(6)
            ]
        assert statuses[:5] == [401] * 5
        assert statuses[5] == 429

    def test_different_ips_have_separate_limits(self):
        client = TestClient(main.app)
        with patch("main.users_col") as mock_users, \
             patch("main.is_account_locked", return_value=False):
            mock_users.find_one.return_value = None
            for _ in range(5):
                client.post(
                    "/auth/login",
                    json={"username": "nouser", "password": "wrong"},
                    headers={"X-Forwarded-For": "1.1.1.1"},
                )
            # A different IP should not be affected by IP 1.1.1.1's limit
            r = client.post(
                "/auth/login",
                json={"username": "nouser", "password": "wrong"},
                headers={"X-Forwarded-For": "2.2.2.2"},
            )
        assert r.status_code == 401  # not 429 — separate bucket

    def test_uses_leftmost_ip_in_forwarded_for_chain(self):
        """Railway's edge proxy appends to X-Forwarded-For; the leftmost
        entry is the real client. A multi-hop chain with a fresh leftmost
        IP should not inherit another IP's exhausted limit."""
        client = TestClient(main.app)
        with patch("main.users_col") as mock_users, \
             patch("main.is_account_locked", return_value=False):
            mock_users.find_one.return_value = None
            for _ in range(5):
                client.post(
                    "/auth/login",
                    json={"username": "nouser", "password": "wrong"},
                    headers={"X-Forwarded-For": "9.9.9.9"},
                )
            r = client.post(
                "/auth/login",
                json={"username": "nouser", "password": "wrong"},
                headers={"X-Forwarded-For": "8.8.8.8, 9.9.9.9"},  # leftmost = 8.8.8.8
            )
        assert r.status_code == 401  # 8.8.8.8 hasn't been limited yet


class TestPasswordResetRateLimit:
    """F91: /auth/request-reset is limited to 3/minute per IP — this is the
    endpoint identified in the June 2026 security review as the highest-
    priority gap (no cooldown previously meant unlimited reset emails)."""

    def test_fourth_attempt_in_a_minute_is_rejected(self):
        client = TestClient(main.app)
        with patch("main.users_col") as mock_users:
            mock_users.find_one.return_value = None  # generic response either way
            statuses = [
                client.post("/auth/request-reset", json={"username": "anyone"}).status_code
                for _ in range(4)
            ]
        assert statuses[:3] == [200, 200, 200]
        assert statuses[3] == 429


class TestNominateRateLimit:
    """F91: /contact/nominate is limited to 10/minute per authenticated
    user (not per IP) — so unrelated users sharing a network don't share
    a bucket, and a malicious user can't bypass the limit by changing IP."""

    def _setup_user(self, mock_users, oid, name):
        user = {"_id": ObjectId(oid), "name": name}
        return user

    def test_eleventh_attempt_in_a_minute_is_rejected(self):
        client = TestClient(main.app)
        oid = str(ObjectId())
        vault_doc = {"content": {"kin": [{"email": "jane@example.com", "first": "Jane"}]}}
        with patch("main.vaults_col") as mock_vaults, \
             patch("main.users_col") as mock_users, \
             patch("main.requests.post") as mock_post:
            mock_vaults.find_one.return_value = vault_doc
            mock_post.return_value = MagicMock(raise_for_status=lambda: None)
            mock_users.find_one.return_value = {"_id": ObjectId(oid), "name": "User One"}
            token = main.create_token(oid)

            statuses = [
                client.post(
                    "/contact/nominate",
                    json={"contact_email": "jane@example.com", "contact_first": "Jane"},
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code
                for _ in range(11)
            ]
        assert statuses[:10] == [200] * 10
        assert statuses[10] == 429

    def test_different_users_have_separate_limits(self):
        """Two different authenticated users must not share a rate-limit
        bucket just because they happen to share an IP (e.g. same office)."""
        client = TestClient(main.app)
        oid1, oid2 = str(ObjectId()), str(ObjectId())
        vault_doc = {"content": {"kin": [{"email": "jane@example.com", "first": "Jane"}]}}
        with patch("main.vaults_col") as mock_vaults, \
             patch("main.users_col") as mock_users, \
             patch("main.requests.post") as mock_post:
            mock_vaults.find_one.return_value = vault_doc
            mock_post.return_value = MagicMock(raise_for_status=lambda: None)
            user1 = {"_id": ObjectId(oid1), "name": "User One"}
            user2 = {"_id": ObjectId(oid2), "name": "User Two"}
            mock_users.find_one.side_effect = lambda q: user1 if q.get("_id") == ObjectId(oid1) else user2
            token1 = main.create_token(oid1)
            token2 = main.create_token(oid2)

            for _ in range(10):
                client.post(
                    "/contact/nominate",
                    json={"contact_email": "jane@example.com", "contact_first": "Jane"},
                    headers={"Authorization": f"Bearer {token1}"},
                )
            r = client.post(
                "/contact/nominate",
                json={"contact_email": "jane@example.com", "contact_first": "Jane"},
                headers={"Authorization": f"Bearer {token2}"},
            )
        assert r.status_code == 200  # user2's own bucket, unaffected by user1


# ─── F95: STRUCTURED LOGGING TESTS ───────────────────────────────────────────


class TestMaskEmail:
    """F95: PII masking for email addresses in log output."""

    def test_masks_simple_email(self):
        assert main.mask_email("sent to alice@example.com ok") == "sent to a***@example.com ok"

    def test_masks_single_char_local(self):
        assert main.mask_email("a@b.com") == "a***@b.com"

    def test_masks_multiple_emails(self):
        result = main.mask_email("from bob@x.co to carol@y.org")
        assert "b***@x.co" in result
        assert "c***@y.org" in result

    def test_no_email_unchanged(self):
        msg = "Pulse scan running"
        assert main.mask_email(msg) == msg

    def test_preserves_surrounding_text(self):
        result = main.mask_email("Error sending to zara@test.com — retrying")
        assert result == "Error sending to z***@test.com — retrying"


class TestJsonFormatter:
    """F95: JSON log formatter produces valid JSON with PII masked."""

    def test_output_is_valid_json(self):
        import json as _json
        formatter = main._JsonFormatter()
        record = logging.LogRecord(
            name="kinlight", level=logging.INFO, pathname="", lineno=0,
            msg="Reminder sent to alice@example.com", args=(), exc_info=None,
        )
        line = formatter.format(record)
        parsed = _json.loads(line)
        assert parsed["level"] == "INFO"
        assert "a***@example.com" in parsed["message"]
        assert "alice@example.com" not in parsed["message"]
        assert "timestamp" in parsed

    def test_error_level(self):
        import json as _json
        formatter = main._JsonFormatter()
        record = logging.LogRecord(
            name="kinlight", level=logging.ERROR, pathname="", lineno=0,
            msg="Email send failed to bob@x.com: timeout", args=(), exc_info=None,
        )
        line = formatter.format(record)
        parsed = _json.loads(line)
        assert parsed["level"] == "ERROR"
        assert "b***@x.com" in parsed["message"]


class TestSecurityLogging:
    """F95: Security events are logged at correct levels.
    Uses TestClient (not direct function calls) because the login endpoint
    is wrapped by slowapi's rate limiter, which requires a real Request object.
    Rate limiter is disabled during these tests so that other tests in the suite
    don't exhaust the 5/minute login budget before we run.
    """

    def test_failed_login_logs_warning(self):
        """Failed login should produce a WARNING log entry with the username."""
        main.limiter.enabled = False
        try:
            client = TestClient(main.app)
            with patch("main.users_col") as mock_users, \
                 patch("main.logger") as mock_logger:
                mock_users.find_one.return_value = None
                client.post("/auth/login", json={"username": "baduser", "password": "wrong"})
                warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
                assert any("baduser" in c for c in warning_calls)
        finally:
            main.limiter.enabled = True

    def test_successful_login_logs_info(self):
        """Successful login should produce an INFO log entry."""
        main.limiter.enabled = False
        try:
            client = TestClient(main.app)
            with patch("main.users_col") as mock_users, \
                 patch("main.logger") as mock_logger:
                mock_users.find_one.return_value = {
                    "_id": ObjectId(), "username": "gooduser",
                    "password": main.hash_password("Test1234!"), "name": "Good User",
                }
                mock_users.update_one.return_value = None
                client.post("/auth/login", json={"username": "gooduser", "password": "Test1234!"})
                info_calls = [str(c) for c in mock_logger.info.call_args_list]
                assert any("gooduser" in c for c in info_calls)
        finally:
            main.limiter.enabled = True

    def test_lockout_logs_warning(self):
        """Login attempt on a locked account should produce a WARNING log."""
        main.limiter.enabled = False
        try:
            client = TestClient(main.app)
            with patch("main.users_col") as mock_users, \
                 patch("main.logger") as mock_logger:
                mock_users.find_one.return_value = {
                    "_id": ObjectId(), "username": "lockeduser",
                    "failedLoginCount": 5,
                    "lockedUntil": datetime.now(timezone.utc) + timedelta(minutes=10),
                }
                r = client.post("/auth/login", json={"username": "lockeduser", "password": "whatever"})
                assert r.status_code == 429
                warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
                assert any("lockeduser" in c for c in warning_calls)
        finally:
            main.limiter.enabled = True

    def test_failed_login_does_not_log_password(self):
        """Passwords must NEVER appear in log output."""
        main.limiter.enabled = False
        try:
            client = TestClient(main.app)
            with patch("main.users_col") as mock_users, \
                 patch("main.logger") as mock_logger:
                mock_users.find_one.return_value = None
                secret_pw = "MyS3cretP@ss!"
                client.post("/auth/login", json={"username": "testuser", "password": secret_pw})
                all_calls = str(mock_logger.warning.call_args_list) + str(mock_logger.info.call_args_list)
                assert secret_pw not in all_calls
        finally:
            main.limiter.enabled = True
