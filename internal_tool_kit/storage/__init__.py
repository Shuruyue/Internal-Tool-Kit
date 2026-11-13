"""Tables of rows behind one interface, with interchangeable backends.

A *table* is a sheet whose first row holds the column names; every cell is read back as a string. The same code
runs on three backends:

=====================  ==========================================  =======================================
backend                for                                         ``production`` by default
=====================  ==========================================  =======================================
:class:`MemoryStorage`  tests                                       False
``ExcelStorage``        development on a local copy (``.xlsx``)     False
``SheetsStorage``       Google Sheets through a service account     True
=====================  ==========================================  =======================================

What the base class guarantees, whatever the backend:

- **Fresh data for writes.** :meth:`Storage.insert` allocates the next id from freshly read rows and appends the
  row under the same lock, so two inserts in one process never get the same id (a cached read would let them).
  :meth:`Storage.update` also locates its row in fresh data. Reads may use a short cache (``cache_seconds``).
- **No silent data loss.** Writing a column the table does not have, updating a key that matches no row or more
  than one row, and reading a table that is missing or has a malformed header all raise :class:`KitError`.
- **Text stays text.** A value that starts with ``=``, ``+``, ``-`` or ``@`` is stored as text, never as a formula
  (Excel: the cell type is forced to string; Sheets: values are written RAW). :func:`escape_formula` protects
  exported CSV files the same way.
- **Production is marked.** ``storage.production`` says whether the backend holds real data;
  :func:`require_test_data` lets tools that write fake data or switch identities refuse to run on it.

The lock is per process. Two servers, or a person typing in the spreadsheet at the same time, can still race;
a spreadsheet has no transactions, which is the main reason to move to a database as an application grows.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable

from internal_tool_kit.i18n import KitError, register

register("storage.missing_table", "Table '{table}' does not exist.", "找不到資料表「{table}」。")
register("storage.bad_header", "Table '{table}' has an empty or repeated column name in its first row.",
         "資料表「{table}」第一列的欄位名稱有空白或重複。")
register("storage.missing_columns", "Table '{table}' lacks the columns: {columns}.",
         "資料表「{table}」缺少欄位：{columns}。")
register("storage.unknown_columns", "Table '{table}' has no columns named: {columns}.",
         "資料表「{table}」沒有這些欄位：{columns}。")
register("storage.not_found", "No row of '{table}' has {column} = {key}.",
         "資料表「{table}」中找不到 {column} = {key} 的資料。")
register("storage.duplicate_key", "{count} rows of '{table}' have {column} = {key}; the key must be unique.",
         "資料表「{table}」中有 {count} 列的 {column} = {key}，鍵值必須唯一。")
register("storage.production_refused", "This tool only runs on test data, and this storage holds production data.",
         "此工具只能用於測試資料，目前連接的是正式資料。")

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def cell(value) -> str:
    """The string stored for a value: '' for None, the text of anything else."""
    return "" if value is None else str(value)


def escape_formula(text: str) -> str:
    """Prefix a quote to text that a spreadsheet program would run as a formula when opening a CSV file
    (OWASP: CSV injection). Use it on every exported cell; the storage backends themselves never need it."""
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def require_test_data(storage: Storage) -> None:
    """Raise unless ``storage`` holds test data; for tools that write fake data or impersonate users."""
    if storage.production:
        raise KitError("storage.production_refused")


class Storage(ABC):
    """The interface; subclasses implement only the four primitives at the bottom."""

    def __init__(self, *, production: bool, cache_seconds: float = 0.0):
        self.production = production
        self.cache_seconds = cache_seconds
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, list[str], list[tuple[int, list[str]]]]] = {}

    # ----------------------------------------------------------------------------------------- reading
    def columns(self, table: str) -> list[str]:
        return list(self._load(table, fresh=False)[0])

    def read(self, table: str, *, fresh: bool = False) -> list[dict[str, str]]:
        """Every row of ``table`` as {column: text}; ``fresh`` skips the cache."""
        header, rows = self._load(table, fresh=fresh)
        return [dict(zip(header, values, strict=True)) for _, values in rows]

    def find(self, table: str, column: str, value, *, fresh: bool = False) -> list[dict[str, str]]:
        self._check_known(table, [column])
        value = cell(value)
        return [r for r in self.read(table, fresh=fresh) if r[column] == value]

    def require_columns(self, table: str, columns: Iterable[str]) -> None:
        """Raise unless ``table`` exists and has every column in ``columns`` (check before a feature relies on it)."""
        missing = [c for c in columns if c not in self.columns(table)]
        if missing:
            raise KitError("storage.missing_columns", table=table, columns=", ".join(missing))

    # ----------------------------------------------------------------------------------------- writing
    def append(self, table: str, row: dict) -> dict[str, str]:
        """Append ``row`` (missing columns are left empty) and return it as stored."""
        with self._lock:
            header, _ = self._load(table, fresh=True)
            values = self._values(table, header, row)
            self._append_values(table, values)
            self._cache.pop(table, None)
            return dict(zip(header, values, strict=True))

    def insert(self, table: str, row: dict, id_column: str) -> str:
        """Append ``row`` with ``id_column`` set to one more than the largest integer id in the table; returns the
        new id. Reading the ids and appending happen under one lock, on fresh data."""
        with self._lock:
            header, rows = self._load(table, fresh=True)
            self._check_known(table, [id_column], header)
            index = header.index(id_column)
            ids = [int(v[index]) for _, v in rows if v[index].strip().lstrip("-").isdigit()]
            new_id = str(max(ids, default=0) + 1)
            self.append(table, {**row, id_column: new_id})
            return new_id

    def update(self, table: str, key_column: str, key, values: dict) -> dict[str, str]:
        """Change ``values`` in the one row whose ``key_column`` equals ``key``; returns the row as stored."""
        with self._lock:
            header, rows = self._load(table, fresh=True)
            self._check_known(table, [key_column, *values], header)
            index, key = header.index(key_column), cell(key)
            hits = [(n, v) for n, v in rows if v[index] == key]
            if not hits:
                raise KitError("storage.not_found", table=table, column=key_column, key=key)
            if len(hits) > 1:
                raise KitError("storage.duplicate_key", table=table, column=key_column, key=key, count=len(hits))
            number, old = hits[0]
            new = [cell(values[c]) if c in values else old[i] for i, c in enumerate(header)]
            self._write_row(table, number, new)
            self._cache.pop(table, None)
            return dict(zip(header, new, strict=True))

    # ----------------------------------------------------------------------------------------- helpers
    def _load(self, table: str, *, fresh: bool) -> tuple[list[str], list[tuple[int, list[str]]]]:
        """(header, [(sheet row number, values padded to the header)]); rows that are entirely empty are skipped."""
        with self._lock:
            hit = self._cache.get(table)
            if not fresh and hit and time.monotonic() - hit[0] < self.cache_seconds:
                return hit[1], hit[2]
            if not self._has_table(table):
                raise KitError("storage.missing_table", table=table)
            raw = [[cell(v) for v in r] for r in self._read_values(table)]
            header = list(raw[0]) if raw else []
            while header and not header[-1].strip():
                header.pop()
            if not header or any(not c.strip() for c in header) or len(set(header)) != len(header):
                raise KitError("storage.bad_header", table=table)
            rows = []
            for number, values in enumerate(raw[1:], start=2):
                values = (values + [""] * len(header))[:len(header)]
                if any(v.strip() for v in values):
                    rows.append((number, values))
            if self.cache_seconds > 0:
                self._cache[table] = (time.monotonic(), header, rows)
            return header, rows

    def _check_known(self, table: str, columns: Iterable[str], header: list[str] | None = None) -> None:
        header = header if header is not None else self.columns(table)
        unknown = [c for c in columns if c not in header]
        if unknown:
            raise KitError("storage.unknown_columns", table=table, columns=", ".join(unknown))

    def _values(self, table: str, header: list[str], row: dict) -> list[str]:
        self._check_known(table, row, header)
        return [cell(row.get(c)) for c in header]

    # ----------------------------------------------------------------------------------------- primitives
    def _has_table(self, table: str) -> bool:
        return table in self.table_names()

    @abstractmethod
    def table_names(self) -> list[str]:
        """Names of the tables (sheets)."""

    @abstractmethod
    def _read_values(self, table: str) -> list[list]:
        """All cells of the table, header first, as a list of rows."""

    @abstractmethod
    def _append_values(self, table: str, values: list[str]) -> None:
        """Add a row after the last row."""

    @abstractmethod
    def _write_row(self, table: str, number: int, values: list[str]) -> None:
        """Overwrite sheet row ``number`` (1 is the header) with ``values``."""


class MemoryStorage(Storage):
    """Tables held in memory; ``tables`` maps a table name to its column names (and optionally rows)."""

    def __init__(self, tables: dict[str, list[str]] | None = None, *, production: bool = False,
                 rows: dict[str, list[dict]] | None = None):
        super().__init__(production=production)
        self._tables: dict[str, list[list[str]]] = {name: [list(cols)] for name, cols in (tables or {}).items()}
        for name, items in (rows or {}).items():
            for item in items:
                self.append(name, item)

    def add_table(self, name: str, columns: list[str]) -> None:
        with self._lock:
            if name in self._tables:
                raise ValueError(f"table {name!r} exists")
            self._tables[name] = [list(columns)]

    def table_names(self) -> list[str]:
        return list(self._tables)

    def _read_values(self, table: str) -> list[list]:
        return [list(r) for r in self._tables[table]]

    def _append_values(self, table: str, values: list[str]) -> None:
        self._tables[table].append(list(values))

    def _write_row(self, table: str, number: int, values: list[str]) -> None:
        self._tables[table][number - 1] = list(values)


__all__ = ["Storage", "MemoryStorage", "cell", "escape_formula", "require_test_data", "FORMULA_PREFIXES"]
