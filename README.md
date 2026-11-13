# Internal Tool Kit

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Independent, tested Python modules for the parts of a small internal tool that are easy to get wrong: signing in, staying signed in, who may do what, an audit log, a spreadsheet as the database, and keeping personal data out of AI prompts.

Small teams often build their internal tools on a spreadsheet and a few pages of Python. The pages are the easy part. The holes are in the parts nobody draws: a deactivated account that keeps working until it logs out, an error message that tells an attacker which usernames exist, a manager who can reset another manager's password and sign in as them, two people getting the same record number, a password kept in a zip file next to the code. Each module here closes one group of such holes and says in its docstring which ones.

The kit draws no user interface and assembles nothing: no pages, no example application. Use the modules you need from Streamlit, Flask, FastAPI or a script. Messages meant for users come in English and Traditional Chinese.

## Modules

| Module | What it does | Holes it closes |
|---|---|---|
| [`storage`](internal_tool_kit/storage/__init__.py) | Tables of rows over Google Sheets, a local `.xlsx` copy or memory, behind one interface | duplicate ids from cached reads; data silently dropped for unknown columns or keys; formula injection (`=HYPERLINK(...)` typed into a field); tools that write fake data into production |
| [`passwords`](internal_tool_kit/passwords.py) | Hashing (scrypt by default, from the standard library; bcrypt and Argon2id readable) and the password policy | weak or unsalted hashes; bcrypt's 72-byte limit crashing a form; no upgrade path for old hashes |
| [`accounts`](internal_tool_kit/accounts.py) | Accounts in a storage table; break-glass password reset by the data owner | the top role locked out with nobody allowed to help |
| [`login`](internal_tool_kit/login.py) | Checking a login, a temporary pause after repeated failures, changing one's password | answers that reveal which usernames exist; permanent lockouts anyone can trigger; password changes without the current password |
| [`sessions`](internal_tool_kit/sessions.py) | Signed "stay signed in" tokens and re-checking the account on every request | sessions surviving deactivation, demotion or a password reset; tokens bound to a username that is later given to someone else |
| [`roles`](internal_tool_kit/roles.py) | Roles, permissions, who may manage whom, hidden system accounts | managers managing their peers or themselves; giving a role as high as one's own; unknown roles getting access |
| [`audit`](internal_tool_kit/audit.py) | An append-only log, checked before the change it records | changes made while the log is missing; nothing to show who changed what |
| [`admin`](internal_tool_kit/admin.py) | Creating accounts, changing roles, deactivating, resetting passwords, all through the checks above | each of the above, in one place |
| [`redaction`](internal_tool_kit/redaction.py), [`ai`](internal_tool_kit/ai.py) | Placeholders for e-mails, phone numbers, Taiwanese IDs, tax and card numbers and given names before text goes to an AI service; originals restored in the answer | personal or client data sent to an outside model |
| [`fakedata`](internal_tool_kit/fakedata.py) | One account per kind of person, uneven load, seeded tables | testing only as the developer; stress data that is too even to break anything |
| [`i18n`](internal_tool_kit/i18n.py) | Every user-facing message in English and Traditional Chinese | — |

How they depend on each other, and where the checks belong in an application: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Install

```bash
pip install -e ".[excel,sheets]"     # from a clone; the core needs only the standard library
pip install -e ".[dev]"              # everything, plus pytest
```

Extras: `excel` (openpyxl), `sheets` (gspread, google-auth), `bcrypt`, `argon2`.

## Using the modules

Signing in and staying signed in:

```python
from internal_tool_kit import accounts, login, sessions
from internal_tool_kit.i18n import KitError
from internal_tool_kit.storage.sheets import SheetsStorage

storage = SheetsStorage.from_service_account(service_account_info, spreadsheet_key)  # from the secret store
store = accounts.AccountStore(storage)
throttle = login.LoginThrottle(attempts=5, minutes=15)

try:
    result = login.authenticate(username, password, find_account=store.find_by_username, throttle=throttle)
except KitError as exc:
    show_error(exc.text("zh-TW"))            # "帳號或密碼錯誤。", whatever went wrong
    show_note(throttle.rule().text("zh-TW"))  # the fixed lockout rule, never a countdown
else:
    if result.new_hash:
        store.update(result.account.account_id, password_hash=result.new_hash)
    token = sessions.issue(result.account, cookie_secret)   # keep in an HttpOnly, Secure cookie
```

On every request (every rerun of a page), before anything else:

```python
account = sessions.verify(token, cookie_secret, get_account=lambda i: store.get(i, fresh=True))
if account is None:
    sign_out()                                # deactivated, password changed, expired or forged
```

In the service layer, where data changes:

```python
ROLES.require(account, "items.write")        # a roles.Roles; raises KitError("roles.forbidden")
log.ensure_ready()                           # an audit.AuditLog: no change without a record
storage.update("items", "item_id", item_id, {"status": "closed"})
log.record(account.account_id, "item.close", "items", item_id, reason)
```

Each module's docstring explains its rules and their reasons; the tests show every rule in use.

## Documentation

| Document | Contents |
|---|---|
| [docs/SECURITY_CHECKLIST.md](docs/SECURITY_CHECKLIST.md) | What to check in a spreadsheet-backed internal tool before others use it |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, where each check goes, how the modules depend on each other |
| [docs/KEY_MANAGEMENT.md](docs/KEY_MANAGEMENT.md) | Service-account keys, signing secrets, the spreadsheet's sharing list, rotation |
| [docs/DATA_CHANGES.md](docs/DATA_CHANGES.md) | Changing tables and columns without touching production first |
| [docs/TESTING.md](docs/TESTING.md) | Testing as every kind of person, stress data, checking that tests can fail |

## Tests

```bash
pytest -q        # about 170 tests, under ten seconds; no network, no credentials
ruff check .
```

The storage tests run the same suite on all three backends; Google Sheets is replaced by a stand-in that fails any write not made RAW. CI runs on Ubuntu and Windows with Python 3.10 and 3.12.

## Limits

- The login pause and the id lock live in one process. With several servers, pass a shared store to `LoginThrottle` and move the data to a database; a spreadsheet has no transactions.
- Redaction finds patterns and the names it is given. It cannot find a name nobody listed; show people what will be sent when that matters.
- Nothing here replaces protecting the spreadsheet itself: whoever can edit it directly can bypass every rule (see [docs/KEY_MANAGEMENT.md](docs/KEY_MANAGEMENT.md)).

## License

[MIT](LICENSE)
