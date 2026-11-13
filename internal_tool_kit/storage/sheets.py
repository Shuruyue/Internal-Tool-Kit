"""Google Sheets as storage, through a service account (requires gspread and google-auth).

Values are written RAW: the API stores what it is given as text and never parses it as a formula, a date or a
number. The storage counts as production unless constructed with ``production=False`` (a test copy of the
spreadsheet). Storage objects of the same spreadsheet share one lock in the process (keyed by the spreadsheet id).

**Quota.** Google allows 60 read and 60 write requests a minute per user of a project, and a service account is one
user however many people use the application. Each read of a table is one request (a header check reads only the
first row, still one request). When the API answers 429 (quota) or a 5xx error, a read is retried with truncated
exponential backoff, as Google recommends. A write is retried only after a 429, which Google returns before doing
anything; after a 5xx the write may or may not have happened, and retrying an append could add the row twice, so
the error is raised instead.

The service account can do whatever its sharing allows, and anyone who can edit the spreadsheet directly can
bypass every rule of the application (change a role, a password hash, delete the audit log). The sharing list of
the spreadsheet is the real permission boundary: share it with the service account and one owner only, and see
``docs/KEY_MANAGEMENT.md``.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from internal_tool_kit.storage import Storage

READ_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
WRITE_RETRY_STATUSES = frozenset({429})


def _status(error: Exception) -> int | None:
    """The HTTP status of a gspread ``APIError`` (or anything with ``code`` or ``response.status_code``)."""
    code = getattr(error, "code", None)
    if isinstance(code, int) and code > 0:
        return code
    code = getattr(getattr(error, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


class SheetsStorage(Storage):
    def __init__(self, spreadsheet, *, production: bool = True, cache_seconds: float = 0.0, retries: int = 5,
                 max_backoff: float = 32.0, sleep: Callable[[float], None] = time.sleep):
        """``spreadsheet``: a ``gspread.Spreadsheet`` (or any object with the same ``worksheets`` method and
        worksheets with ``title``, ``get_all_values``, ``row_values``, ``append_row`` and ``update``).
        ``retries``: how many times a request refused for the quota (or a 5xx error, reads only) is repeated."""
        spreadsheet_id = getattr(spreadsheet, "id", None)
        super().__init__(production=production, cache_seconds=cache_seconds,
                         lock_key=("sheets", spreadsheet_id) if spreadsheet_id else None)
        self._spreadsheet = spreadsheet
        self._sheets: dict = {}
        self.retries, self.max_backoff, self._sleep = retries, max_backoff, sleep

    @classmethod
    def from_service_account(cls, info: dict, spreadsheet_key: str, **kwargs) -> SheetsStorage:
        """Open a spreadsheet by key with a service-account key given as a dict (for example from the deployment
        platform's secret store; never from a file in the repository)."""
        import gspread

        return cls(gspread.service_account_from_dict(info).open_by_key(spreadsheet_key), **kwargs)

    def _call(self, statuses: frozenset[int], fn, *args, **kwargs):
        """``fn(*args, **kwargs)``, repeated with truncated exponential backoff while the API answers a status in
        ``statuses``: wait min(2^n + random fraction, max_backoff) seconds before the n-th repetition."""
        for attempt in range(self.retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if attempt == self.retries or _status(exc) not in statuses:
                    raise
                self._sleep(min(2 ** attempt + random.random(), self.max_backoff))  # noqa: S311  (jitter)
        raise AssertionError("unreachable")  # pragma: no cover

    def _refresh(self) -> None:
        self._sheets = {ws.title: ws for ws in self._call(READ_RETRY_STATUSES, self._spreadsheet.worksheets)}

    def _sheet(self, table: str):
        if table not in self._sheets:
            self._refresh()
        return self._sheets[table]

    def _has_table(self, table: str) -> bool:
        if table not in self._sheets:
            self._refresh()
        return table in self._sheets

    def table_names(self) -> list[str]:
        self._refresh()
        return list(self._sheets)

    def _read_values(self, table: str) -> list[list]:
        return self._call(READ_RETRY_STATUSES, self._sheet(table).get_all_values)

    def _read_header(self, table: str) -> list:
        return self._call(READ_RETRY_STATUSES, self._sheet(table).row_values, 1)

    def _append_values(self, table: str, values: list[str]) -> None:
        # table_range A1: append after the table that starts at A1, not after some stray cell further down
        self._call(WRITE_RETRY_STATUSES, self._sheet(table).append_row, values, value_input_option="RAW",
                   insert_data_option="INSERT_ROWS", table_range="A1")

    def _write_row(self, table: str, number: int, values: list[str]) -> None:
        self._call(WRITE_RETRY_STATUSES, self._sheet(table).update, [values], range_name=f"A{number}",
                   value_input_option="RAW")
