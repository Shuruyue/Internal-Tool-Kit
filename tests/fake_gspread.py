"""A minimal stand-in for a gspread Spreadsheet: the methods SheetsStorage uses, with gspread's behavior where it
matters (values come back as strings, rows padded to the same width) and a check that every write is RAW.

Every call that would be one request to the Sheets API is counted in ``spreadsheet.requests``, and ``fail`` can
make the next calls raise errors carrying an HTTP status, like gspread's ``APIError``."""

from __future__ import annotations

import itertools
import re

_ids = itertools.count(1)


class FakeAPIError(Exception):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeWorksheet:
    def __init__(self, title: str, rows: list[list] | None = None, book: FakeSpreadsheet | None = None):
        self.title = title
        self.rows: list[list[str]] = [[str(v) for v in r] for r in (rows or [])]
        self.writes: list[str] = []
        self.book = book

    def _request(self, kind: str) -> None:
        if self.book is not None:
            self.book.request(kind)

    def get_all_values(self) -> list[list[str]]:
        self._request("read")
        width = max((len(r) for r in self.rows), default=0)
        last = max((i for i, r in enumerate(self.rows) if any(r)), default=-1)
        return [list(r) + [""] * (width - len(r)) for r in self.rows[:last + 1]]

    def row_values(self, row: int) -> list[str]:
        self._request("read")
        values = list(self.rows[row - 1]) if len(self.rows) >= row else []
        while values and values[-1] == "":
            values.pop()
        return values

    def _check(self, value_input_option) -> None:
        if value_input_option != "RAW":
            raise AssertionError(f"write with value_input_option={value_input_option!r}; only RAW is safe")

    def append_row(self, values, value_input_option=None, insert_data_option=None, table_range=None):
        self._check(value_input_option)
        self._request("write")
        self.writes.append("append")
        last = max((i for i, r in enumerate(self.rows) if any(r)), default=-1)
        self.rows.insert(last + 1, [str(v) for v in values])

    def update(self, values, range_name=None, value_input_option=None):
        self._check(value_input_option)
        self._request("write")
        self.writes.append(f"update {range_name}")
        number = int(re.fullmatch(r"A(\d+)", range_name).group(1))
        while len(self.rows) < number:
            self.rows.append([])
        self.rows[number - 1] = [str(v) for v in values[0]]


class FakeSpreadsheet:
    def __init__(self, tables: dict[str, list[str]], *, spreadsheet_id: str | None = None):
        self.id = spreadsheet_id or f"fake-{next(_ids)}"
        self.sheets = {name: FakeWorksheet(name, [cols], self) for name, cols in tables.items()}
        self.listings = 0
        self.requests = {"read": 0, "write": 0}
        self.failures: list[int] = []          # HTTP statuses the next requests fail with, in order

    def request(self, kind: str) -> None:
        if self.failures:
            raise FakeAPIError(self.failures.pop(0))
        self.requests[kind] += 1

    def fail(self, *statuses: int) -> None:
        self.failures.extend(statuses)

    def reset_counts(self) -> None:
        self.requests = {"read": 0, "write": 0}

    def worksheets(self):
        self.request("read")
        self.listings += 1
        return list(self.sheets.values())
