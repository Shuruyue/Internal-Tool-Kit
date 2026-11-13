# Internal Tool Kit

[![CI](https://github.com/Shuruyue/Internal-Tool-Kit/actions/workflows/ci.yml/badge.svg)](https://github.com/Shuruyue/Internal-Tool-Kit/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Independent, tested Python modules for the parts of a small internal tool that are easy to get wrong: signing in, staying signed in, who may do what, an audit log, a spreadsheet as the database, and keeping personal data out of AI prompts.

**Status: complete (v0.1.0).** The repository is archived and read-only; fork it to adapt the modules.

Small teams often build their internal tools on a spreadsheet and a few pages of Python. The pages are the easy part. The holes are in the parts nobody draws: a deactivated account that keeps working until it logs out, an error message that tells an attacker which usernames exist, a manager who can reset another manager's password and sign in as them, two people getting the same record number, a page that rereads the whole spreadsheet so often that the API quota runs out, a service-account key that travels with every copy of the code. Each module here closes one group of such holes and says in its docstring which ones.

The kit draws no user interface and assembles nothing: no pages, no example application. Use the modules you need from Streamlit, Flask, FastAPI or a script. Messages meant for users come in English and Traditional Chinese.

## Modules

| Module | What it does | Holes it closes |
|---|---|---|
| [`storage`](internal_tool_kit/storage/__init__.py) | Tables of rows over Google Sheets, a local `.xlsx` copy or memory, behind one interface | duplicate ids from cached reads or from one storage object per user session; data silently dropped for unknown columns or keys; formula injection (`=HYPERLINK(...)` typed into a field); the Sheets read quota used up by repeated reads; tools that write fake data into production |
| [`passwords`](internal_tool_kit/passwords.py) | Hashing (scrypt by default, from the standard library, 32 MiB per hash; bcrypt and Argon2id readable) and the password policy | weak or unsalted hashes; a few login attempts exhausting a small server's memory; bcrypt's 72-byte limit crashing a form; no upgrade path for old hashes |
| [`accounts`](internal_tool_kit/accounts.py) | Accounts in a storage table; renaming; signing out of all devices; break-glass password reset by the data owner, recorded in the audit log | the top role locked out with nobody allowed to help; usernames changed without the checks a new one gets |
| [`login`](internal_tool_kit/login.py) | Checking a login, a temporary pause after repeated failures (per username and per client), changing one's password | answers that reveal which usernames exist; permanent lockouts anyone can trigger; one password tried on every username; password changes without the current password, or by guessing it |
| [`sessions`](internal_tool_kit/sessions.py) | Signed "stay signed in" tokens and re-checking the account on every request | sessions surviving deactivation, demotion, a password reset or "sign out of all devices"; tokens bound to a username that is later given to someone else |
| [`roles`](internal_tool_kit/roles.py) | Roles, permissions, who may manage whom, hidden system accounts | managers managing their peers or themselves; giving a role as high as one's own; unknown roles getting access |
| [`audit`](internal_tool_kit/audit.py) | An append-only log, written before the change it records | changes made while the log is missing or unwritable; nothing to show who changed what |
| [`admin`](internal_tool_kit/admin.py) | Creating, renaming, changing roles, deactivating, resetting passwords, signing out, all through the checks above | each of the above, in one place |
| [`redaction`](internal_tool_kit/redaction.py), [`ai`](internal_tool_kit/ai.py) | Placeholders for e-mails, phone numbers, Taiwanese IDs, tax and card numbers and given names before text goes to an AI service; originals restored in the answer | personal or client data sent to an outside model |
| [`fakedata`](internal_tool_kit/fakedata.py) | One account per kind of person, uneven load, seeded tables | testing only as the developer; stress data that is too even to break anything |
| [`i18n`](internal_tool_kit/i18n.py) | Every user-facing message in English and Traditional Chinese | — |

How they depend on each other, where the checks belong in an application, and how many Google Sheets requests each step costs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Install

```bash
pip install "internal-tool-kit[excel,sheets] @ git+https://github.com/Shuruyue/Internal-Tool-Kit@v0.1.0"
pip install -e ".[dev]"              # from a clone: everything, plus pytest
```

The core needs only the standard library. Extras: `excel` (openpyxl), `sheets` (gspread, google-auth), `bcrypt`, `argon2`.

## Using the modules

Create the storage once per process and share it (in Streamlit, inside a function decorated with `st.cache_resource`), so that its short-lived copies of tables are shared too:

```python
from internal_tool_kit import accounts, login, sessions
from internal_tool_kit.i18n import KitError
from internal_tool_kit.storage.sheets import SheetsStorage

storage = SheetsStorage.from_service_account(service_account_info, spreadsheet_key)  # from the secret store
store = accounts.AccountStore(storage)
throttle = login.LoginThrottle(attempts=5, minutes=15)
```

Signing in:

```python
try:
    result = login.authenticate(username, password, find_account=store.find_by_username, throttle=throttle,
                                client=client_ip)    # optional: also pauses one place trying many usernames
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
    sign_out()          # deactivated, password changed, signed out of all devices, expired or forged
```

`fresh=True` reads the accounts table on every request: one Google Sheets request each time, against a quota of 60 a minute for the whole application. With many people at once, use `max_age=5` instead: the table is then read at most every 5 seconds, and a deactivation takes effect within 5 seconds. "Sign out of all devices" is `store.end_sessions(account.account_id)`.

In the service layer, where data changes:

```python
ROLES.require(account, "items.write")        # a roles.Roles; raises KitError("roles.forbidden")
with log.change(account.account_id, "item.close", "items", item_id, reason):   # an audit.AuditLog
    storage.update("items", "item_id", item_id, {"status": "closed"})          # recorded before it runs
```

Each module's docstring explains its rules and their reasons; the tests show every rule in use.

## Documentation

| Document | Contents |
|---|---|
| [docs/SECURITY_CHECKLIST.md](docs/SECURITY_CHECKLIST.md) | What to check in a spreadsheet-backed internal tool before others use it |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, where each check goes, how the modules depend on each other, the Google Sheets quota |
| [docs/KEY_MANAGEMENT.md](docs/KEY_MANAGEMENT.md) | Service-account keys, signing secrets, the spreadsheet's sharing list, rotation |
| [docs/DATA_CHANGES.md](docs/DATA_CHANGES.md) | Changing tables and columns without touching production first |
| [docs/TESTING.md](docs/TESTING.md) | Testing as every kind of person, stress data, checking that tests can fail |

## Tests

```bash
pytest -q        # about 210 tests, under ten seconds; no network, no credentials
ruff check .
```

The storage tests run the same suite on all three backends; Google Sheets is replaced by a stand-in that counts API requests, can answer with quota and server errors, and fails any write not made RAW. CI runs on Ubuntu and Windows with Python 3.10 and 3.12.

## Limits

- The login pause and the id lock live in one process (the lock is shared by every storage object of the same spreadsheet or file). With several servers, pass a shared store to `LoginThrottle` and move the data to a database; a spreadsheet has no transactions.
- Redaction finds patterns and the names it is given. It cannot find a name nobody listed; show people what will be sent when that matters.
- Nothing here replaces protecting the spreadsheet itself: whoever can edit it directly can bypass every rule (see [docs/KEY_MANAGEMENT.md](docs/KEY_MANAGEMENT.md)).

## License

[MIT](LICENSE)
