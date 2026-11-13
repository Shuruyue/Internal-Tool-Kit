"""Who may do and manage what, checked with one account per kind of person."""

import pytest

from internal_tool_kit.accounts import Account
from internal_tool_kit.i18n import KitError
from internal_tool_kit.roles import Role, Roles

ROLES = Roles([Role("member", 10, {"items.read", "items.write"}),
               Role("manager", 20, {"items.read", "items.write", "accounts.manage"}),
               Role("owner", 30, {"items.read", "items.write", "accounts.manage"}),
               Role("system", 99, {"items.read"}, hidden=True)])


def acct(account_id, role, active=True):
    return Account(str(account_id), f"user{account_id}", f"User {account_id}", role, active, "", 1, False)


MEMBER, MANAGER, MANAGER2, OWNER, OWNER2 = acct(1, "member"), acct(2, "manager"), acct(3, "manager"), \
    acct(4, "owner"), acct(5, "owner")
SYSTEM, INACTIVE_OWNER, TYPO = acct(6, "system"), acct(7, "owner", active=False), acct(8, "Owner")


def test_permissions_follow_the_declared_roles():
    assert ROLES.can(MEMBER, "items.write") and not ROLES.can(MEMBER, "accounts.manage")
    assert ROLES.can(MANAGER, "accounts.manage")
    with pytest.raises(KitError) as exc:
        ROLES.require(MEMBER, "accounts.manage")
    assert exc.value.key == "roles.forbidden"


def test_unknown_roles_inactive_accounts_and_no_account_get_nothing():
    for who in (TYPO, INACTIVE_OWNER, None):
        assert not ROLES.can(who, "items.read")
        assert ROLES.assignable(who) == []
        assert not ROLES.can_manage(who, MEMBER)


@pytest.mark.parametrize("actor, target, allowed", [
    (OWNER, MANAGER, True), (OWNER, MEMBER, True), (MANAGER, MEMBER, True),
    (MANAGER, MANAGER2, False), (OWNER, OWNER2, False),          # same level: neither can take over the other
    (MANAGER, OWNER, False), (MEMBER, MANAGER, False),            # upwards
    (OWNER, SYSTEM, False), (MANAGER, SYSTEM, False),             # hidden accounts are not manageable
    (OWNER, TYPO, True),                                          # a broken role is below every role: fixable
    (SYSTEM, OWNER, True),
])
def test_accounts_can_be_managed_only_from_above(actor, target, allowed):
    assert ROLES.can_manage(actor, target) is allowed


def test_nobody_manages_their_own_account_here():
    for who in (MEMBER, MANAGER, OWNER, SYSTEM):
        assert not ROLES.can_manage(who, who)
        with pytest.raises(KitError) as exc:
            ROLES.require_manage(who, who)
        assert exc.value.key == "roles.not_self"
    with pytest.raises(KitError) as exc:
        ROLES.require_manage(MANAGER, MANAGER2)
    assert exc.value.key == "roles.cannot_manage"


def test_roles_can_be_given_only_below_your_own_and_never_hidden_ones():
    assert ROLES.assignable(OWNER) == ["member", "manager"]
    assert ROLES.assignable(MANAGER) == ["member"]
    assert ROLES.assignable(MEMBER) == []
    assert "system" not in ROLES.assignable(SYSTEM)
    for role, key in [("owner", "roles.cannot_grant"), ("system", "roles.cannot_grant"), ("admin", "roles.unknown")]:
        with pytest.raises(KitError) as exc:
            ROLES.require_assignable(OWNER, role)
        assert exc.value.key == key


def test_hidden_accounts_are_not_listed_except_for_hidden_roles():
    everyone = [MEMBER, MANAGER, OWNER, SYSTEM]
    assert SYSTEM not in ROLES.visible(everyone, OWNER)
    assert ROLES.visible(everyone, SYSTEM) == everyone
