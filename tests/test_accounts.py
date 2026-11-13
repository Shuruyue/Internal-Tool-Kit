import pytest

from internal_tool_kit import accounts, audit, passwords
from internal_tool_kit.accounts import AccountStore, reset_password
from internal_tool_kit.audit import AuditLog
from internal_tool_kit.i18n import KitError
from internal_tool_kit.login import LoginThrottle
from internal_tool_kit.storage import MemoryStorage


@pytest.fixture
def store():
    return AccountStore(MemoryStorage({"accounts": accounts.COLUMNS, "audit_logs": audit.COLUMNS}))


@pytest.fixture
def log(store):
    return AuditLog(store.storage)


def test_a_new_account_is_active_must_change_its_password_and_has_version_1(store):
    a = store.create(" Anna.Lee ", "Anna Lee", "member", passwords.hash_password("river7stone"))
    assert (a.account_id, a.username, a.active, a.must_change_password) == ("1", "anna.lee", True, True)
    assert (a.password_version, a.session_version) == (1, 1)
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
    assert (b.password_version, b.must_change_password, b.session_version) == (2, False, 1)


def test_end_sessions_increases_only_the_session_version(store):
    a = store.create("anna", "Anna", "member", "")
    b = store.end_sessions(a.account_id)
    assert (b.session_version, b.password_version) == (2, 1)


def test_a_malformed_version_cell_ends_sessions_but_keeps_the_table_readable(store):
    a = store.create("anna", "Anna", "member", "")
    store.storage.update("accounts", "account_id", a.account_id, {"session_version": "two"})
    assert store.get(a.account_id).session_version == 0
    assert len(store.all()) == 1


def test_update_cannot_change_the_id_or_the_username(store):
    a = store.create("anna", "Anna", "member", "")
    for column in ("username", "account_id"):
        with pytest.raises(ValueError):
            store.update(a.account_id, **{column: "x"})
    assert store.get(a.account_id) == a


def test_rename_checks_the_new_name_like_a_new_account(store):
    a = store.create("anna", "Anna", "member", "")
    store.create("ben", "Ben", "member", "")
    assert store.rename(a.account_id, " Anna.Lee ").username == "anna.lee"
    assert store.rename(a.account_id, "ANNA.LEE").username == "anna.lee"     # its own name is not "taken"
    with pytest.raises(KitError) as exc:
        store.rename(a.account_id, "Ben")
    assert exc.value.key == "accounts.username_taken"
    with pytest.raises(KitError) as exc:
        store.rename(a.account_id, "a b")
    assert exc.value.key == "accounts.username_invalid"
    assert store.find_by_username("anna") is None


def test_break_glass_reset_gives_a_one_time_password_ends_sessions_and_can_reenable(store, log):
    a = store.create("head", "Head", "owner", passwords.hash_password("river7stone"), must_change_password=False)
    store.update(a.account_id, active=False)
    throttle = LoginThrottle(attempts=1)
    throttle.failure("head")
    account, temp = reset_password(store, "Head", audit=log, reason="by the data owner, forgotten password",
                                   enable=True, throttle=throttle)
    assert passwords.verify_password(temp, account.password_hash)
    assert not passwords.verify_password("river7stone", account.password_hash)
    assert (account.active, account.must_change_password, account.password_version) == (True, True, 2)
    assert not throttle.is_paused("head")
    [entry] = log.entries()
    assert (entry["actor_id"], entry["action"], entry["target_id"]) == ("", "account.password_reset", a.account_id)
    assert entry["detail"] == "break-glass reset and re-enable: by the data owner, forgotten password"
    assert temp not in str(store.storage.read("audit_logs"))
    with pytest.raises(KitError) as exc:
        reset_password(store, "nobody", audit=log, reason="test")
    assert exc.value.key == "accounts.not_found"


def test_break_glass_reset_leaves_a_deactivated_account_deactivated_unless_asked(store, log):
    a = store.create("anna", "Anna", "member", "")
    store.update(a.account_id, active=False)
    account, _ = reset_password(store, "anna", audit=log, reason="test")
    assert not account.active


def test_break_glass_reset_is_refused_without_a_log_or_a_reason():
    storage = MemoryStorage({"accounts": accounts.COLUMNS})          # no audit_logs table
    store = AccountStore(storage)
    a = store.create("head", "Head", "owner", "")
    with pytest.raises(KitError) as exc:
        reset_password(store, "head", audit=AuditLog(storage), reason="forgotten")
    assert exc.value.key == "storage.missing_table"
    assert store.get(a.account_id).password_version == 1           # nothing changed
    with pytest.raises(ValueError):
        reset_password(store, "head", audit=AuditLog(storage), reason=" ")
    account, _ = reset_password(store, "head", audit=None, reason="")  # explicitly without a log
    assert account.password_version == 2
