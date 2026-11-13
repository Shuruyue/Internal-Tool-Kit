# Changing tables without touching production first

A spreadsheet invites editing production by hand: add a column, rename a sheet, fix a value. Each such edit can break the application for everyone at once, and nothing records it. Treat the structure of the data like code.

## The process

1. **Write a change list** (a short file in the repository, for example `docs/changes/<date>_<topic>.md`): which table, which columns are added, renamed or removed, why, which code depends on them, and how to check the result.
2. **Apply it to the test copy** (an `.xlsx` file for `ExcelStorage`, or a copy of the spreadsheet opened with `SheetsStorage(..., production=False)` and its own service account).
3. **Run the tests and try the change as every kind of person** (see [TESTING.md](TESTING.md)).
4. **Have it approved** by whoever owns the data.
5. **Apply it to production**, in the order the change list gives (new columns before the code that needs them), and record it in the audit log or the change list.

## Rules

- **Code checks for what it needs.** Call `storage.require_columns(table, [...])` (the account store and the audit log do it themselves) so that a missing column stops the feature with a clear message instead of writing to the wrong place.
- **Add, do not repurpose.** A new meaning gets a new column; renaming or reusing one breaks every reader that still expects the old meaning.
- **AI assistants and scripts get the test copy only.** `storage.require_test_data(storage)` lets a tool refuse production.
- **Real data stays out of the repository.** Test data is generated (`fakedata`) or blurred before it is committed.
