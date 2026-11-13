import pytest

from internal_tool_kit import accounts, passwords
from internal_tool_kit.accounts import AccountStore, reset_password
from internal_tool_kit.i18n import KitError
from internal_tool_kit.login import LoginThrottle
from internal_tool_kit.storage import MemoryStorage


@pytest.fixture
def store():
    return AccountStore(MemoryStorage({"accounts": accounts.COLUMNS}))


def test_a_new_account_is_active_must_change_its_password_and_has_version_1(store):
    a = store.create(" Anna.Lee ", "Anna Lee", "member", passwords.hash_password("river7stone"))
    assert (a.account_id, a.username, a.active, a.must_change_password, a.password_version) == \
           ("1", "anna.lee", True, True, 1)
    assert store.get(1) == a == store.find_by_username("ANNA.LEE")


def test_usernames_are_unique_ignoring_case_and_spaces(store):
    store.create("anna", "Anna", "member", "")
    with pytest.raises(KitError) as exc:
        store.create(" ANNA ", "Other Anna", "member", "")
    assert exc.value.key == "accounts.username_taken"
    assert len(store.all()) == 1


@pytest.mark.parametrize("name", ["ab", "a" * 65, "anna lee", "安娜", "anna@x", ""])
def test_invalid_usernames_are_refused(store, name):
    with pytest.raises(KitError) as exc:
        store.create(name, "x", "member", "")
    assert exc.value.key == "accounts.username_invalid"


def test_a_table_without_the_required_columns_is_reported():
    store = AccountStore(MemoryStorage({"accounts": ["account_id", "username"]}))
    with pytest.raises(KitError) as exc:
        store.all()
    assert exc.value.key == "storage.missing_columns"


def test_setting_a_password_increases_the_version(store):
    a = store.create("anna", "Anna", "member", passwords.hash_password("river7stone"))
    b = store.set_password(a.account_id, passwords.hash_password("ocean4wind"), must_change_password=False)
    assert (b.password_version, b.must_change_password) == (2, False)


def test_break_glass_reset_gives_a_one_time_password_ends_sessions_and_can_reenable(store):
    a = store.create("head", "Head", "owner", passwords.hash_password("river7stone"), must_change_password=False)
    store.update(a.account_id, active=False)
    throttle = LoginThrottle(attempts=1)
    throttle.failure("head")
    account, temp = reset_password(store, "Head", enable=True, throttle=throttle)
    assert passwords.verify_password(temp, account.password_hash)
    assert not passwords.verify_password("river7stone", account.password_hash)
    assert (account.active, account.must_change_password, account.password_version) == (True, True, 2)
    assert not throttle.is_paused("head")
    with pytest.raises(KitError) as exc:
        reset_password(store, "nobody")
    assert exc.value.key == "accounts.not_found"


def test_break_glass_reset_leaves_a_deactivated_account_deactivated_unless_asked(store):
    a = store.create("anna", "Anna", "member", "")
    store.update(a.account_id, active=False)
    account, _ = reset_password(store, "anna")
    assert not account.active
