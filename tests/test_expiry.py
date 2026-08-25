"""LinkedIn token expiry arithmetic.

The token itself carries no expiry we can read back, so the warning is driven by
a recorded issue date plus the documented 60-day lifetime, with a live probe as
the tiebreaker when the two disagree.
"""
from datetime import date

import pytest

from syndicate.expiry import (
    NOT_CONFIGURED,
    EXPIRED,
    OK,
    UNKNOWN,
    WARNING,
    parse_issued_at,
    token_status,
)

TODAY = date(2026, 8, 25)


def test_parses_a_plain_date():
    assert parse_issued_at("2026-08-01") == date(2026, 8, 1)


def test_parses_a_full_timestamp_down_to_the_date():
    assert parse_issued_at("2026-08-01T09:30:00Z") == date(2026, 8, 1)


def test_rejects_something_that_is_not_a_date():
    with pytest.raises(ValueError) as exc:
        parse_issued_at("last tuesday")
    assert "LINKEDIN_TOKEN_ISSUED_AT" in str(exc.value)


def test_fresh_token_is_ok_with_days_remaining():
    status = token_status(issued_at="2026-08-20", probe="valid", today=TODAY)
    assert status.status == OK
    assert status.days_remaining == 55
    assert status.expires_at == date(2026, 10, 19).isoformat()


def test_token_inside_the_warning_window_warns():
    status = token_status(issued_at="2026-07-01", probe="valid", today=TODAY)
    assert status.status == WARNING
    assert status.days_remaining == 5


def test_warning_window_is_configurable():
    status = token_status(issued_at="2026-07-01", probe="valid", warn_days=3, today=TODAY)
    assert status.status == OK


def test_token_past_its_lifetime_is_expired():
    status = token_status(issued_at="2026-06-01", probe="valid", today=TODAY)
    assert status.status == EXPIRED
    assert status.days_remaining == -25


def test_a_live_probe_that_says_expired_overrides_a_healthy_looking_date():
    status = token_status(issued_at="2026-08-20", probe="expired", today=TODAY)
    assert status.status == EXPIRED
    assert "rejected" in status.message.lower()


def test_no_recorded_issue_date_is_unknown_not_ok():
    status = token_status(issued_at=None, probe="valid", today=TODAY)
    assert status.status == UNKNOWN
    assert "LINKEDIN_TOKEN_ISSUED_AT" in status.message


def test_absent_credentials_are_not_configured_not_a_failure():
    status = token_status(issued_at=None, probe=None, today=TODAY)
    assert status.status == NOT_CONFIGURED


def test_an_inconclusive_probe_still_trusts_the_recorded_date():
    status = token_status(issued_at="2026-07-01", probe="inconclusive", today=TODAY)
    assert status.status == WARNING


def test_an_issue_date_in_the_future_is_rejected():
    with pytest.raises(ValueError):
        token_status(issued_at="2026-09-01", probe="valid", today=TODAY)


def test_status_serialises_for_the_workflow():
    payload = token_status(issued_at="2026-07-01", probe="valid", today=TODAY).as_dict()
    assert payload["status"] == WARNING
    assert payload["days_remaining"] == 5
    assert payload["issued_at"] == "2026-07-01"
