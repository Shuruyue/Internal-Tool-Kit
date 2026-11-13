"""Checking a login, pausing sign-in after repeated failures, and changing a password.

Rules, each against a known way such systems get attacked:

- **One answer for every failure.** A wrong password, an unknown username, a deactivated account and a paused
  sign-in all raise ``login.invalid`` ("Incorrect username or password"), so the answer does not tell whether an
  account exists. Show the fixed rule text (:meth:`LoginThrottle.rule`) next to the form instead of counting down.
  An unknown username is hashed against a dummy hash, so it takes as long as a real one.
- **Pause, not lock.** After ``attempts`` failures in a row, sign-in for that username is refused for ``minutes``;
  unknown usernames are counted too. A permanent lock would let anyone who knows a username shut its owner out
  (all administrators at once, if their usernames are known). An administrator can lift the pause early.
- **The pause lives in the process** (a dict) unless you pass a shared ``store``; with several servers, pass one
  backed by a database or Redis.
"""

from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

from internal_tool_kit import passwords
from internal_tool_kit.accounts import Account, normalize_username
from internal_tool_kit.i18n import KitError, Message, register

register("login.invalid", "Incorrect username or password.", "帳號或密碼錯誤。")
register("login.rule", "After {attempts} failed attempts in a row, sign-in is paused for {minutes} minutes.",
         "連續輸入錯誤 {attempts} 次後，將暫停登入 {minutes} 分鐘。")

_dummy_hash: str | None = None


def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = passwords.hash_password("dummy password for timing")
    return _dummy_hash


class LoginThrottle:
    def __init__(self, attempts: int = 5, minutes: float = 15, *, store: MutableMapping | None = None,
                 clock: Callable[[], float] = time.time):
        self.attempts, self.minutes, self.clock = attempts, minutes, clock
        self.store = {} if store is None else store          # username -> (failures, paused until)

    def rule(self) -> Message:
        return Message("login.rule", {"attempts": self.attempts, "minutes": self.minutes})

    def _state(self, username: str) -> tuple[int, float]:
        failures, until = self.store.get(normalize_username(username), (0, 0.0))
        if until and self.clock() >= until:                   # the pause is over: start counting again
            return 0, 0.0
        return failures, until

    def is_paused(self, username: str) -> bool:
        return self._state(username)[1] > self.clock()

    def failure(self, username: str) -> None:
        failures, until = self._state(username)
        failures += 1
        if failures >= self.attempts:
            until = self.clock() + self.minutes * 60
        self.store[normalize_username(username)] = (failures, until)

    def success(self, username: str) -> None:
        self.store.pop(normalize_username(username), None)

    def unlock(self, username: str) -> None:
        """Lift a pause early (an administrator re-enabling an account, or a break-glass reset)."""
        self.store.pop(normalize_username(username), None)


@dataclass(frozen=True)
class LoginResult:
    account: Account
    must_change_password: bool
    new_hash: str | None = None       # set when the stored hash should be replaced (older format or parameters)


def authenticate(username: str, password: str, *, find_account: Callable[[str], Account | None],
                 throttle: LoginThrottle, hasher: passwords.Hasher | None = None) -> LoginResult:
    """The account for a correct username and password; otherwise ``KitError("login.invalid")``.

    ``find_account`` looks an account up by username (for example ``AccountStore.find_by_username``). When
    ``result.new_hash`` is set, store it (``AccountStore.update(id, password_hash=...)``); this upgrades old hashes
    as users log in."""
    name = normalize_username(username)
    if throttle.is_paused(name):
        passwords.verify_password(password, _dummy())
        raise KitError("login.invalid")
    account = find_account(name)
    ok = passwords.verify_password(password, account.password_hash if account else _dummy())
    if account is None or not ok or not account.active:
        throttle.failure(name)
        raise KitError("login.invalid")
    throttle.success(name)
    new_hash = passwords.hash_password(password, hasher) if passwords.needs_rehash(account.password_hash, hasher) \
        else None
    return LoginResult(account, account.must_change_password, new_hash)


def new_password_hash(account: Account, current: str, new: str, *, hasher: passwords.Hasher | None = None,
                      **policy) -> str:
    """Check a password change made by the account holder and return the hash to store
    (``AccountStore.set_password(id, hash, must_change_password=False)``, which also ends the other sessions).
    ``policy`` goes to :func:`passwords.check_policy`."""
    if not passwords.verify_password(current, account.password_hash):
        raise KitError("password.current_wrong")
    if new == current:
        raise KitError("password.same_as_old")
    passwords.check_policy(new, username=account.username, **policy)
    return passwords.hash_password(new, hasher)
