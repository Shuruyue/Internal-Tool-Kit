import pytest

from internal_tool_kit import accounts, login, passwords
from internal_tool_kit.accounts import AccountStore
from internal_tool_kit.i18n import KitError
from internal_tool_kit.login import LoginThrottle, authenticate
from internal_tool_kit.passwords import BcryptHasher
from internal_tool_kit.storage import MemoryStorage


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def store():
    s = AccountStore(MemoryStorage({"accounts": accounts.COLUMNS}))
    s.create("anna", "Anna", "member", passwords.hash_password("river7stone"), must_change_password=False)
    s.create("ben", "Ben", "member", passwords.hash_password("ocean4wind"))
    return s


@pytest.fixture
def clock():
    return Clock()


def attempt(store, throttle, username, password):
    return authenticate(username, password, find_account=store.find_by_username, throttle=throttle)


def test_correct_login_returns_the_account_and_whether_it_must_change_its_password(store, clock):
    t = LoginThrottle(clock=clock)
    assert attempt(store, t, " Anna ", "river7stone").account.username == "anna"
    assert attempt(store, t, "ben", "ocean4wind").must_change_password


def test_every_failure_gives_the_same_answer(store, clock):
    store.update(store.find_by_username("ben").account_id, active=False)
    t = LoginThrottle(attempts=100, clock=clock)
    answers = set()
    for user, pw in [("anna", "wrong1pass"), ("nobody", "river7stone"), ("ben", "ocean4wind"), ("", "")]:
        with pytest.raises(KitError) as exc:
            attempt(store, t, user, pw)
        answers.add((exc.value.key, exc.value.text("en"), exc.value.text("zh-TW")))
    assert answers == {("login.invalid", "Incorrect username or password.", "帳號或密碼錯誤。")}


def test_unknown_usernames_are_hashed_too(store, clock, monkeypatch):
    calls = []
    real = passwords.verify_password
    monkeypatch.setattr(passwords, "verify_password", lambda *a, **k: calls.append(1) or real(*a, **k))
    with pytest.raises(KitError):
        attempt(store, LoginThrottle(clock=clock), "nobody", "x")
    assert calls == [1]


def test_sign_in_pauses_after_repeated_failures_and_resumes(store, clock):
    t = LoginThrottle(attempts=3, minutes=15, clock=clock)
    for _ in range(3):
        with pytest.raises(KitError):
            attempt(store, t, "anna", "wrong1pass")
    assert t.is_paused("ANNA")
    with pytest.raises(KitError) as exc:                 # even the right password is refused, with the same answer
        attempt(store, t, "anna", "river7stone")
    assert exc.value.key == "login.invalid"
    clock.now += 15 * 60 - 1
    assert t.is_paused("anna")
    clock.now += 2
    assert attempt(store, t, "anna", "river7stone").account.username == "anna"
    assert t.store == {}


def test_unknown_usernames_are_counted_and_an_administrator_can_lift_a_pause(store, clock):
    t = LoginThrottle(attempts=2, clock=clock)
    for _ in range(2):
        with pytest.raises(KitError):
            attempt(store, t, "ghost", "x")
    assert t.is_paused("ghost")
    for _ in range(2):
        with pytest.raises(KitError):
            attempt(store, t, "anna", "wrong1pass")
    t.unlock("anna")
    assert attempt(store, t, "anna", "river7stone")


def test_a_success_resets_the_count(store, clock):
    t = LoginThrottle(attempts=3, clock=clock)
    for _ in range(2):
        with pytest.raises(KitError):
            attempt(store, t, "anna", "wrong1pass")
    attempt(store, t, "anna", "river7stone")
    for _ in range(2):
        with pytest.raises(KitError):
            attempt(store, t, "anna", "wrong1pass")
    assert not t.is_paused("anna")


def test_the_rule_text_is_fixed_and_bilingual(clock):
    rule = LoginThrottle(attempts=5, minutes=15, clock=clock).rule()
    assert rule.text("en") == "After 5 failed attempts in a row, sign-in is paused for 15 minutes."
    assert rule.text("zh-TW") == "連續輸入錯誤 5 次後，將暫停登入 15 分鐘。"


def test_old_hashes_are_upgraded_on_login(store, clock):
    anna = store.find_by_username("anna")
    store.update(anna.account_id, password_hash=BcryptHasher(rounds=4).hash("river7stone"))
    result = attempt(store, LoginThrottle(clock=clock), "anna", "river7stone")
    assert result.new_hash.startswith("scrypt$")
    store.update(anna.account_id, password_hash=result.new_hash)
    assert attempt(store, LoginThrottle(clock=clock), "anna", "river7stone").new_hash is None


def test_password_change_checks_the_current_password_and_the_policy(store):
    anna = store.find_by_username("anna")
    for current, new, key in [("wrong1pass", "new9password", "password.current_wrong"),
                              ("river7stone", "river7stone", "password.same_as_old"),
                              ("river7stone", "short1", "password.too_short"),
                              ("river7stone", "my-anna-2026", "password.contains_username")]:
        with pytest.raises(KitError) as exc:
            login.new_password_hash(anna, current, new)
        assert exc.value.key == key
    new_hash = login.new_password_hash(anna, "river7stone", "new9password")
    changed = store.set_password(anna.account_id, new_hash, must_change_password=False)
    assert changed.password_version == anna.password_version + 1
    assert passwords.verify_password("new9password", changed.password_hash)
