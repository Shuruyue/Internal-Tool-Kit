"""The storage interface, run on every backend."""

import threading

import pytest

from internal_tool_kit.i18n import KitError
from internal_tool_kit.storage import MemoryStorage, escape_formula, require_test_data
from internal_tool_kit.storage.excel import ExcelStorage
from internal_tool_kit.storage.sheets import SheetsStorage

from .fake_gspread import FakeSpreadsheet

TABLES = {"items": ["item_id", "name", "owner_id", "note"], "empty": ["a", "b"]}


@pytest.fixture(params=["memory", "excel", "sheets"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStorage(TABLES)
    if request.param == "excel":
        return ExcelStorage.create(tmp_path / "data.xlsx", TABLES)
    return SheetsStorage(FakeSpreadsheet(TABLES), production=False)


def test_insert_numbers_rows_and_reads_them_back_as_text(store):
    assert store.insert("items", {"name": "pump", "owner_id": 7}, "item_id") == "1"
    assert store.insert("items", {"name": "valve"}, "item_id") == "2"
    rows = store.read("items")
    assert rows == [{"item_id": "1", "name": "pump", "owner_id": "7", "note": ""},
                    {"item_id": "2", "name": "valve", "owner_id": "", "note": ""}]
    assert store.find("items", "owner_id", 7) == rows[:1]


def test_ids_continue_after_the_largest_integer_and_ignore_other_values(store):
    store.append("items", {"item_id": "41", "name": "a"})
    store.append("items", {"item_id": "draft", "name": "b"})
    store.append("items", {"item_id": "9", "name": "c"})
    assert store.insert("items", {"name": "d"}, "item_id") == "42"


def test_update_changes_only_the_given_columns_of_one_row(store):
    store.insert("items", {"name": "pump", "note": "old"}, "item_id")
    store.insert("items", {"name": "valve"}, "item_id")
    assert store.update("items", "item_id", 1, {"note": "new"})["name"] == "pump"
    assert [r["note"] for r in store.read("items", fresh=True)] == ["new", ""]


def test_update_refuses_a_missing_or_repeated_key(store):
    store.append("items", {"item_id": "5", "name": "a"})
    store.append("items", {"item_id": "5", "name": "b"})
    with pytest.raises(KitError) as exc:
        store.update("items", "item_id", "6", {"note": "x"})
    assert exc.value.key == "storage.not_found"
    with pytest.raises(KitError) as exc:
        store.update("items", "item_id", "5", {"note": "x"})
    assert exc.value.key == "storage.duplicate_key"
    assert [r["note"] for r in store.read("items", fresh=True)] == ["", ""]


def test_unknown_tables_and_columns_are_refused_not_dropped(store):
    with pytest.raises(KitError) as exc:
        store.read("nope")
    assert exc.value.key == "storage.missing_table"
    with pytest.raises(KitError) as exc:
        store.append("items", {"name": "a", "colour": "red"})
    assert exc.value.key == "storage.unknown_columns"
    with pytest.raises(KitError) as exc:
        store.update("items", "item_id", "1", {"colour": "red"})
    assert exc.value.key == "storage.unknown_columns"
    assert store.read("items") == []


def test_require_columns_names_what_is_missing(store):
    store.require_columns("items", ["item_id", "note"])
    with pytest.raises(KitError) as exc:
        store.require_columns("items", ["item_id", "created_at", "actor"])
    assert exc.value.key == "storage.missing_columns"
    assert "created_at, actor" in exc.value.text("en")
    assert "created_at, actor" in exc.value.text("zh-TW")


def test_formula_like_text_is_stored_and_read_back_as_text(store):
    for text in ("=1+1", '=HYPERLINK("http://example.com","x")', "+886 2 1234", "-5", "@user"):
        store.append("items", {"item_id": text[:3], "name": text})
    assert [r["name"] for r in store.read("items", fresh=True)] == [
        "=1+1", '=HYPERLINK("http://example.com","x")', "+886 2 1234", "-5", "@user"]


def test_empty_rows_are_skipped_and_updates_hit_the_right_sheet_row(store):
    store.append("items", {"item_id": "1", "name": "a"})
    store.update("items", "item_id", "1", {"item_id": "", "name": ""})   # blank out the row
    store.append("items", {"item_id": "2", "name": "b"})
    store.update("items", "item_id", "2", {"note": "n"})
    assert store.read("items", fresh=True) == [{"item_id": "2", "name": "b", "owner_id": "", "note": "n"}]


def test_concurrent_inserts_get_distinct_ids(store):
    ids, errors = [], []

    def work():
        try:
            for _ in range(5):
                ids.append(store.insert("items", {"name": "x"}, "item_id"))
        except Exception as exc:  # noqa: BLE001  (collected and asserted below)
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(ids, key=int) == [str(i) for i in range(1, 21)]


def test_bad_headers_are_refused():
    store = MemoryStorage({"dup": ["a", "a"], "blank": ["a", "", "b"], "none": []})
    for table in ("dup", "blank", "none"):
        with pytest.raises(KitError) as exc:
            store.read(table)
        assert exc.value.key == "storage.bad_header"


def test_reads_may_be_cached_but_writes_always_see_fresh_data():
    sheet = FakeSpreadsheet(TABLES)
    store = SheetsStorage(sheet, production=False, cache_seconds=60)
    store.insert("items", {"name": "a"}, "item_id")
    assert len(store.read("items")) == 1
    # someone adds a row directly in the spreadsheet: a cached read does not see it, an insert does
    sheet.sheets["items"].rows.append(["2", "typed by hand", "", ""])
    assert len(store.read("items")) == 1
    assert store.insert("items", {"name": "b"}, "item_id") == "3"
    assert [r["item_id"] for r in store.read("items")] == ["1", "2", "3"]


def test_sheets_backend_lists_worksheets_only_when_a_table_is_new():
    sheet = FakeSpreadsheet(TABLES)
    store = SheetsStorage(sheet, production=False)
    for _ in range(5):
        store.read("items")
    assert sheet.listings == 1


def test_excel_cells_are_text_on_disk_and_external_edits_are_seen(tmp_path):
    import openpyxl

    path = tmp_path / "data.xlsx"
    store = ExcelStorage.create(path, TABLES)
    store.append("items", {"item_id": "1", "name": "=1+1"})
    cell = openpyxl.load_workbook(path)["items"]["B2"]
    assert (cell.value, cell.data_type) == ("=1+1", "s")
    # edit the file with another program (here openpyxl), then read through the same storage object
    book = openpyxl.load_workbook(path)
    book["items"].append(["2", "added outside", "", ""])
    book.save(path)
    assert [r["name"] for r in store.read("items")] == ["=1+1", "added outside"]
    assert not list(tmp_path.glob("*.tmp"))


def test_production_storage_is_refused_by_test_tools(tmp_path):
    require_test_data(MemoryStorage(TABLES))
    assert SheetsStorage(FakeSpreadsheet(TABLES)).production
    with pytest.raises(KitError) as exc:
        require_test_data(SheetsStorage(FakeSpreadsheet(TABLES)))
    assert exc.value.key == "storage.production_refused"
    with pytest.raises(KitError):
        require_test_data(ExcelStorage.create(tmp_path / "prod.xlsx", TABLES, production=True))


@pytest.mark.parametrize("text, escaped", [("=SUM(A1)", "'=SUM(A1)"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"),
                                           ("\tx", "'\tx"), ("plain", "plain"), ("a=b", "a=b"), ("", "")])
def test_escape_formula_for_csv_exports(text, escaped):
    assert escape_formula(text) == escaped
