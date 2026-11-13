"""User accounts kept in a storage table.

The table needs the columns in :data:`COLUMNS`. ``password_version`` counts password changes: session tokens
carry it (see :mod:`internal_tool_kit.sessions`), so a password change or reset ends every other session at once.
Usernames are compared after trimming and lower-casing, and must be unique.

:func:`reset_password` is the break-glass path for an account nobody in the application may reset (for example
the highest role, see :mod:`internal_tool_kit.roles`): whoever holds the storage credentials calls it, hands the
temporary password to the person by a safe channel, and the person must change it at the next login.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from internal_tool_kit import passwords
from internal_tool_kit.i18n import KitError, register
from internal_tool_kit.storage import Storage

COLUMNS = ["account_id", "username", "display_name", "role", "active", "password_hash", "password_version",
           "must_change_password", "created_at"]

register("accounts.username_taken", "The username '{username}' is already in use.", "帳號名稱「{username}」已被使用。")
register("accounts.username_invalid", "A username needs 3 to 64 characters: letters, digits, '.', '_' or '-'.",
         "帳號名稱須為 3 到 64 個字元，只能使用英文字母、數字、「.」「_」「-」。")
register("accounts.not_found", "There is no account '{username}'.", "找不到帳號「{username}」。")

_TRUE = {"1", "true", "yes", "y"}


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _flag(text: str) -> bool:
    return str(text).strip().lower() in _TRUE


@dataclass(frozen=True)
class Account:
    account_id: str
    username: str
    display_name: str
    role: str
    active: bool
    password_hash: str
    password_version: int
    must_change_password: bool

    @classmethod
    def from_row(cls, row: dict[str, str]) -> Account:
        return cls(account_id=row["account_id"], username=row["username"], display_name=row["display_name"],
                   role=row["role"], active=_flag(row["active"]), password_hash=row["password_hash"],
                   password_version=int(row["password_version"] or 0),
                   must_change_password=_flag(row["must_change_password"]))


class AccountStore:
    def __init__(self, storage: Storage, table: str = "accounts"):
        self.storage, self.table = storage, table

    def _rows(self, fresh: bool = False) -> list[dict[str, str]]:
        self.storage.require_columns(self.table, COLUMNS)
        return self.storage.read(self.table, fresh=fresh)

    def all(self, *, fresh: bool = False) -> list[Account]:
        return [Account.from_row(r) for r in self._rows(fresh)]

    def get(self, account_id, *, fresh: bool = False) -> Account | None:
        account_id = str(account_id)
        return next((Account.from_row(r) for r in self._rows(fresh) if r["account_id"] == account_id), None)

    def find_by_username(self, username: str, *, fresh: bool = False) -> Account | None:
        name = normalize_username(username)
        return next((Account.from_row(r) for r in self._rows(fresh) if normalize_username(r["username"]) == name),
                    None)

    def create(self, username: str, display_name: str, role: str, password_hash: str, *,
               must_change_password: bool = True) -> Account:
        """A new active account. The username check and the insert happen under one lock."""
        name = normalize_username(username)
        if not (3 <= len(name) <= 64 and all(c.isascii() and (c.isalnum() or c in "._-") for c in name)):
            raise KitError("accounts.username_invalid")
        with self.storage.lock:
            if self.find_by_username(name, fresh=True):
                raise KitError("accounts.username_taken", username=name)
            new_id = self.storage.insert(self.table, {
                "username": name, "display_name": display_name, "role": role, "active": "1",
                "password_hash": password_hash, "password_version": "1",
                "must_change_password": "1" if must_change_password else "0",
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, "account_id")
        return self.get(new_id, fresh=True)

    def update(self, account_id, **values) -> Account:
        """Change columns of one account; booleans are stored as 1 / 0."""
        values = {k: ("1" if v else "0") if isinstance(v, bool) else v for k, v in values.items()}
        self.storage.update(self.table, "account_id", str(account_id), values)
        return self.get(account_id, fresh=True)

    def set_password(self, account_id, password_hash: str, *, must_change_password: bool) -> Account:
        """Store a new hash and increase the password version, which ends every session of the account."""
        with self.storage.lock:
            current = self.get(account_id, fresh=True)
            if current is None:
                raise KitError("accounts.not_found", username=str(account_id))
            return self.update(account_id, password_hash=password_hash,
                               password_version=str(current.password_version + 1),
                               must_change_password=must_change_password)


def reset_password(store: AccountStore, username: str, *, enable: bool = False, hasher=None,
                   throttle=None) -> tuple[Account, str]:
    """Break-glass reset by whoever holds the storage credentials: a new temporary password (returned once, to be
    handed over in person or by another safe channel), which must be changed at the next login. Every session of
    the account ends; ``enable`` also reactivates it, and ``throttle`` (a :class:`login.LoginThrottle`) lifts a
    temporary lockout. Record the reset in the audit log yourself (``audit.record``), naming who did it and why."""
    account = store.find_by_username(username, fresh=True)
    if account is None:
        raise KitError("accounts.not_found", username=normalize_username(username))
    temp = passwords.temporary_password()
    account = store.set_password(account.account_id, passwords.hash_password(temp, hasher), must_change_password=True)
    if enable and not account.active:
        account = store.update(account.account_id, active=True)
    if throttle is not None:
        throttle.unlock(account.username)
    return account, temp
