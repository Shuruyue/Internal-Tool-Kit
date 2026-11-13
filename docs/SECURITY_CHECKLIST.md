# Security checklist for a spreadsheet-backed internal tool

Go through this before other people use the tool. Each item is a rule and the reason for it; most are holes that appear in real tools because nobody thought of them, not because anyone chose them. Where a module of this kit enforces the rule, it is named.

## Accounts and signing in

- [ ] **Every failed login gets the same answer** ("Incorrect username or password"), whether the username is unknown, the password wrong, the account deactivated or sign-in paused. Otherwise the form tells an attacker which usernames exist. Show the lockout rule as fixed text beside the form, not as "2 attempts left". (`login`)
- [ ] **Unknown usernames are hashed and counted too.** Answering faster for unknown names, or never pausing them, gives the same information away. (`login`)
- [ ] **Repeated failures pause sign-in for a while; they do not lock the account for good.** A permanent lock lets anyone who knows a username shut its owner out, all administrators at once if their names are known. An administrator can lift a pause early. (`login`)
- [ ] **Passwords are hashed with a slow, salted function** (scrypt, Argon2id or bcrypt), never stored or compared in plain text. (`passwords`)
- [ ] **Passwords have an upper length**, and the form says so. bcrypt cannot use more than 72 bytes and recent versions raise an error, which crashes a page that does not expect it. (`passwords`)
- [ ] **Changing one's own password asks for the current one.** An unattended session must not be enough to take over the account. (`login`)
- [ ] **A forgotten password at the top role has a planned way back**: someone outside the application who holds the data credentials issues a temporary password that must be changed at the next login, and the reset is logged. (`accounts.reset_password`)

## Sessions

- [ ] **The account is read again on every request.** Deactivating, demoting or resetting an account must take effect on its open sessions at once, not at its next login. (`sessions.current_account`)
- [ ] **"Stay signed in" tokens name the account by id and carry its password version.** Bound to a username, a token signs in whoever gets that name after a rename; without the version, a changed password leaves old devices signed in. (`sessions`)
- [ ] **Tokens are signed with a long random secret, expire, and are stored in an HttpOnly, Secure cookie** where the framework allows it. (`sessions`)

## Permissions

- [ ] **Permissions are checked where data changes (the service layer), not only by hiding buttons.** A hidden button is not a check: any other path to the same function bypasses it. (`roles.require`)
- [ ] **Accounts manage only accounts below their own level**, and give only roles below their own. Otherwise one manager can deactivate another, or reset the other's password and sign in as them, with the audit log naming the victim. (`roles`)
- [ ] **No account manages itself through the management screens.** Its own password goes through "change password". (`roles`)
- [ ] **Unknown roles get nothing.** A typo in the role column must not grant access. (`roles`)
- [ ] **System and maintenance accounts are hidden** from lists, choices and statistics of ordinary roles, and cannot be managed by them. (`roles.visible`)
- [ ] **Ownership rules are enforced on the server**: a user can create a record only as its owner unless a role allows assigning others; the owner must be an active account of the right role; a child record id must belong to the parent record it is changed through.

## Records and their life cycle

- [ ] **Closed records are read-only**, in the service layer and on the page, except for what closing itself produces. Reopening is a separate action for a higher role, with a reason, and is logged.
- [ ] **Closing checks for unfinished business**: items waiting for someone's confirmation cannot be closed over, or they stay pending forever.
- [ ] **Confirmation dialogs carry the id of the record they were opened for**, and refuse to act if the page has moved to another record. A dialog that keeps its data while the user switches records can apply it to the wrong one.
- [ ] **Editing a record does not silently change fields the user did not touch** (for example setting an end date to today on every save).
- [ ] **Handing a record to another owner asks for confirmation**, keeps the previous owner as a collaborator, and is logged; a manager can reassign records of a deactivated owner.
- [ ] **Deletion is a "void" with a reason**, not a removal, where history matters.

## Audit log

- [ ] **Changes to accounts, roles and record status are always logged**: who, what, which record, when, why. (`audit`)
- [ ] **The log is checked before the change**, so that a missing log blocks the change instead of letting it pass unrecorded. (`audit.ensure_ready`)
- [ ] **The log table is protected** so that only the application's service account can edit it (a protected range in Google Sheets). The application offers no edit or delete. (`audit`)
- [ ] **No password, temporary password, token or key ever goes into the log.**

## The spreadsheet as a database

- [ ] **The sharing list is the real permission boundary.** Anyone who can edit the spreadsheet directly can change a role, a password hash or the log, bypassing every rule of the application; anyone who can view it sees every hash. Share the production spreadsheet with the service account (editor) and one owner only; everyone else uses the application. See [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md).
- [ ] **"Anyone with the link" is off.**
- [ ] **Development, tests and AI assistants use a copy**, never the production spreadsheet. See [DATA_CHANGES.md](DATA_CHANGES.md). (`storage.require_test_data`)
- [ ] **New ids are taken from fresh data under the same lock as the insert.** A cached read lets two users get the same number. (`storage.insert`)
- [ ] **Values are written as text** (Google Sheets: RAW; Excel: string cells), so a user cannot plant a formula; exports to CSV escape formula-like cells. (`storage`, `storage.escape_formula`)
- [ ] **A spreadsheet has no transactions.** Two servers, or a person typing in the sheet, can still race the application. Plan the move to a database when users or data grow.

## Secrets

- [ ] **No key, token or password is in the repository**, in its history, in a zip file next to the code, or in a chat message. `.gitignore` covers `secrets/`, `*.zip`, `.env`, key and credential files.
- [ ] **Keys live in the deployment platform's secret store** and are given to people who need them, not to everyone who clones the code.
- [ ] **A key that was ever committed is rotated**, not just deleted: removing it from the latest commit leaves it in the history and in every clone.
- [ ] **Keys are rotated on a schedule** (Google suggests at most 90 days for service-account keys) and whenever someone with access leaves.

## Pages and output

- [ ] **User input is escaped before it is put into HTML.** Only markup the program generates itself is rendered as HTML.
- [ ] **Real data is blurred before demos, screenshots or AI prompts.** A "sample project" adapted from a real case still names a real client. (`redaction`)
- [ ] **Personal and client data is replaced before text goes to an outside AI service**, and people can see what will be sent. (`redaction`, `ai.prepare`)

## Code and repository

- [ ] **Dependencies are pinned to the versions that were tested**, test tools are kept out of the production requirements, and every package the code imports is listed.
- [ ] **The main branch is protected**: changes arrive by pull request with one review; AI coding assistants push only to feature branches.
- [ ] **Secret scanning and push protection are on** where the hosting service offers them.
- [ ] **Documentation matches the code**: account counts, hashing scheme, rules. Out-of-date documentation leads the next maintainer to wrong conclusions.
