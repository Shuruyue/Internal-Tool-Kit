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
    assert sessions.current_account("1", 1, get_account=lambda i: store.get(i, fresh=True)) is None


def test_a_renamed_account_keeps_its_token_and_the_old_name_gives_nothing(store, clock):
    token = sessions.issue(store.get(1), SECRET, clock=clock)
    store.update(1, username="anna.old")
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
