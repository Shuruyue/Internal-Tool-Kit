"""Account management inside the application: create, change role, deactivate or re-enable, reset a password.

Every operation checks, in this order: the actor holds the management permission; the target is below the actor
(:meth:`Roles.require_manage`); a new role is one the actor may give; the audit log is writable. Only then does it
change the account and record the change. Deactivating, demoting and resetting take effect on the target's open
sessions at once, because :func:`sessions.current_account` reads the account on every request (a reset also
increases the password version). Re-enabling and resetting lift a login pause.

Temporary passwords are returned once and never stored or logged; hand them over in person or by another safe
channel. The account must change it at the next login.
"""

from __future__ import annotations

from internal_tool_kit import passwords
from internal_tool_kit.accounts import Account, AccountStore
from internal_tool_kit.audit import AuditLog
from internal_tool_kit.i18n import KitError
from internal_tool_kit.roles import Roles

CREATE, ROLE, DEACTIVATE, ACTIVATE, PASSWORD_RESET = (
    "account.create", "account.role", "account.deactivate", "account.activate", "account.password_reset")


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
        self.roles.require(actor, self.permission)
        self.roles.require_assignable(actor, role)
        self.audit.ensure_ready()
        temp = passwords.temporary_password()
        account = self.store.create(username, display_name, role, passwords.hash_password(temp, self.hasher),
                                    must_change_password=True)
        self.audit.record(actor.account_id, CREATE, self.store.table, account.account_id,
                          f"{account.username} as {role}")
        return account, temp

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
