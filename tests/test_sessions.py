import base64
import json

import pytest

from internal_tool_kit import accounts, passwords, sessions
from internal_tool_kit.accounts import AccountStore
from internal_tool_kit.storage import MemoryStorage

SECRET = sessions.new_secret()


class Clock:
    def __init__(self):
        self.now = 2_000_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def store():
    s = AccountStore(MemoryStorage({"accounts": accounts.COLUMNS}))
    s.create("anna", "Anna", "member", passwords.hash_password("river7stone"))
    return s


@pytest.fixture
def clock():
    return Clock()


def check(store, token, clock, secret=SECRET):
    return sessions.verify(token, secret, get_account=lambda i: store.get(i, fresh=True), clock=clock)


def test_a_token_signs_its_account_in_until_it_expires(store, clock):
    anna = store.get(1)
    token = sessions.issue(anna, SECRET, days=7, clock=clock)
    assert check(store, token, clock) == anna
    clock.now += 7 * 86400 - 1
    assert check(store, token, clock) == anna
    clock.now += 1
    assert check(store, token, clock) is None


def test_a_password_change_voids_older_tokens(store, clock):
    token = sessions.issue(store.get(1), SECRET, clock=clock)
    store.set_password(1, passwords.hash_password("ocean4wind"), must_change_password=False)
    assert check(store, token, clock) is None
    assert check(store, sessions.issue(store.get(1), SECRET, clock=clock), clock) is not None


def test_deactivation_ends_the_session_and_a_role_change_shows_at_once(store, clock):
    token = sessions.issue(store.get(1), SECRET, clock=clock)
    store.update(1, role="admin")
    assert check(store, token, clock).role == "admin"
    store.update(1, active=False)
    assert check(store, token, clock) is None
    assert sessions.current_account("1", 1, 1, get_account=lambda i: store.get(i, fresh=True)) is None


def test_a_renamed_account_keeps_its_token_and_the_old_name_gives_nothing(store, clock):
    token = sessions.issue(store.get(1), SECRET, clock=clock)
    store.rename(1, "anna.old")
    store.create("anna", "Another Anna", "member", "")
    assert check(store, token, clock).display_name == "Anna"


def test_tampering_and_other_secrets_are_refused(store, clock):
    token = sessions.issue(store.get(1), SECRET, clock=clock)
    payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["a"] = "2"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    store.create("other", "Other", "admin", "")
    assert check(store, f"{forged}.{signature}", clock) is None
    assert check(store, token, clock, secret=sessions.new_secret()) is None
    for bad in ["", "x", "a.b.c", f"{payload}.", f".{signature}", token + "x", "é.é"]:
        assert check(store, bad, clock) is None


def test_secrets_rotate_without_signing_everyone_out(store, clock):
    old, new = SECRET, sessions.new_secret()
    token = sessions.issue(store.get(1), old, clock=clock)
    assert check(store, token, clock, secret=[new, old]) is not None
    fresh = sessions.issue(store.get(1), [new, old], clock=clock)
    assert check(store, fresh, clock, secret=new) is not None
    assert check(store, token, clock, secret=new) is None


@pytest.mark.parametrize("secret", ["short", "", []])
def test_weak_or_missing_secrets_are_refused(store, secret):
    with pytest.raises(ValueError):
        sessions.issue(store.get(1), secret)


def test_signing_out_of_all_devices_voids_every_older_token(store, clock):
    phone = sessions.issue(store.get(1), SECRET, clock=clock)
    laptop = sessions.issue(store.get(1), SECRET, clock=clock)
    store.end_sessions(1)
    assert check(store, phone, clock) is None and check(store, laptop, clock) is None
    assert check(store, sessions.issue(store.get(1), SECRET, clock=clock), clock) is not None


def test_a_server_side_session_keeps_both_versions(store):
    anna = store.get(1)
    versions = (anna.account_id, anna.password_version, anna.session_version)
    get = lambda i: store.get(i, fresh=True)   # noqa: E731
    assert sessions.current_account(*versions, get_account=get) == anna
    store.end_sessions(1)
    assert sessions.current_account(*versions, get_account=get) is None


def test_a_token_without_a_session_version_is_refused(store, clock):
    token = sessions.issue(store.get(1), SECRET, clock=clock)
    payload, _ = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    del claims["s"]
    stripped = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    assert check(store, f"{stripped}.{sessions._sign(SECRET.encode(), stripped)}", clock) is None


def test_max_age_reuses_a_recent_read_and_then_sees_the_deactivation(store, clock, monkeypatch):
    import internal_tool_kit.storage as storage_module

    now = [100.0]
    monkeypatch.setattr(storage_module.time, "monotonic", lambda: now[0])
    token = sessions.issue(store.get(1), SECRET, clock=clock)

    def verify():
        return sessions.verify(token, SECRET, get_account=lambda i: store.get(i, max_age=5), clock=clock)

    assert verify() is not None
    store.storage._tables["accounts"][1][4] = "0"      # deactivated by hand in the sheet: no cache invalidation
    now[0] += 4
    assert verify() is not None                        # within max_age the earlier read is reused
    now[0] += 2
    assert verify() is None                            # after max_age the account is read again
