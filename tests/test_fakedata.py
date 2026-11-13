import datetime as dt
import random
from collections import Counter

import pytest

from internal_tool_kit import accounts, fakedata, login
from internal_tool_kit.accounts import AccountStore
from internal_tool_kit.i18n import KitError
from internal_tool_kit.storage import MemoryStorage
from internal_tool_kit.storage.sheets import SheetsStorage

from .fake_gspread import FakeSpreadsheet


def test_personas_cover_every_role_and_an_inactive_account_and_can_log_in():
    store = AccountStore(MemoryStorage({"accounts": accounts.COLUMNS}))
    made = fakedata.persona_accounts(store, ["member", "manager", "owner"], "Test1234pass")
    assert sorted(made) == ["inactive", "manager1", "manager2", "member1", "member2", "owner1", "owner2"]
    assert not made["inactive"].active
    throttle = login.LoginThrottle()
    assert login.authenticate("owner2", "Test1234pass", find_account=store.find_by_username, throttle=throttle)
    with pytest.raises(KitError):
        login.authenticate("inactive", "Test1234pass", find_account=store.find_by_username, throttle=throttle)


def test_production_storage_is_refused():
    production = SheetsStorage(FakeSpreadsheet({"accounts": accounts.COLUMNS, "items": ["item_id", "x"]}))
    with pytest.raises(KitError) as exc:
        fakedata.persona_accounts(AccountStore(production), ["member"], "Test1234pass")
    assert exc.value.key == "storage.production_refused"
    with pytest.raises(KitError):
        fakedata.fill_table(production, "items", 3, {"x": lambda rng, i: i})
    assert production.read("items") == [] and production.read("accounts") == []


def test_skewed_choice_puts_most_of_the_load_on_a_few():
    rng = random.Random(1)
    people = ["p1", "p2", "p3", "p4", "p5", "p6"]
    counts = Counter(fakedata.skewed_choice(rng, people) for _ in range(6000))
    assert counts["p1"] > 2 * counts["p3"] > 0 and counts["p1"] > 4 * counts["p6"]


def test_fill_table_is_reproducible_from_its_seed():
    def make():
        storage = MemoryStorage({"items": ["item_id", "owner", "due", "note"]})
        start, end = dt.date(2026, 1, 1), dt.date(2026, 12, 31)
        return fakedata.fill_table(storage, "items", 20, {
            "owner": lambda rng, i: fakedata.skewed_choice(rng, ["a", "b", "c"]),
            "due": lambda rng, i: fakedata.date_between(rng, start, end).isoformat(),
            "note": lambda rng, i: fakedata.FAKE_MARK}, seed=7, id_column="item_id")
    first, second = make(), make()
    assert first == second and len(first) == 20
    assert [r["item_id"] for r in first] == [str(i) for i in range(1, 21)]
    assert all("2026-01-01" <= r["due"] <= "2026-12-31" for r in first)
