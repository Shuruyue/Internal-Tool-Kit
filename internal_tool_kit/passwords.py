"""Password hashing and the password policy.

The default hasher is scrypt from the standard library, with the parameters OWASP recommends (N = 2^17, r = 8,
p = 1: 128 MiB and a few hundred milliseconds per hash), so nothing has to be installed. bcrypt and Argon2id are
available as alternatives (``pip install internal-tool-kit[bcrypt]`` or ``[argon2]``), for example to keep the
hashes of an existing user table. :func:`verify_password` recognizes all three formats, so a table can hold a mix
while it migrates: after a successful login, :func:`needs_rehash` says when to store a new hash.

The policy limits a password to 72 bytes by default. bcrypt cannot use more (newer versions refuse), and a limit
keeps a slow hash from being fed megabytes; 72 bytes are 72 Latin letters or 24 Chinese characters.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import string
from typing import Protocol

from internal_tool_kit.i18n import KitError, register

register("password.too_short", "The password must have at least {min_length} characters.",
         "密碼至少需要 {min_length} 個字元。")
register("password.too_long", "The password must not be longer than {max_bytes} bytes.",
         "密碼不能超過 {max_bytes} 個位元組（中文字一個字算 3 個位元組）。")
register("password.needs_letter_and_digit", "The password must contain at least one letter and one digit.",
         "密碼必須同時包含英文字母與數字。")
register("password.contains_username", "The password must not contain the username.", "密碼不能包含帳號名稱。")
register("password.same_as_old", "The new password must differ from the current one.", "新密碼不能與目前的密碼相同。")
register("password.current_wrong", "The current password is incorrect.", "目前的密碼不正確。")


class Hasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password: str, stored: str) -> bool: ...
    def identifies(self, stored: str) -> bool: ...
    def needs_rehash(self, stored: str) -> bool: ...


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ScryptHasher:
    """scrypt from hashlib. Stored as ``scrypt$N$r$p$salt$hash`` (base64url)."""

    prefix = "scrypt$"

    def __init__(self, n: int = 2 ** 17, r: int = 8, p: int = 1, salt_bytes: int = 16, length: int = 32):
        self.n, self.r, self.p, self.salt_bytes, self.length = n, r, p, salt_bytes, length

    @staticmethod
    def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=length,
                              maxmem=128 * r * n * p + 2 ** 24)

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(self.salt_bytes)
        key = self._derive(password, salt, self.n, self.r, self.p, self.length)
        return f"scrypt${self.n}${self.r}${self.p}${_b64(salt)}${_b64(key)}"

    @staticmethod
    def _parse(stored: str):
        _, n, r, p, salt, key = stored.split("$")
        return int(n), int(r), int(p), _unb64(salt), _unb64(key)

    def verify(self, password: str, stored: str) -> bool:
        try:
            n, r, p, salt, key = self._parse(stored)
            if n > 2 ** 20 or r > 32 or p > 16:          # refuse parameters that would exhaust memory
                return False
            return hmac.compare_digest(self._derive(password, salt, n, r, p, len(key)), key)
        except (ValueError, TypeError):
            return False

    def identifies(self, stored: str) -> bool:
        return stored.startswith(self.prefix)

    def needs_rehash(self, stored: str) -> bool:
        try:
            n, r, p, _, key = self._parse(stored)
        except (ValueError, TypeError):
            return True
        return (n, r, p, len(key)) != (self.n, self.r, self.p, self.length)


class BcryptHasher:
    """bcrypt (``pip install bcrypt``); passwords longer than 72 bytes are refused, not truncated."""

    def __init__(self, rounds: int = 12):
        self.rounds = rounds

    def hash(self, password: str) -> str:
        import bcrypt

        data = password.encode("utf-8")
        if len(data) > 72:
            raise KitError("password.too_long", max_bytes=72)
        return bcrypt.hashpw(data, bcrypt.gensalt(self.rounds)).decode()

    def verify(self, password: str, stored: str) -> bool:
        import bcrypt

        data = password.encode("utf-8")
        if len(data) > 72:
            return False
        try:
            return bcrypt.checkpw(data, stored.encode())
        except ValueError:
            return False

    def identifies(self, stored: str) -> bool:
        return stored.startswith(("$2a$", "$2b$", "$2y$"))

    def needs_rehash(self, stored: str) -> bool:
        try:
            return int(stored.split("$")[2]) < self.rounds
        except (IndexError, ValueError):
            return True


class Argon2Hasher:
    """Argon2id (``pip install argon2-cffi``) with the library's default parameters."""

    def __init__(self):
        from argon2 import PasswordHasher

        self._ph = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, password: str, stored: str) -> bool:
        from argon2.exceptions import Argon2Error, InvalidHashError

        try:
            return self._ph.verify(stored, password)
        except (Argon2Error, InvalidHashError, ValueError):
            return False

    def identifies(self, stored: str) -> bool:
        return stored.startswith("$argon2")

    def needs_rehash(self, stored: str) -> bool:
        try:
            return self._ph.check_needs_rehash(stored)
        except ValueError:
            return True


DEFAULT_HASHER: Hasher = ScryptHasher()


def _hasher_for(stored: str, hashers) -> Hasher | None:
    for h in hashers:
        if h.identifies(stored):
            return h
    if BcryptHasher().identifies(stored):
        return BcryptHasher()
    if stored.startswith("$argon2"):
        return Argon2Hasher()
    return None


def hash_password(password: str, hasher: Hasher | None = None) -> str:
    return (hasher or DEFAULT_HASHER).hash(password)


def verify_password(password: str, stored: str, hashers: tuple = ()) -> bool:
    """Whether ``password`` matches ``stored`` in any known format; False (never an exception) for an empty or
    unrecognized hash, so a malformed row cannot be used to log in."""
    if not stored:
        return False
    h = _hasher_for(stored, (*hashers, DEFAULT_HASHER))
    return bool(h and h.verify(password, stored))


def needs_rehash(stored: str, hasher: Hasher | None = None) -> bool:
    """Whether ``stored`` should be replaced by a hash from ``hasher`` (another format or weaker parameters)."""
    hasher = hasher or DEFAULT_HASHER
    return not hasher.identifies(stored) or hasher.needs_rehash(stored)


def check_policy(password: str, *, username: str | None = None, min_length: int = 8, max_bytes: int = 72,
                 letter_and_digit: bool = True) -> None:
    """Raise :class:`KitError` with the first rule the password breaks."""
    if len(password) < min_length:
        raise KitError("password.too_short", min_length=min_length)
    if len(password.encode("utf-8")) > max_bytes:
        raise KitError("password.too_long", max_bytes=max_bytes)
    if letter_and_digit and not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise KitError("password.needs_letter_and_digit")
    if username and len(username) >= 3 and username.strip().lower() in password.lower():
        raise KitError("password.contains_username")


_UNAMBIGUOUS_LETTERS = "".join(c for c in string.ascii_letters if c not in "IlO")
_UNAMBIGUOUS_DIGITS = "23456789"


def temporary_password(length: int = 12) -> str:
    """A random password that meets the default policy and has no easily confused characters (I, l, O, 0, 1)."""
    alphabet = _UNAMBIGUOUS_LETTERS + _UNAMBIGUOUS_DIGITS
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c in _UNAMBIGUOUS_LETTERS for c in pw) and any(c in _UNAMBIGUOUS_DIGITS for c in pw):
            return pw
