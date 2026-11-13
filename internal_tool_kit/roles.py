"""Roles, permissions and who may manage whom.

Declare the roles once, with a level and the permissions each holds::

    ROLES = Roles([Role("member", 10, {"items.read", "items.write"}),
                   Role("manager", 20, {"items.read", "items.write", "accounts.manage"}),
                   Role("owner", 30, {"items.read", "items.write", "accounts.manage"}),
                   Role("system", 99, hidden=True)])

and check in the service layer, where the data changes, not only by hiding a button::

    ROLES.require(actor, "items.write")

The rules for managing accounts, each closing a hole that is easy to leave open:

- **Only below your own level.** An account can manage only accounts of a lower level, and can give only roles
  below its own. Otherwise one owner could deactivate another, or reset the other's password and sign in as them
  (with the audit log naming the victim). Accounts at the top level are managed outside the application
  (:func:`internal_tool_kit.accounts.reset_password`).
- **Not yourself.** No account manages itself through these checks; changing one's own password goes through
  :func:`internal_tool_kit.login.new_password_hash`, which asks for the current password (an unattended session
  cannot be used to change it).
- **Hidden roles** (system or maintenance accounts) are never listed for, and never manageable by, other roles.
- **Fail closed.** An account whose role is not declared (a typo in a spreadsheet) has no permission and a level
  below every role; an inactive account has no permission at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from internal_tool_kit.accounts import Account
from internal_tool_kit.i18n import KitError, register

register("roles.forbidden", "You do not have permission to do this.", "您沒有執行此操作的權限。")
register("roles.cannot_manage", "You can only manage accounts with a role below your own.",
         "只能管理權限比自己低的帳號。")
register("roles.not_self", "Your own account cannot be changed here; use 'Change password' instead.",
         "不能在這裡修改自己的帳號，請使用「修改密碼」。")
register("roles.cannot_grant", "You cannot give the role '{role}'.", "您不能授予「{role}」權限。")
register("roles.unknown", "The role '{role}' does not exist.", "沒有「{role}」這個權限角色。")

NO_LEVEL = -(10 ** 9)


@dataclass(frozen=True)
class Role:
    name: str
    level: int
    permissions: frozenset[str] = field(default_factory=frozenset)
    hidden: bool = False

    def __init__(self, name: str, level: int, permissions: Iterable[str] = (), hidden: bool = False):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "permissions", frozenset(permissions))
        object.__setattr__(self, "hidden", hidden)


class Roles:
    def __init__(self, roles: Iterable[Role]):
        self._roles = {r.name: r for r in roles}
        if not self._roles:
            raise ValueError("declare at least one role")

    def get(self, name: str) -> Role | None:
        return self._roles.get(name)

    def names(self) -> list[str]:
        return [r.name for r in sorted(self._roles.values(), key=lambda r: r.level)]

    def level(self, account: Account) -> int:
        role = self._roles.get(account.role)
        return role.level if role else NO_LEVEL

    def is_hidden(self, account: Account) -> bool:
        role = self._roles.get(account.role)
        return bool(role and role.hidden)

    def can(self, account: Account | None, permission: str) -> bool:
        if account is None or not account.active:
            return False
        role = self._roles.get(account.role)
        return bool(role and permission in role.permissions)

    def require(self, account: Account | None, permission: str) -> None:
        if not self.can(account, permission):
            raise KitError("roles.forbidden")

    def can_manage(self, actor: Account | None, target: Account) -> bool:
        if actor is None or not actor.active or actor.account_id == target.account_id:
            return False
        if self.is_hidden(target) and not self.is_hidden(actor):
            return False
        return self.level(actor) > self.level(target) and self.level(actor) != NO_LEVEL

    def require_manage(self, actor: Account | None, target: Account) -> None:
        if actor is not None and actor.account_id == target.account_id:
            raise KitError("roles.not_self")
        if not self.can_manage(actor, target):
            raise KitError("roles.cannot_manage")

    def assignable(self, actor: Account | None) -> list[str]:
        """The roles ``actor`` may give: below its own level, never hidden ones."""
        if actor is None or not actor.active or self.level(actor) == NO_LEVEL:
            return []
        return [r.name for r in sorted(self._roles.values(), key=lambda r: r.level)
                if r.level < self.level(actor) and not r.hidden]

    def require_assignable(self, actor: Account | None, role: str) -> None:
        if role not in self._roles:
            raise KitError("roles.unknown", role=role)
        if role not in self.assignable(actor):
            raise KitError("roles.cannot_grant", role=role)

    def visible(self, accounts: Iterable[Account], actor: Account | None) -> list[Account]:
        """``accounts`` without the hidden ones, unless ``actor`` itself has a hidden role."""
        if actor is not None and self.is_hidden(actor):
            return list(accounts)
        return [a for a in accounts if not self.is_hidden(a)]
