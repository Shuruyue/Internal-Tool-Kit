"""Account management with an audit log, played through with one account per kind of person."""

import pytest

from internal_tool_kit import accounts, audit, passwords, sessions
from internal_tool_kit.accounts import AccountStore
from internal_tool_kit.admin import AccountAdmin
from internal_tool_kit.audit import AuditLog
from internal_tool_kit.i18n import KitError
from internal_tool_kit.login import LoginThrottle
from internal_tool_kit.roles import Role, Roles
from internal_tool_kit.storage import MemoryStorage

ROLES = Roles([Role("member", 10, {"items.read"}),
               Role("manager", 20, {"items.read", "accounts.manage"}),
               Role("owner", 30, {"items.read", "accounts.manage"}),
               Role("system", 99, {"accounts.manage"}, hidden=True)])
SECRET = sessions.new_secret()


@pytest.fixture
def world():
    storage = MemoryStorage({"accounts": accounts.COLUMNS, "audit_logs": audit.COLUMNS})
    store = AccountStore(storage)
    people = {}
    for name, role in [("owner", "owner"), ("owner2", "owner"), ("mgr", "manager"), ("mgr2", "manager"),
                       ("member", "member"), ("system", "system")]:
        people[name] = store.create(name, name.title(), role, passwords.hash_password("river7stone"))
    throttle = LoginThrottle(attempts=1)
    log = AuditLog(storage, clock=lambda: "2026-01-01T00:00:00+00:00")
    return {"store": store, "log": log, "throttle": throttle, "people": people, "storage": storage,
            "admin": AccountAdmin(store, ROLES, log, throttle=throttle)}


def who(world, name):
    return world["store"].get(world["people"][name].account_id, fresh=True)


def expect(key, fn, *args):
    with pytest.raises(KitError) as exc:
        fn(*args)
    assert exc.value.key == key


def test_who_may_create_which_role(world):
    admin = world["admin"]
    account, temp = admin.create(who(world, "owner"), "newmgr", "New Manager", "manager")
    assert account.must_change_password and passwords.verify_password(temp, account.password_hash)
    admin.create(who(world, "mgr"), "newmember", "New Member", "member")
    expect("roles.cannot_grant", admin.create, who(world, "mgr"), "x1", "X", "manager")
    expect("roles.cannot_grant", admin.create, who(world, "owner"), "x2", "X", "owner")
    expect("roles.cannot_grant", admin.create, who(world, "owner"), "x3", "X", "system")
    expect("roles.forbidden", admin.create, who(world, "member"), "x4", "X", "member")


@pytest.mark.parametrize("actor, target, key", [
    ("mgr", "mgr2", "roles.cannot_manage"),       # same level
    ("owner", "owner2", "roles.cannot_manage"),   # one owner cannot take over another
    ("mgr", "owner", "roles.cannot_manage"),      # upwards
    ("owner", "system", "roles.cannot_manage"),   # hidden account
    ("owner", "owner", "roles.not_self"),          # own account: use "change password"
    ("member", "member", "roles.forbidden"),
])
def test_refused_management_changes_nothing(world, actor, target, key):
    admin, before = world["admin"], who(world, target)
    expect(key, admin.set_active, who(world, actor), before.account_id, False)
    expect(key, admin.reset_password, who(world, actor), before.account_id)
    assert who(world, target) == before
    assert world["log"].entries() == []


def test_deactivation_and_reset_end_open_sessions_at_once(world):
    admin, owner = world["admin"], who(world, "owner")
    get = lambda i: world["store"].get(i, fresh=True)   # noqa: E731
    token = sessions.issue(who(world, "member"), SECRET)
    assert sessions.verify(token, SECRET, get_account=get)
    admin.set_active(owner, who(world, "member").account_id, False)
    assert sessions.verify(token, SECRET, get_account=get) is None
    admin.set_active(owner, who(world, "member").account_id, True)
    assert sessions.verify(token, SECRET, get_account=get)
    _, temp = admin.reset_password(owner, who(world, "member").account_id)
    assert sessions.verify(token, SECRET, get_account=get) is None
    assert who(world, "member").must_change_password


def test_re_enabling_and_resetting_lift_a_login_pause(world):
    admin, owner, throttle = world["admin"], who(world, "owner"), world["throttle"]
    throttle.failure("member")
    assert throttle.is_paused("member")
    admin.set_active(owner, who(world, "member").account_id, True)
    assert not throttle.is_paused("member")
    throttle.failure("mgr")
    admin.reset_password(owner, who(world, "mgr").account_id)
    assert not throttle.is_paused("mgr")


