"""Account management inside the application: create, rename, change role, deactivate or re-enable, reset a
password, sign out of all devices.

Every operation checks, in this order: the actor holds the management permission; the target is below the actor
(:meth:`Roles.require_manage`); a new role is one the actor may give. Then it records the change in the audit log
(which refuses if the log cannot be written) and makes it (:meth:`AuditLog.change`). Deactivating, demoting,
resetting and signing out take effect on the target's open sessions at once, because
:func:`sessions.current_account` reads the account on every request (a reset increases the password version, a
sign-out the session version). Re-enabling and resetting lift a login pause.

Temporary passwords are returned once and never stored or logged; hand them over in person or by another safe
channel. The account must change it at the next login.
"""

from __future__ import annotations

from internal_tool_kit import passwords
from internal_tool_kit.accounts import Account, AccountStore, check_username
from internal_tool_kit.audit import AuditLog
from internal_tool_kit.i18n import KitError
from internal_tool_kit.roles import Roles

CREATE, RENAME, ROLE, DEACTIVATE, ACTIVATE, PASSWORD_RESET, SESSIONS_END = (
    "account.create", "account.rename", "account.role", "account.deactivate", "account.activate",
    "account.password_reset", "account.sessions_end")


class AccountAdmin:
    def __init__(self, store: AccountStore, roles: Roles, audit: AuditLog, *, throttle=None,
                 permission: str = "accounts.manage", hasher=None):
        self.store, self.roles, self.audit = store, roles, audit
        self.throttle, self.permission, self.hasher = throttle, permission, hasher

    def _target(self, actor: Account, target_id) -> Account:
        self.roles.require(actor, self.permission)
        target = self.store.get(target_id, fresh=True)
        if target is None:
            raise KitError("accounts.not_found", username=str(target_id))
        self.roles.require_manage(actor, target)
        return target

    def list(self, actor: Account) -> list[Account]:
        """The accounts ``actor`` may see (hidden roles left out)."""
        self.roles.require(actor, self.permission)
        return self.roles.visible(self.store.all(fresh=True), actor)

    def create(self, actor: Account, username: str, display_name: str, role: str) -> tuple[Account, str]:
        """A new account with a temporary password (returned once). The new id is not known before the account
        exists, so the entry names the username, which is unique."""
        self.roles.require(actor, self.permission)
        self.roles.require_assignable(actor, role)
        name = check_username(username)
        temp = passwords.temporary_password()
        with self.audit.change(actor.account_id, CREATE, self.store.table, "", f"{name} as {role}"):
            account = self.store.create(name, display_name, role, passwords.hash_password(temp, self.hasher),
                                        must_change_password=True)
        return account, temp

    def rename(self, actor: Account, target_id, username: str) -> Account:
        target = self._target(actor, target_id)
        name = check_username(username)
        with self.audit.change(actor.account_id, RENAME, self.store.table, target.account_id,
                               f"{target.username} -> {name}"):
            account = self.store.rename(target.account_id, name)
        return account

    def end_sessions(self, actor: Account, target_id) -> Account:
        """Sign the target out of every device (for example after a lost phone)."""
        target = self._target(actor, target_id)
        with self.audit.change(actor.account_id, SESSIONS_END, self.store.table, target.account_id):
            account = self.store.end_sessions(target.account_id)
        return account

    def set_role(self, actor: Account, target_id, role: str) -> Account:
        target = self._target(actor, target_id)
        self.roles.require_assignable(actor, role)
        with self.audit.change(actor.account_id, ROLE, self.store.table, target.account_id,
                               f"{target.role} -> {role}"):
            account = self.store.update(target.account_id, role=role)
        return account

    def set_active(self, actor: Account, target_id, active: bool) -> Account:
        target = self._target(actor, target_id)
        with self.audit.change(actor.account_id, ACTIVATE if active else DEACTIVATE, self.store.table,
                               target.account_id):
            account = self.store.update(target.account_id, active=active)
            if active and self.throttle is not None:
                self.throttle.unlock(account.username)
        return account

    def reset_password(self, actor: Account, target_id) -> tuple[Account, str]:
        target = self._target(actor, target_id)
        temp = passwords.temporary_password()
        with self.audit.change(actor.account_id, PASSWORD_RESET, self.store.table, target.account_id,
                               "temporary password issued; must be changed at the next login"):
            account = self.store.set_password(target.account_id, passwords.hash_password(temp, self.hasher),
                                              must_change_password=True)
            if self.throttle is not None:
                self.throttle.unlock(account.username)
        return account, temp
