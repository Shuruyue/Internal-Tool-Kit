# Architecture

The kit is a set of modules, not an application. This page shows how they depend on each other and where an application built with them should put each check.

## Layers of an application

```text
pages / routes / views        draw, read input, show messages                 (yours; no rule lives here)
        │
services                      every rule: who may, what is allowed, what is logged    (yours, using the kit)
        │   roles.require · audit.ensure_ready / record · sessions.current_account
        │
storage                       read and write tables                           (internal_tool_kit.storage)
        │
Google Sheets · .xlsx copy · memory
```

- **Pages never decide.** A page may hide a button the user cannot use, but the service that the button calls checks again. Any other path to the same service (another page, a script, a later feature) would otherwise bypass the rule.
- **Services never draw.** They raise `KitError` with a message key; the page turns it into text in the user's language (`exc.text("zh-TW")`).
- **Storage knows no rules.** It guarantees that writes are not lost or duplicated; whether a write is allowed is decided above it.

## Each request

1. `sessions.current_account(...)` (or `sessions.verify(token, ...)` for a remembered device) with fresh data. `None` ends the session.
2. If `account.must_change_password`, allow only the change-password service.
3. The service checks `ROLES.require(account, permission)` and any ownership or life-cycle rule of the record.
4. For a change that must be logged: `log.ensure_ready()`, the change, `log.record(...)` (or `with log.change(...)`).
5. The page shows the result, or `exc.text(language)` of the `KitError`.

## Module dependencies

```text
i18n  ◄── every module (messages)
storage
  ▲
  ├── accounts ◄── passwords
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
| scrypt from the standard library as the default hash | no dependency to install; parameters as OWASP recommends; bcrypt and Argon2id hashes still verify, and are upgraded on login |
| every cell read back as text | Google Sheets returns text; the same code then behaves the same on every backend |
| integer ids allocated by the storage | readable in a spreadsheet; allocated from fresh data under a lock |
| one exception type with a message key | services stay free of language; one place holds both languages, and a test checks that every message has both with the same fields |
| no user interface | the kit can be used from any framework, and holds no product decisions |