def test_promotion_and_demotion_follow_the_same_limits(world):
    admin = world["admin"]
    assert admin.set_role(who(world, "owner"), who(world, "member").account_id, "manager").role == "manager"
    expect("roles.cannot_grant", admin.set_role, who(world, "owner"), who(world, "mgr").account_id, "owner")
    expect("roles.cannot_manage", admin.set_role, who(world, "mgr"), who(world, "member").account_id, "member")


def test_every_change_is_logged_with_its_actor_and_no_password(world):
    admin, owner = world["admin"], who(world, "owner")
    member_id = who(world, "member").account_id
    _, temp1 = admin.create(owner, "newbie", "Newbie", "member")
    admin.set_role(owner, member_id, "manager")
    admin.set_active(owner, member_id, False)
    _, temp2 = admin.reset_password(owner, member_id)
    entries = world["log"].entries()
    assert [e["action"] for e in entries] == ["account.password_reset", "account.deactivate", "account.role",
                                             "account.create"]
    assert {e["actor_id"] for e in entries} == {owner.account_id}
    assert entries[2]["detail"] == "member -> manager"
    raw = str(world["storage"].read("audit_logs"))
    assert temp1 not in raw and temp2 not in raw
    assert [e["action"] for e in world["log"].entries(target_id=member_id)] == [
        "account.password_reset", "account.deactivate", "account.role"]


def test_a_missing_audit_table_blocks_the_change(world):
    storage = MemoryStorage({"accounts": accounts.COLUMNS})
    store = AccountStore(storage)
    owner = store.create("owner", "Owner", "owner", "")
    member = store.create("member", "Member", "member", "")
    admin = AccountAdmin(store, ROLES, AuditLog(storage))
    expect("storage.missing_table", admin.set_active, owner, member.account_id, False)
    expect("storage.missing_table", admin.reset_password, owner, member.account_id)
    expect("storage.missing_table", admin.create, owner, "another", "Another", "member")
    assert store.get(member.account_id) == member and len(store.all()) == 2


def test_the_change_is_recorded_first_and_a_failure_is_recorded_too(world):
    log = world["log"]
    with pytest.raises(RuntimeError), log.change("1", "item.close", "items", "9", "closing"):
        assert [e["action"] for e in log.entries()] == ["item.close"]     # written before the block runs
        raise RuntimeError("secret detail 0912345678")
    failed, intent = log.entries()
    assert (intent["action"], intent["detail"]) == ("item.close", "closing")
    assert (failed["action"], failed["detail"]) == ("item.close.failed", "failed: RuntimeError")  # no message text
    with pytest.raises(KitError), log.change("1", "item.close", "items", "9"):
        raise KitError("roles.forbidden")
    assert log.entries()[0]["detail"] == "failed: roles.forbidden"


def test_record_returns_a_unique_id_led_by_the_time(world):
    log = world["log"]
    ids = [log.record(None, "account.password_reset", "accounts", "5", "break-glass by the data owner")
           for _ in range(50)]
    times = [i.split("-")[0] for i in ids]
    assert len(set(ids)) == 50 and times == sorted(times)
    assert log.entries(actor_id="")[0]["log_id"] == ids[-1]


def test_admin_create_rename_and_sign_out_are_logged(world):
    admin, owner = world["admin"], who(world, "owner")
    account, _ = admin.create(owner, " NewBie ", "Newbie", "member")
    admin.rename(owner, account.account_id, "new.bie")
    renamed = admin.end_sessions(owner, account.account_id)
    assert (renamed.username, renamed.session_version) == ("new.bie", 2)
    actions = [(e["action"], e["target_id"], e["detail"]) for e in world["log"].entries()]
    assert actions == [("account.sessions_end", account.account_id, ""),
                       ("account.rename", account.account_id, "newbie -> new.bie"),
                       ("account.create", "", "newbie as member")]
    expect("roles.cannot_manage", admin.rename, who(world, "mgr"), who(world, "mgr2").account_id, "x.y.z")
    expect("roles.cannot_manage", admin.end_sessions, who(world, "mgr"), who(world, "owner").account_id)
    expect("accounts.username_taken", admin.rename, owner, account.account_id, "member")
    assert who(world, "member").username == "member"


def test_a_refused_creation_leaves_a_failed_entry_and_no_account(world):
    admin, owner = world["admin"], who(world, "owner")
    expect("accounts.username_taken", admin.create, owner, "member", "Second Member", "member")
    assert [e["action"] for e in world["log"].entries()] == ["account.create.failed", "account.create"]
    assert len(world["store"].all()) == 6
