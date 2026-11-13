import pytest

from internal_tool_kit import ai
from internal_tool_kit.redaction import Redactor, valid_luhn, valid_tax_id, valid_tw_id


@pytest.mark.parametrize("text, ok", [("A123456789", True), ("A123456788", False), ("a123456789", False),
                                      ("A323456789", False), ("A12345678", False)])
def test_taiwan_id_check_digit(text, ok):
    assert valid_tw_id(text) is ok


@pytest.mark.parametrize("text, ok", [("04595257", True), ("04595258", False), ("1234567", False)])
def test_tax_id_check_digit(text, ok):
    assert valid_tax_id(text) is ok


def test_tax_id_seventh_digit_seven_may_count_either_way():
    # with 7 in the 7th place the product 28 counts as 0 or 1; find one number that needs the "+1" branch
    hits = [f"{n:08d}" for n in range(10_000_000, 10_020_000) if f"{n:08d}"[6] == "7" and valid_tax_id(f"{n:08d}")]
    plain = [h for h in hits if sum(sum(divmod(int(d) * w, 10)) for d, w in zip(h, (1, 2, 1, 2, 1, 2, 4, 1),
                                                                                  strict=True)) % 5 == 0]
    assert len(hits) > len(plain) > 0


def test_luhn():
    assert valid_luhn("4111111111111111") and not valid_luhn("4111111111111112")


def kinds(text, **terms):
    return Redactor(terms).redact(text).kinds


@pytest.mark.parametrize("text, kind", [
    ("mail anna.lee+work@example.co.uk now", "EMAIL"),
    ("手機 0912-345-678", "PHONE"), ("手機0912345678。", "PHONE"), ("call +886 912 345 678", "PHONE"),
    ("市話 (02) 2345-6789", "PHONE"), ("市話 02-2345-6789", "PHONE"), ("tel 04 2345 6789", "PHONE"),
    ("UK +44 20 7946 0958", "PHONE"),
    ("身分證A123456789號", "TWID"), ("居留證 A800000014", "TWID"),
    ("統編：04595257", "TAXID"), ("統一編號 04595257", "TAXID"), ("VAT No. 04595257", "TAXID"),
    ("card 4111 1111 1111 1111", "CARD"), ("4111-1111-1111-1111", "CARD"),
])
def test_each_kind_is_detected(text, kind):
    assert kinds(text) == {kind: 1}


@pytest.mark.parametrize("text", [
    "meeting on 2026-11-13 at 14:30", "budget 12345678 NTD", "order 04595257 shipped",   # bare 8 digits: not a tax id
    "version 3.12.16", "A123456788 is not an ID", "room B1-203", "4111 1111 1111 1112",
    "progress 70% by week 46", "ISO 9001:2015", "the ratio 0.912345678",
])
def test_ordinary_text_is_left_alone(text):
    assert kinds(text) == {}


def test_same_value_same_placeholder_and_restore():
    r = Redactor({"NAME": ["王小明", "Lin Mei"]}).redact(
        "王小明 (wang@example.com) met LIN MEI; 王小明 will mail wang@example.com again.")
    assert r.text == "[NAME_1] ([EMAIL_1]) met [NAME_2]; [NAME_1] will mail [EMAIL_1] again."
    assert r.restore("Thank [NAME_1] and [NAME_2] at [EMAIL_1].") == "Thank 王小明 and LIN MEI at wang@example.com."
    assert r.kinds == {"NAME": 2, "EMAIL": 1}


def test_longer_terms_win_and_terms_beat_patterns_on_the_same_span():
    r = Redactor({"CLIENT": ["Acme", "Acme Taiwan Ltd"]}).redact("Acme Taiwan Ltd and Acme")
    assert r.text == "[CLIENT_1] and [CLIENT_2]"
    r = Redactor({"NAME": ["anna@example.com"]}).redact("write anna@example.com")
    assert r.kinds == {"NAME": 1}


def test_placeholders_already_in_the_text_are_not_reused_or_restored():
    r = Redactor().redact("template [EMAIL_1] stays; real one bob@example.com")
    assert r.text == "template [EMAIL_1] stays; real one [EMAIL_2]"
    assert r.restore(r.text) == "template [EMAIL_1] stays; real one bob@example.com"


def test_summary_is_bilingual():
    r = Redactor({"NAME": ["王小明"]}).redact("王小明 0912345678")
    assert r.summary().text("en") == "2 item(s) were replaced before sending: NAME 1, PHONE 1."
    assert r.summary().text("zh-TW") == "送出前已替換 2 項資料：NAME 1, PHONE 1。"
    assert Redactor().redact("nothing here").summary().text("zh-TW") == "送出前沒有需要替換的資料。"


def test_term_kinds_must_be_upper_case_letters():
    with pytest.raises(ValueError):
        Redactor({"name": ["x"]})


class Recorder:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, prompt, *, system=None):
        self.calls.append((prompt, system))
        return self.reply


def test_the_provider_never_sees_the_originals_and_the_answer_gets_them_back():
    provider = Recorder("Dear [NAME_1], we will call [PHONE_1].")
    redactor = Redactor({"NAME": ["王小明"], "CLIENT": ["Acme"]})
    answer = ai.ask(provider, "Write to 王小明 of Acme, phone 0912-345-678.", redactor=redactor,
                    system="You assist 王小明's team.")
    prompt, system = provider.calls[0]
    for secret in ("王小明", "Acme", "0912-345-678"):
        assert secret not in prompt and secret not in system
    assert system == "You assist [NAME_1]'s team."      # same person, same placeholder in both texts
    assert answer.text == "Dear 王小明, we will call 0912-345-678."


def test_prepare_then_send_lets_a_person_check_what_leaves():
    out = ai.prepare("Ask anna@example.com", redactor=Redactor())
    assert out.prompt == "Ask [EMAIL_1]" and out.system is None
    provider = Recorder("Asked [EMAIL_1].")
    assert ai.send(provider, out).text == "Asked anna@example.com."
    assert provider.calls == [("Ask [EMAIL_1]", None)]
