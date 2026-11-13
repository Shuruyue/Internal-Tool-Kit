"""Signed "stay signed in" tokens, and re-checking the signed-in account on every request.

A token is ``payload.signature`` (base64url), the signature an HMAC-SHA256 of the payload with a server secret.
It names the account by its **id**, not its username (a renamed account's old name may be given to someone else,
and a token bound to the name would sign them in), and carries the account's **password version**: changing or
resetting the password increases the version, which voids every token issued before. It expires after ``days``.

A token is only half of it. The account behind a session can be deactivated, demoted or have its password reset
while the session is open, so :func:`current_account` should run on every request (every rerun of a page): it reads
the account again and ends the session when the account is gone, inactive or on a newer password version.

Storing the token is the caller's job: an HttpOnly, Secure, SameSite=Lax cookie where the framework allows it.

Secrets rotate without signing everyone out: pass ``[new_secret, old_secret]``; tokens are issued with the first
and accepted with any, until the old one is dropped after ``days``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Sequence

from internal_tool_kit.accounts import Account

MIN_SECRET_BYTES = 32


def new_secret() -> str:
    """A random secret for signing tokens (keep it in the deployment platform's secret store)."""
    return secrets.token_urlsafe(MIN_SECRET_BYTES)


def _secrets(secret: str | Sequence[str]) -> list[bytes]:
    items = [secret] if isinstance(secret, str) else list(secret)
    if not items:
        raise ValueError("no signing secret given")
    for s in items:
        if len(s.encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"a signing secret needs at least {MIN_SECRET_BYTES} bytes; use sessions.new_secret()")
    return [s.encode() for s in items]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, payload: str) -> str:
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def issue(account: Account, secret: str | Sequence[str], *, days: float = 7,
          clock: Callable[[], float] = time.time) -> str:
    now = int(clock())
    payload = _b64(json.dumps({"a": account.account_id, "v": account.password_version, "iat": now,
                               "exp": now + int(days * 86400), "n": secrets.token_hex(8)},
                              separators=(",", ":")).encode())
    return f"{payload}.{_sign(_secrets(secret)[0], payload)}"


def verify(token: str, secret: str | Sequence[str], *, get_account: Callable[[str], Account | None],
           clock: Callable[[], float] = time.time) -> Account | None:
    """The account a token signs in, or None for anything wrong with it (never an exception for a bad token)."""
    keys = _secrets(secret)
    try:
        payload, signature = token.split(".")
        if not any(hmac.compare_digest(_sign(k, payload), signature) for k in keys):
            return None
        claims = json.loads(_unb64(payload))
        account_id, version, expires = str(claims["a"]), int(claims["v"]), int(claims["exp"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if clock() >= expires:
        return None
    return current_account(account_id, version, get_account=get_account)


def current_account(account_id: str, password_version: int, *,
                    get_account: Callable[[str], Account | None]) -> Account | None:
    """The account as it is now, or None if the session must end: the account no longer exists, is inactive, or
    its password changed since the session began. Call it on every request with fresh data, and use the returned
    account (its role may have changed) rather than one kept from the login."""
    account = get_account(str(account_id))
    if account is None or not account.active or account.password_version != int(password_version):
        return None
    return account
