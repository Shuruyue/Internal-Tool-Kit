import time

import pytest

from internal_tool_kit import passwords
from internal_tool_kit.i18n import KitError
from internal_tool_kit.passwords import Argon2Hasher, BcryptHasher, ScryptHasher

from .conftest import FAST


def test_default_scrypt_parameters_are_owasps_and_a_default_hash_round_trips():
    default = ScryptHasher()
    assert (default.n, default.r, default.p) == (2 ** 17, 8, 1)
    stored = default.hash("correct horse 1")
    assert stored.startswith("scrypt$131072$8$1$")
    assert default.verify("correct horse 1", stored)
    assert not default.verify("correct horse 2", stored)


def test_scrypt_round_trip_salt_and_rehash():
    a, b = FAST.hash("pw12345678"), FAST.hash("pw12345678")
    assert a != b                                        # random salt
    assert passwords.verify_password("pw12345678", a) and passwords.verify_password("pw12345678", b)
    assert not passwords.verify_password("pw12345679", a)
    assert not FAST.needs_rehash(a)
    assert ScryptHasher(n=2 ** 11).needs_rehash(a)


@pytest.mark.parametrize("stored", ["", "scrypt$", "scrypt$x$8$1$aa$bb", "scrypt$1024$8$1$!!$??", "md5$abc",
                                    "plaintext-password"])
def test_malformed_or_unknown_hashes_never_match(stored):
    assert not passwords.verify_password("plaintext-password", stored)


def test_parameters_that_would_exhaust_memory_are_refused_quickly():
    stored = FAST.hash("pw12345678").replace("scrypt$1024$", f"scrypt${2 ** 30}$")
    t0 = time.perf_counter()
    assert not passwords.verify_password("pw12345678", stored)
    assert time.perf_counter() - t0 < 0.5


def test_bcrypt_hashes_are_recognized_and_long_passwords_refused():
    h = BcryptHasher(rounds=4)
    stored = h.hash("pw12345678")
    assert stored.startswith("$2b$04$")
    assert passwords.verify_password("pw12345678", stored)
    assert passwords.needs_rehash(stored)                # another format than the default: upgrade on login
    assert BcryptHasher(rounds=12).needs_rehash(stored)
    with pytest.raises(KitError) as exc:
        h.hash("a1" * 37)                                # 74 bytes
    assert exc.value.key == "password.too_long"
    assert not h.verify("a1" * 37, stored)


def test_argon2_hashes_are_recognized():
    stored = Argon2Hasher().hash("pw12345678")
    assert stored.startswith("$argon2id$")
    assert passwords.verify_password("pw12345678", stored)
    assert not passwords.verify_password("pw12345670", stored)


@pytest.mark.parametrize("password, key", [("a1b2c3", "password.too_short"),
                                            ("密碼" * 12 + "a1", "password.too_long"),
                                            ("abcdefghij", "password.needs_letter_and_digit"),
                                            ("1234567890", "password.needs_letter_and_digit"),
                                            ("xAnna2026x", "password.contains_username")])
def test_policy_names_the_broken_rule(password, key):
    with pytest.raises(KitError) as exc:
        passwords.check_policy(password, username="anna")
    assert exc.value.key == key


def test_policy_accepts_a_good_password_and_counts_bytes_not_characters():
    passwords.check_policy("river7stone", username="anna")
    passwords.check_policy("密碼" * 11 + "a1")           # 68 bytes
    with pytest.raises(KitError):
        passwords.check_policy("密碼" * 12 + "a1")       # 74 bytes


def test_temporary_passwords_meet_the_policy_and_avoid_confusable_characters():
    seen = set()
    for _ in range(200):
        pw = passwords.temporary_password()
        passwords.check_policy(pw)
        assert not set(pw) & set("IlO01")
        seen.add(pw)
    assert len(seen) == 200
