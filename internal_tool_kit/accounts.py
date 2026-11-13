"""User accounts kept in a storage table.

The table needs the columns in :data:`COLUMNS`. Two counters end sessions (see :mod:`internal_tool_kit.sessions`):
``password_version`` grows with every password change or reset, ``session_version`` with every "sign out of all
devices" (:meth:`AccountStore.end_sessions`). Session tokens carry both, so either change voids every token issued
before it. Usernames are compared after trimming and lower-casing, must be unique, and change only through
:meth:`AccountStore.rename`.

:func:`reset_password` is the break-glass path for an account nobody in the application may reset (for example
the highest role, see :mod:`internal_tool_kit.roles`): whoever holds the storage credentials calls it, it records
the reset in the audit log before making it, hands back a temporary password for a safe channel, and the person
must change it at the next login.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from internal_tool_kit import passwords
from internal_tool_kit.i18n import KitError, register
from internal_tool_kit.storage import Storage

COLUMNS = ["account_id", "username", "display_name", "role", "active", "password_hash", "password_version",
           "session_version", "must_change_password", "created_at"]

register("accounts.username_taken", "The username '{username}' is already in use.", "帳號名稱「{username}」已被使用。")
register("accounts.username_invalid", "A username needs 3 to 64 characters: letters, digits, '.', '_' or '-'.",
         "帳號名稱須為 3 到 64 個字元，只能使用英文字母、數字、「.」「_」「-」。")
register("accounts.not_found", "There is no account '{username}'.", "找不到帳號「{username}」。")

_TRUE = {"1", "true", "yes", "y"}
_INTEGER = re.compile(r"-?[0-9]+")
_PROTECTED = {"account_id": "an account keeps its id", "username": "use rename()"}


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def check_username(username: str) -> str:
    """The normalized username, or ``KitError("accounts.username_invalid")``."""
    name = normalize_username(username)
    if not (3 <= len(name) <= 64 and all(c.isascii() and (c.isalnum() or c in "._-") for c in name)):
        raise KitError("accounts.username_invalid")
    return name


def _flag(text: str) -> bool:
    return str(text).strip().lower() in _TRUE


def _version(text: str) -> int:
    """A version counter; a cell that is not an integer reads as 0, which matches no token (the session ends)
    without making the whole table unreadable."""
    text = str(text).strip()
    return int(text) if _INTEGER.fullmatch(text) else 0


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
    session_version: int = 1

    @classmethod
    def from_row(cls, row: dict[str, str]) -> Account:
        return cls(account_id=row["account_id"], username=row["username"], display_name=row["display_name"],
                   role=row["role"], active=_flag(row["active"]), password_hash=row["password_hash"],
                   password_version=_version(row["password_version"]),
                   must_change_password=_flag(row["must_change_password"]),
                   session_version=_version(row["session_version"]))


class AccountStore:
    def __init__(self, storage: Storage, table: str = "accounts"):
        self.storage, self.table = storage, table

    def _rows(self, fresh: bool = False, max_age: float | None = None) -> list[dict[str, str]]:
        return self.storage.read(self.table, fresh=fresh, max_age=max_age, require=COLUMNS)

    def all(self, *, fresh: bool = False, max_age: float | None = None) -> list[Account]:
        return [Account.from_row(r) for r in self._rows(fresh, max_age)]

    def get(self, account_id, *, fresh: bool = False, max_age: float | None = None) -> Account | None:
        """The account with this id (one read of the table, or none within ``max_age`` seconds of the last)."""
        account_id = str(account_id)
        return next((Account.from_row(r) for r in self._rows(fresh, max_age) if r["account_id"] == account_id), None)

    def find_by_username(self, username: str, *, fresh: bool = False,
                         max_age: float | None = None) -> Account | None:
        name = normalize_username(username)
        return next((Account.from_row(r) for r in self._rows(fresh, max_age)
                     if normalize_username(r["username"]) == name), None)

    def create(self, username: str, display_name: str, role: str, password_hash: str, *,
               must_change_password: bool = True) -> Account:
        """A new active account. The username check and the insert happen under one lock."""
        name = check_username(username)
        with self.storage.lock:
            if self.find_by_username(name, fresh=True):
                raise KitError("accounts.username_taken", username=name)
            new_id = self.storage.insert(self.table, {
                "username": name, "display_name": display_name, "role": role, "active": "1",
                "password_hash": password_hash, "password_version": "1", "session_version": "1",
                "must_change_password": "1" if must_change_password else "0",
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, "account_id")
        return self.get(new_id, fresh=True)

    def update(self, account_id, /, **values) -> Account:
        """Change columns of one account; booleans are stored as 1 / 0. The id never changes, and the username
        only through :meth:`rename`, which checks it."""
        for column, why in _PROTECTED.items():
            if column in values:
                raise ValueError(f"AccountStore.update cannot change {column}: {why}")
        values = {k: ("1" if v else "0") if isinstance(v, bool) else v for k, v in values.items()}
        self.storage.update(self.table, "account_id", str(account_id), values)
        return self.get(account_id, fresh=True)

    def rename(self, account_id, username: str) -> Account:
        """Give the account a new username, checked like a new one. Sessions name the account by id, so they
        continue; the old name becomes free for someone else."""
        name = check_username(username)
        with self.storage.lock:
            other = self.find_by_username(name, fresh=True)
            if other is not None and other.account_id != str(account_id):
                raise KitError("accounts.username_taken", username=name)
            self.storage.update(self.table, "account_id", str(account_id), {"username": name})
        return self.get(account_id, fresh=True)

    def _increase(self, account_id, column: str, **values) -> Account:
        with self.storage.lock:
            current = self.get(account_id, fresh=True)
            if current is None:
                raise KitError("accounts.not_found", username=str(account_id))
            return self.update(account_id, **{column: str(getattr(current, column) + 1)}, **values)

    def set_password(self, account_id, password_hash: str, *, must_change_password: bool) -> Account:
        """Store a new hash and increase the password version, which ends every session of the account."""
        return self._increase(account_id, "password_version", password_hash=password_hash,
                              must_change_password=must_change_password)

    def end_sessions(self, account_id) -> Account:
        """Sign the account out of every device: increase the session version, which voids every token issued
        before. The caller signs the current device in again if it should stay (``sessions.issue``)."""
        return self._increase(account_id, "session_version")


def reset_password(store: AccountStore, username: str, *, audit, reason: str, enable: bool = False, hasher=None,
                   throttle=None) -> tuple[Account, str]:
    """Break-glass reset by whoever holds the storage credentials: a new temporary password (returned once, to be
    handed over in person or by another safe channel), which must be changed at the next login. Every session of
    the account ends; ``enable`` also reactivates it, and ``throttle`` (a :class:`login.LoginThrottle`) lifts a
    temporary lockout.

    ``audit`` (an :class:`audit.AuditLog`) records the reset before it is made, with no actor and ``reason`` (who
    reset it and why); the reset is refused if the log cannot be written. Pass ``audit=None`` only where no log
    exists at all."""
    if audit is not None and not reason.strip():
        raise ValueError("say who reset the password and why (reason)")
    account = store.find_by_username(username, fresh=True)
    if account is None:
        raise KitError("accounts.not_found", username=normalize_username(username))
    temp = passwords.temporary_password()
    detail = f"break-glass reset{' and re-enable' if enable and not account.active else ''}: {reason.strip()}"

    def change() -> Account:
        changed = store.set_password(account.account_id, passwords.hash_password(temp, hasher),
                                     must_change_password=True)
        if enable and not changed.active:
            changed = store.update(changed.account_id, active=True)
        return changed

    if audit is None:
        account = change()
    else:
        with audit.change(None, "account.password_reset", store.table, account.account_id, detail):
            account = change()
    if throttle is not None:
        throttle.unlock(account.username)
    return account, temp
