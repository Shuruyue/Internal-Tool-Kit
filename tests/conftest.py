import pytest

from internal_tool_kit import i18n, login, passwords

FAST = passwords.ScryptHasher(n=2 ** 10)


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """Tests hash with small scrypt parameters (the default takes a few hundred milliseconds per hash);
    test_passwords checks the default parameters separately."""
    monkeypatch.setattr(passwords, "DEFAULT_HASHER", FAST)
    monkeypatch.setattr(login, "_dummy_hash", None)
    i18n.set_default_language("en")
    yield
    i18n.set_default_language("en")
