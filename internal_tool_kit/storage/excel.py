"""A local ``.xlsx`` workbook as storage, for development on a copy of the real data (requires openpyxl).

Every string is written as a text cell, so a value such as ``=HYPERLINK(...)`` typed by a user stays text
(openpyxl would otherwise store any string that starts with ``=`` as a formula). The workbook is reloaded when the
file changes on disk, and saved through a temporary file so that an interrupted save cannot corrupt it.
"""

from __future__ import annotations

import os
from pathlib import Path

from internal_tool_kit.storage import Storage


class ExcelStorage(Storage):
    def __init__(self, path: str | os.PathLike, *, production: bool = False, cache_seconds: float = 0.0):
        super().__init__(production=production, cache_seconds=cache_seconds)
        self.path = Path(path)
        self._book = None
        self._mtime: int | None = None

    @staticmethod
    def create(path: str | os.PathLike, tables: dict[str, list[str]], **kwargs) -> ExcelStorage:
        """Write a new workbook with one sheet per table (header row only) and open it."""
        import openpyxl

        book = openpyxl.Workbook()
        book.remove(book.active)
        for name, columns in tables.items():
            book.create_sheet(name).append(list(columns))
        path = Path(path)
        book.save(path)
        return ExcelStorage(path, **kwargs)

    def _workbook(self):
        import openpyxl

        mtime = self.path.stat().st_mtime_ns
        if self._book is None or mtime != self._mtime:
            self._book = openpyxl.load_workbook(self.path)
            self._mtime = mtime
        return self._book

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        self._book.save(tmp)
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime_ns

    def table_names(self) -> list[str]:
        return list(self._workbook().sheetnames)

    def _read_values(self, table: str) -> list[list]:
        return [list(r) for r in self._workbook()[table].iter_rows(values_only=True)]

    def _set_row(self, sheet, number: int, values: list[str]) -> None:
        for column, value in enumerate(values, start=1):
            c = sheet.cell(row=number, column=column)
            c.value = value
            c.data_type = "s"        # text, never a formula

    def _append_values(self, table: str, values: list[str]) -> None:
        sheet = self._workbook()[table]
        rows = self._read_values(table)
        last = max((i for i, r in enumerate(rows, start=1) if any(v not in (None, "") for v in r)), default=1)
        self._set_row(sheet, last + 1, values)
        self._save()

    def _write_row(self, table: str, number: int, values: list[str]) -> None:
        self._set_row(self._workbook()[table], number, values)
        self._save()
