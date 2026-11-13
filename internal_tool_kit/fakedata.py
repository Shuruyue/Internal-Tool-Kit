"""Fake data for tests, demonstrations and stress tests. Refuses to write to production storage.

Two things make test data useful beyond "the page opens":

- **One account per kind of person.** :func:`persona_accounts` creates, for every role, an active account, plus
  an inactive one, so each rule can be tried as everyone it concerns (the owner of a record, a colleague, an
  unrelated user, a manager, a deactivated account).
- **Uneven load.** Real work is not spread evenly: a few people carry most of it, deadlines bunch up, some items
  are overdue. :func:`skewed_choice` picks with Zipf-like weights, so a stress data set has the extreme cases that
  break charts and totals.

Everything is generated from a seed, so a failing test can be run again on the same data.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable, Sequence

from internal_tool_kit import passwords
from internal_tool_kit.accounts import Account, AccountStore
from internal_tool_kit.storage import Storage, require_test_data

FAKE_MARK = "fake data"


def skewed_choice(rng: random.Random, items: Sequence, exponent: float = 1.2):
    """An item drawn with weight 1 / rank^exponent: the first items are picked much more often."""
    weights = [1 / (i + 1) ** exponent for i in range(len(items))]
    return rng.choices(list(items), weights=weights, k=1)[0]


def date_between(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=rng.randint(0, max((end - start).days, 0)))


def persona_accounts(store: AccountStore, roles: Sequence[str], password: str, *, hasher=None) -> dict[str, Account]:
    """For every role an active account ``<role>1`` and one more ``<role>2``, and an inactive ``inactive``
    account with the first role; all with ``password`` and no forced change. Returns {username: account}."""
    require_test_data(store.storage)
    hashed = passwords.hash_password(password, hasher)
    made = {}
    for role in roles:
        for n in (1, 2):
            a = store.create(f"{role}{n}", f"Test {role.title()} {n}", role, hashed, must_change_password=False)
            made[a.username] = a
    inactive = store.create("inactive", "Test Inactive", roles[0], hashed, must_change_password=False)
    made["inactive"] = store.update(inactive.account_id, active=False)
    return made


def fill_table(storage: Storage, table: str, count: int, columns: dict[str, Callable[[random.Random, int], object]],
               *, seed: int = 0, id_column: str | None = None) -> list[dict[str, str]]:
    """Append ``count`` rows whose values come from ``columns`` (column -> function of (rng, row index)).
    With ``id_column`` the rows get increasing ids. Returns the rows as stored."""
    require_test_data(storage)
    rng = random.Random(seed)  # noqa: S311  (reproducible fake data, not secrets; secrets use the secrets module)
    rows = []
    for i in range(count):
        row = {c: f(rng, i) for c, f in columns.items()}
        if id_column:
            storage.insert(table, row, id_column)
        else:
            storage.append(table, row)
        rows.append(row)
    return storage.read(table, fresh=True)[-count:] if count else []
