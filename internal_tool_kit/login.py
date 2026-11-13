"""Checking a login, pausing sign-in after repeated failures, and changing a password.

Rules, each against a known way such systems get attacked:

- **One answer for every failure.** A wrong password, an unknown username, a deactivated account and a paused
  sign-in all raise ``login.invalid`` ("Incorrect username or password"), so the answer does not tell whether an
  account exists. Show the fixed rule text (:meth:`LoginThrottle.rule`) next to the form instead of counting down.
  An unknown username is hashed against a dummy hash, so it takes as long as a real one.
- **Pause, not lock.** After ``attempts`` failures, sign-in for that username is refused for ``minutes``; unknown
  usernames are counted too. Failures are forgotten ``minutes`` after the last one. A permanent lock would let
  anyone who knows a username shut its owner out (all administrators at once, if their usernames are known). An
  administrator can lift the pause early.
- **Many usernames from one place.** Trying one common password on many usernames ("password spraying") fails
  only once per username. Pass ``client`` (for example the client's IP address, as the web framework reports it)
  and the client is paused too after ``client_attempts`` failures, whichever usernames it tried. A success does
  not clear the client's count, so an attacker cannot reset it by signing in to an account of their own.
- **Changing a password asks for the current one**, and wrong guesses are paused the same way when a throttle is
  given, so an unattended session cannot be used to find the password by trying.
- **The counts live in the process** (a dict, cleaned of expired entries as it grows) unless you pass a shared
  ``store``; with several servers, pass one backed by a database or Redis (string keys, tuple values).
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
register("login.change_paused", "Too many wrong current passwords. Try again in {minutes} minutes.",
         "目前密碼輸入錯誤次數過多，請 {minutes} 分鐘後再試。")

_dummy_hash: str | None = None
_PRUNE_FROM = 1024


def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = passwords.hash_password("dummy password for timing")
    return _dummy_hash


class LoginThrottle:
    """Failure counts per username (and per client, if given), each paused after its limit."""

    def __init__(self, attempts: int = 5, minutes: float = 15, *, client_attempts: int = 20,
                 store: MutableMapping | None = None, clock: Callable[[], float] = time.time):
        self.attempts, self.minutes, self.client_attempts, self.clock = attempts, minutes, client_attempts, clock
        self._own_store = store is None
        self.store = {} if store is None else store     # key -> (failures, paused until, last failure)
        self._prune_at = _PRUNE_FROM

    def rule(self) -> Message:
        return Message("login.rule", {"attempts": self.attempts, "minutes": self.minutes})

    # keys never collide: a username typed as "client:..." becomes "user:client:..."
    @staticmethod
    def _user(username: str) -> str:
        return "user:" + normalize_username(username)

    @staticmethod
    def _client(client: str) -> str:
        return "client:" + str(client)

    @staticmethod
    def _change(account_id: str) -> str:
        return "change:" + str(account_id)

    def _state(self, key: str) -> tuple[int, float, float]:
        failures, until, last = self.store.get(key, (0, 0.0, 0.0))
        now = self.clock()
        if (until and now >= until) or (not until and now - last >= self.minutes * 60):
            return 0, 0.0, 0.0                              # the pause is over, or the failures are old
        return failures, until, last

    def _paused(self, key: str) -> bool:
        return self._state(key)[1] > self.clock()

    def _fail(self, key: str, limit: int) -> None:
        failures, until, _ = self._state(key)
        failures, now = failures + 1, self.clock()
        if failures >= limit:
            until = now + self.minutes * 60
        self.store[key] = (failures, until, now)
        if self._own_store and len(self.store) >= self._prune_at:
            self.prune()
            self._prune_at = max(_PRUNE_FROM, 2 * len(self.store))

    def prune(self) -> int:
        """Drop entries that no longer count (pause over, or failures older than ``minutes``); returns how many."""
        expired = [k for k in list(self.store) if self._state(k) == (0, 0.0, 0.0)]
        for k in expired:
            self.store.pop(k, None)
        return len(expired)

    def is_paused(self, username: str, client: str | None = None) -> bool:
        return self._paused(self._user(username)) or (client is not None and self._paused(self._client(client)))

    def failure(self, username: str, client: str | None = None) -> None:
        self._fail(self._user(username), self.attempts)
        if client is not None:
            self._fail(self._client(client), self.client_attempts)

    def success(self, username: str) -> None:
        self.store.pop(self._user(username), None)

    def unlock(self, username: str) -> None:
        """Lift a pause early (an administrator re-enabling an account, or a break-glass reset)."""
        self.store.pop(self._user(username), None)

    def unlock_client(self, client: str) -> None:
        self.store.pop(self._client(client), None)


@dataclass(frozen=True)
class LoginResult:
    account: Account
    must_change_password: bool
    new_hash: str | None = None       # set when the stored hash should be replaced (older format or parameters)


def authenticate(username: str, password: str, *, find_account: Callable[[str], Account | None],
                 throttle: LoginThrottle, client: str | None = None,
                 hasher: passwords.Hasher | None = None) -> LoginResult:
    """The account for a correct username and password; otherwise ``KitError("login.invalid")``.

    ``find_account`` looks an account up by username (for example ``AccountStore.find_by_username``). ``client``
    identifies where the attempt comes from (see the module text). When ``result.new_hash`` is set, store it
    (``AccountStore.update(id, password_hash=...)``); this upgrades old hashes as users log in."""
    name = normalize_username(username)
    if throttle.is_paused(name, client):
        passwords.verify_password(password, _dummy())
        raise KitError("login.invalid")
    account = find_account(name)
    ok = passwords.verify_password(password, account.password_hash if account else _dummy())
    if account is None or not ok or not account.active:
        throttle.failure(name, client)
        raise KitError("login.invalid")
    throttle.success(name)
    new_hash = passwords.hash_password(password, hasher) if passwords.needs_rehash(account.password_hash, hasher) \
        else None
    return LoginResult(account, account.must_change_password, new_hash)


def new_password_hash(account: Account, current: str, new: str, *, throttle: LoginThrottle | None = None,
                      hasher: passwords.Hasher | None = None, **policy) -> str:
    """Check a password change made by the account holder and return the hash to store
    (``AccountStore.set_password(id, hash, must_change_password=False)``, which also ends the other sessions).
    With ``throttle``, ``throttle.attempts`` wrong current passwords pause changes for ``throttle.minutes``
    (``login.change_paused``). ``policy`` goes to :func:`passwords.check_policy`."""
    key = LoginThrottle._change(account.account_id)
    if throttle is not None and throttle._paused(key):
        raise KitError("login.change_paused", minutes=throttle.minutes)
    if not passwords.verify_password(current, account.password_hash):
        if throttle is not None:
            throttle._fail(key, throttle.attempts)
        raise KitError("password.current_wrong")
    if throttle is not None:
        throttle.store.pop(key, None)
    if new == current:
        raise KitError("password.same_as_old")
    passwords.check_policy(new, username=account.username, **policy)
    return passwords.hash_password(new, hasher)
