"""A minimal stand-in for a gspread Spreadsheet: the methods SheetsStorage uses, with gspread's behavior where it
matters (values come back as strings, rows padded to the same width) and a check that every write is RAW."""

from __future__ import annotations

import re


class FakeWorksheet:
    def __init__(self, title: str, rows: list[list] | None = None):
        self.title = title
        self.rows: list[list[str]] = [[str(v) for v in r] for r in (rows or [])]
        self.writes: list[str] = []

    def get_all_values(self) -> list[list[str]]:
        width = max((len(r) for r in self.rows), default=0)
        last = max((i for i, r in enumerate(self.rows) if any(r)), default=-1)
        return [list(r) + [""] * (width - len(r)) for r in self.rows[:last + 1]]

    def _check(self, value_input_option) -> None:
        if value_input_option != "RAW":
            raise AssertionError(f"write with value_input_option={value_input_option!r}; only RAW is safe")

    def append_row(self, values, value_input_option=None, insert_data_option=None, table_range=None):
        self._check(value_input_option)
        self.writes.append("append")
        last = max((i for i, r in enumerate(self.rows) if any(r)), default=-1)
        self.rows.insert(last + 1, [str(v) for v in values])

    def update(self, values, range_name=None, value_input_option=None):
        self._check(value_input_option)
        self.writes.append(f"update {range_name}")
        number = int(re.fullmatch(r"A(\d+)", range_name).group(1))
        while len(self.rows) < number:
            self.rows.append([])
        self.rows[number - 1] = [str(v) for v in values[0]]


class FakeSpreadsheet:
    def __init__(self, tables: dict[str, list[str]]):
        self.sheets = {name: FakeWorksheet(name, [cols]) for name, cols in tables.items()}
        self.listings = 0

    def worksheets(self):
        self.listings += 1
        return list(self.sheets.values())
