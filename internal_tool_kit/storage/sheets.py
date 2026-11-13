"""Google Sheets as storage, through a service account (requires gspread and google-auth).

Values are written RAW: the API stores what it is given as text and never parses it as a formula, a date or a
number. The storage counts as production unless constructed with ``production=False`` (a test copy of the
spreadsheet).

The service account can do whatever its sharing allows, and anyone who can edit the spreadsheet directly can
bypass every rule of the application (change a role, a password hash, delete the audit log). The sharing list of
the spreadsheet is the real permission boundary: share it with the service account and one owner only, and see
``docs/KEY_MANAGEMENT.md``.
"""

from __future__ import annotations

from internal_tool_kit.storage import Storage


class SheetsStorage(Storage):
    def __init__(self, spreadsheet, *, production: bool = True, cache_seconds: float = 0.0):
        """``spreadsheet``: a ``gspread.Spreadsheet`` (or any object with the same ``worksheets`` method and
        worksheets with ``title``, ``get_all_values``, ``append_row`` and ``update``)."""
        super().__init__(production=production, cache_seconds=cache_seconds)
        self._spreadsheet = spreadsheet
        self._sheets: dict = {}

    @classmethod
    def from_service_account(cls, info: dict, spreadsheet_key: str, **kwargs) -> SheetsStorage:
        """Open a spreadsheet by key with a service-account key given as a dict (for example from the deployment
        platform's secret store; never from a file in the repository)."""
        import gspread

        return cls(gspread.service_account_from_dict(info).open_by_key(spreadsheet_key), **kwargs)

    def _refresh(self) -> None:
        self._sheets = {ws.title: ws for ws in self._spreadsheet.worksheets()}

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
        return self._sheet(table).get_all_values()

    def _append_values(self, table: str, values: list[str]) -> None:
        # table_range A1: append after the table that starts at A1, not after some stray cell further down
        self._sheet(table).append_row(values, value_input_option="RAW", insert_data_option="INSERT_ROWS",
                                      table_range="A1")

    def _write_row(self, table: str, number: int, values: list[str]) -> None:
        self._sheet(table).update([values], range_name=f"A{number}", value_input_option="RAW")
