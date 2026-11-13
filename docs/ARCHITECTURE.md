# Architecture

The kit is a set of modules, not an application. This page shows how they depend on each other and where an application built with them should put each check.

## Layers of an application

```text
pages / routes / views        draw, read input, show messages                 (yours; no rule lives here)
        │
services                      every rule: who may, what is allowed, what is logged    (yours, using the kit)
        │   roles.require · audit.change · sessions.current_account
        │
storage                       read and write tables                           (internal_tool_kit.storage)
        │
Google Sheets · .xlsx copy · memory
```

- **Pages never decide.** A page may hide a button the user cannot use, but the service that the button calls checks again. Any other path to the same service (another page, a script, a later feature) would otherwise bypass the rule.
- **Services never draw.** They raise `KitError` with a message key; the page turns it into text in the user's language (`exc.text("zh-TW")`).
- **Storage knows no rules.** It guarantees that writes are not lost or duplicated; whether a write is allowed is decided above it.
- **One storage object per process.** Create it once and share it (in Streamlit, inside a function decorated with `st.cache_resource`). The id lock is shared by every storage object of the same spreadsheet or file anyway, but the short-lived copies of tables that save Google Sheets requests belong to one object.

## Each request

1. `sessions.verify(token, ...)` for a remembered device, or `sessions.current_account(id, password_version, session_version, ...)` for a session kept on the server, with data at most `max_age` seconds old (see below). `None` ends the session.
2. If `account.must_change_password`, allow only the change-password service.
3. The service checks `ROLES.require(account, permission)` and any ownership or life-cycle rule of the record.
4. For a change that must be logged: `with log.change(...):` the change. The entry is written first; if the change fails, a `.failed` entry follows.
5. The page shows the result, or `exc.text(language)` of the `KitError`.

## Reads and the Google Sheets quota

Google allows each user of a project 60 read requests and 60 write requests a minute (300 each for the whole project), and a service account is one user, however many people use the application ([Google Sheets API limits](https://developers.google.com/workspace/sheets/api/limits)). A refused request gets HTTP 429; `SheetsStorage` waits and retries reads with truncated exponential backoff, as Google recommends, and retries a write only after a 429, because after a server error the write may already have happened.

What each step costs on Google Sheets:

| Step | Read requests | Write requests |
|---|---|---|
| `sessions.verify` / `current_account` with `store.get(i, fresh=True)` | 1 | 0 |
| the same with `store.get(i, max_age=t)` | 1 every `t` seconds, whoever asks | 0 |
| `login.authenticate` | 1 | 0 (1 more if an old hash is upgraded) |
| `log.record`, or the entry of `log.change` | 1 (the header row only) | 1 |
| `storage.insert` / `storage.update` | 1 | 1 |
| `admin.set_role` (target, entry, change, result) | 4 | 2 |

A page that checks the session on every rerun, with `fresh=True`, uses one read per click, so about 60 clicks a minute across all users, before its own reads. With `max_age=5` the session checks cost at most 12 reads a minute however many people are signed in, and a deactivation or a "sign out of all devices" takes effect within 5 seconds. Pages can read their own tables with `max_age` in the same way; writes always read fresh data.

## Module dependencies

```text
i18n  ◄── every module (messages)
storage
  ▲
  ├── accounts ◄── passwords, audit (break-glass reset)
  │     ▲
  │     ├── login
  │     ├── sessions
  │     ├── roles
  │     └── admin ◄── roles, audit, login (throttle, optional)
  ├── audit
  └── fakedata ◄── accounts, passwords
redaction ◄── ai
```

`redaction` and `ai` depend on nothing else in the kit; `passwords`, `sessions` and `roles` work with any object shaped like `accounts.Account`, so they can be used with another user store.

## Choices and their reasons

| Choice | Reason |
|---|---|
| scrypt from the standard library as the default hash, N = 2^15, r = 8, p = 3 | no dependency to install; one of the settings the OWASP Password Storage Cheat Sheet lists as equivalent to N = 2^17, r = 8, p = 1, with 32 MiB instead of 128 MiB per hash, so a few simultaneous logins cannot exhaust a small server; bcrypt and Argon2id hashes still verify, and every older hash is upgraded on login |
| every cell read back as text | Google Sheets returns text; the same code then behaves the same on every backend |
| integer ids allocated by the storage for records | readable in a spreadsheet; allocated from fresh data under a lock shared by every storage object of the same data |
| audit entries written before the change, with time-and-random ids | a change can never happen unrecorded; writing an entry does not read the log, which only grows |
| one exception type with a message key | services stay free of language; one place holds both languages, and a test checks that every message has both with the same fields |
| no user interface | the kit can be used from any framework, and holds no product decisions |
