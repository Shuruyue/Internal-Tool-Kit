"""An append-only record of who changed what.

Some changes must never happen without a record: accounts created, deactivated, re-enabled, promoted or given a
new password; records closed, reopened or handed to someone else. For those, record the change *before* making it,
so that a missing or broken log table blocks the change instead of letting it pass unrecorded::

    with log.change(actor.account_id, "item.close", "items", item_id, "closed after the final review"):
        ... make the change ...

:meth:`AuditLog.change` writes the entry, runs the block, and if the block raises, writes a second entry with the
action ``<action>.failed`` and the error type before passing the error on. Read an entry without a ``.failed``
partner as "done". (A spreadsheet has no transactions: only if the change fails *and* the second write fails too
does an entry stand for a change that did not happen; the opposite, a change without an entry, cannot occur.)
:meth:`AuditLog.record` writes a single entry, for events that are not changes or are already done.

Entry ids are the UTC time and a random part (``20261113T081500.123456Z-3f9a1c2b``), so writing an entry reads only
the header row, not the whole log, which only grows. :meth:`AuditLog.entries` lists the newest first by position.

The module offers no way to edit or delete an entry. That alone does not protect the log: protect the table itself
(in Google Sheets, a protected range editable only by the application's service account). Never put a password,
a token or a key in ``detail``.
"""

from __future__ import annotations

import datetime as dt
import secrets
from collections.abc import Callable
from contextlib import contextmanager

from internal_tool_kit.i18n import KitError
from internal_tool_kit.storage import Storage

COLUMNS = ["log_id", "at", "actor_id", "action", "target_table", "target_id", "detail"]


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_log_id() -> str:
    """Unique without reading the table: UTC time (to the clock's resolution, which on Windows can be coarser than
    a microsecond) and 32 random bits. The order of the log is the order of its rows, not of the ids."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + secrets.token_hex(4)


def _failure(error: Exception) -> str:
    """What a failed change records: the message key or the error type, never the message (it may hold data)."""
    return f"failed: {error.key}" if isinstance(error, KitError) else f"failed: {type(error).__name__}"


class AuditLog:
    def __init__(self, storage: Storage, table: str = "audit_logs", *, clock: Callable[[], str] = _utc_now):
        self.storage, self.table, self.clock = storage, table, clock

    def ensure_ready(self) -> None:
        """Raise ``KitError`` (storage.missing_table / storage.missing_columns) if entries cannot be written."""
        self.storage.require_columns(self.table, COLUMNS)

    def record(self, actor_id: str | None, action: str, target_table: str = "", target_id: str = "",
               detail: str = "") -> str:
        """Append an entry and return its id (one read of the header row, one write). ``actor_id`` None means
        someone outside the application (for example a break-glass reset); say who in ``detail``."""
        log_id = new_log_id()
        self.storage.append(self.table, {"log_id": log_id, "at": self.clock(), "actor_id": actor_id or "",
                                         "action": action, "target_table": target_table,
                                         "target_id": str(target_id), "detail": detail}, require=COLUMNS)
        return log_id

    @contextmanager
    def change(self, actor_id: str | None, action: str, target_table: str = "", target_id: str = "",
               detail: str = ""):
        """Record the change, then run the block; if the block raises, record ``<action>.failed`` and re-raise."""
        self.record(actor_id, action, target_table, target_id, detail)
        try:
            yield
        except Exception as exc:
            try:
                self.record(actor_id, action + ".failed", target_table, target_id, _failure(exc))
            except Exception:  # noqa: BLE001, S110  (the original error matters more; the entry above stands)
                pass
            raise

    def entries(self, *, target_table: str | None = None, target_id: str | None = None,
                actor_id: str | None = None, fresh: bool = True) -> list[dict[str, str]]:
        """Entries matching every filter given, newest first (the log is append-only, so by position)."""
        rows = self.storage.read(self.table, fresh=fresh, require=COLUMNS)
        rows = [r for r in rows if (target_table is None or r["target_table"] == target_table)
                and (target_id is None or r["target_id"] == str(target_id))
                and (actor_id is None or r["actor_id"] == str(actor_id))]
        return rows[::-1]
