"""An append-only record of who changed what.

Some changes must never happen without a record: accounts created, deactivated, re-enabled, promoted or given a
new password; records closed, reopened or handed to someone else. For those, check that the log can be written
*before* making the change, so that a missing or broken log table blocks the change instead of letting it pass
unrecorded::

    log.ensure_ready()                       # raises if the table or a column is missing
    ... make the change ...
    log.record(actor.account_id, "item.close", "items", item_id, "closed after the final review")

or, equivalently, ``with log.change(actor_id, action, table, id, detail): ...``, which records only if the block
succeeds.

The module offers no way to edit or delete an entry. That alone does not protect the log: protect the table itself
(in Google Sheets, a protected range editable only by the application's service account). Never put a password,
a token or a key in ``detail``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import contextmanager

from internal_tool_kit.storage import Storage

COLUMNS = ["log_id", "at", "actor_id", "action", "target_table", "target_id", "detail"]


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    def __init__(self, storage: Storage, table: str = "audit_logs", *, clock: Callable[[], str] = _utc_now):
        self.storage, self.table, self.clock = storage, table, clock

    def ensure_ready(self) -> None:
        """Raise ``KitError`` (storage.missing_table / storage.missing_columns) if entries cannot be written."""
        self.storage.require_columns(self.table, COLUMNS)

    def record(self, actor_id: str | None, action: str, target_table: str = "", target_id: str = "",
               detail: str = "") -> str:
        """Append an entry and return its id. ``actor_id`` None means someone outside the application (for
        example a break-glass reset); say who in ``detail``."""
        self.ensure_ready()
        return self.storage.insert(self.table, {"at": self.clock(), "actor_id": actor_id or "", "action": action,
                                                "target_table": target_table, "target_id": target_id,
                                                "detail": detail}, "log_id")

    @contextmanager
    def change(self, actor_id: str | None, action: str, target_table: str = "", target_id: str = "",
               detail: str = ""):
        """Check the log first, run the block, then record; nothing is recorded if the block raises."""
        self.ensure_ready()
        yield
        self.record(actor_id, action, target_table, target_id, detail)

    def entries(self, *, target_table: str | None = None, target_id: str | None = None,
                actor_id: str | None = None, fresh: bool = True) -> list[dict[str, str]]:
        """Entries matching every filter given, newest first."""
        self.ensure_ready()
        rows = self.storage.read(self.table, fresh=fresh)
        rows = [r for r in rows if (target_table is None or r["target_table"] == target_table)
                and (target_id is None or r["target_id"] == str(target_id))
                and (actor_id is None or r["actor_id"] == str(actor_id))]
        return sorted(rows, key=lambda r: int(r["log_id"]) if r["log_id"].isdigit() else 0, reverse=True)
