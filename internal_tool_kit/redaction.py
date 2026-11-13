"""Replace personal and confidential data with placeholders before text leaves for an outside service (an AI
model, a translation API), and put the originals back into the answer.

Detected automatically:

========  =====================================================================================================
EMAIL     e-mail addresses
PHONE     Taiwanese mobile and landline numbers, and international numbers written with ``+``
TWID      Taiwanese national ID and resident certificate numbers, only when the check digit is right
TAXID     Taiwanese unified business numbers (統一編號), only after a label such as 統編, 統一編號, VAT or Tax ID
          and with a valid check digit (any 8 digits pass the check one time in five, so a bare number is not one)
CARD      payment card numbers of 13 to 19 digits that pass the Luhn check
========  =====================================================================================================

**Names cannot be found reliably by pattern**, in Chinese or in English. Pass every name the application knows
(people, clients, projects, addresses) as ``terms``; they are replaced wherever they occur, ignoring case. A term
that begins or ends with a Latin letter or digit must stand as a whole word there ("Lin" is not found in "Linux"
or "Berlin"); Chinese has no spaces between words, so a Chinese term is found inside longer text too. Anything
neither detected nor listed is sent as it is: show the user the redacted text before sending when that matters.

The same value always becomes the same placeholder (``[EMAIL_1]``), so the model can still tell two people
apart; placeholders already present in the text are never reused.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from internal_tool_kit.i18n import Message, register

register("redaction.summary", "{count} item(s) were replaced before sending: {kinds}.",
         "送出前已替換 {count} 項資料：{kinds}。")
register("redaction.none", "Nothing was replaced before sending.", "送出前沒有需要替換的資料。")

_NOT_ALNUM_BEFORE, _NOT_ALNUM_AFTER = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_PLACEHOLDER = re.compile(r"\[([A-Z]+)_(\d+)\]")

_ID_LETTERS = {"A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17, "I": 34, "J": 18, "K": 19,
               "L": 20, "M": 21, "N": 22, "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28, "V": 29,
               "W": 32, "X": 30, "Y": 31, "Z": 33}


def valid_tw_id(text: str) -> bool:
    """Check digit of a Taiwanese national ID (second character 1 or 2) or resident certificate (8 or 9)."""
    if not re.fullmatch(r"[A-Z][1289]\d{8}", text):
        return False
    code = _ID_LETTERS[text[0]]
    total = code // 10 + (code % 10) * 9
    total += sum(int(d) * w for d, w in zip(text[1:9], (8, 7, 6, 5, 4, 3, 2, 1), strict=True)) + int(text[9])
    return total % 10 == 0


def valid_tax_id(text: str) -> bool:
    """Check digit of a Taiwanese unified business number (the rule since 2023: divisible by 5)."""
    if not re.fullmatch(r"\d{8}", text):
        return False
    sums = [sum(divmod(int(d) * w, 10)) for d, w in zip(text, (1, 2, 1, 2, 1, 2, 4, 1), strict=True)]
    total = sum(sums)
    if total % 5 == 0:
        return True
    return text[6] == "7" and (total + 1) % 5 == 0      # the 7th digit 7: its product 28 may count as 0 or 1


def valid_luhn(digits: str) -> bool:
    total, double = 0, False
    for d in reversed(digits):
        n = int(d) * (2 if double else 1)
        total += n - 9 if n > 9 else n
        double = not double
    return total % 10 == 0


@dataclass(frozen=True)
class _Pattern:
    kind: str
    regex: re.Pattern
    group: int = 0
    check: object = None          # callable(normalized text) -> bool


PATTERNS = [
    _Pattern("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    _Pattern("TWID", re.compile(_NOT_ALNUM_BEFORE + r"[A-Z][1289]\d{8}" + _NOT_ALNUM_AFTER), check=valid_tw_id),
    _Pattern("TAXID", re.compile(r"(?:統一編號|統編|VAT(?:\s*(?:No\.?|number))?|Tax\s*ID)\s*[:：#]?\s*(\d{8})(?!\d)",
                                 re.IGNORECASE), group=1, check=valid_tax_id),
    _Pattern("CARD", re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)"),
             check=lambda s: 13 <= len(re.sub(r"\D", "", s)) <= 19 and valid_luhn(re.sub(r"\D", "", s))),
    _Pattern("PHONE", re.compile(
        r"(?<![\d+])(?:\+886[-\s]?9\d{2}[-\s]?\d{3}[-\s]?\d{3}"       # +886 mobile
        r"|09\d{2}[-\s]?\d{3}[-\s]?\d{3}"                              # mobile
        r"|\(0\d{1,2}\)\s?\d{3,4}[-\s]?\d{4}|0\d{1,2}[-\s]\d{3,4}[-\s]?\d{4}"  # landline: (02) 2345-6789, 02-2345-6789
        r"|\+\d{1,3}(?:[-\s]?\d{1,4}){2,5})(?!\d)")),                  # international with +
]


def _term_regex(value: str) -> re.Pattern:
    """``value`` ignoring case; a Latin letter or digit at either end must not continue into the text around it."""
    before = _NOT_ALNUM_BEFORE if value[0].isascii() and value[0].isalnum() else ""
    after = _NOT_ALNUM_AFTER if value[-1].isascii() and value[-1].isalnum() else ""
    return re.compile(before + re.escape(value) + after, re.IGNORECASE)


@dataclass
class Redacted:
    """The text to send, and how to turn placeholders back into the originals."""

    text: str
    mapping: dict[str, str] = field(default_factory=dict)       # placeholder -> original
    kinds: dict[str, int] = field(default_factory=dict)         # kind -> number of distinct values

    def restore(self, text: str) -> str:
        """Put the originals back into ``text`` (for example the model's answer)."""
        return _PLACEHOLDER.sub(lambda m: self.mapping.get(m.group(0), m.group(0)), text)

    def summary(self) -> Message:
        if not self.mapping:
            return Message("redaction.none")
        return Message("redaction.summary", {"count": len(self.mapping),
                                             "kinds": ", ".join(f"{k} {n}" for k, n in sorted(self.kinds.items()))})


class Redactor:
    def __init__(self, terms: Mapping[str, Iterable[str]] | None = None, *, patterns: Iterable[_Pattern] = PATTERNS):
        """``terms``: kind -> known values, for example ``{"NAME": ["王小明", "Lin Mei"], "CLIENT": ["Acme"]}``.
        Kinds are upper-case letters; values are matched ignoring case, the longest first."""
        self.patterns = list(patterns)
        terms_found: list[tuple[str, str]] = []
        for kind, values in (terms or {}).items():
            if not re.fullmatch(r"[A-Z]+", kind):
                raise ValueError(f"term kind {kind!r} must be upper-case letters")
            terms_found += [(kind, v.strip()) for v in values if v and v.strip()]
        terms_found.sort(key=lambda kv: -len(kv[1]))
        self.terms: list[tuple[str, re.Pattern]] = [(kind, _term_regex(v)) for kind, v in terms_found]

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        found = []
        for kind, regex in self.terms:       # matched on the text itself: lower() may change its length
            found += [(m.start(), m.end(), kind) for m in regex.finditer(text)]
        for p in self.patterns:
            for m in p.regex.finditer(text):
                s, e = m.span(p.group)
                if p.check is None or p.check(text[s:e]):
                    found.append((s, e, p.kind))
        # keep non-overlapping spans: earlier first, then longer, then listed terms over patterns (stable order)
        found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        chosen, end = [], -1
        for s, e, kind in found:
            if s >= end:
                chosen.append((s, e, kind))
                end = e
        return chosen

    def redact(self, text: str) -> Redacted:
        reserved = set(_PLACEHOLDER.findall(text))
        counters: dict[str, int] = {}
        by_value: dict[tuple[str, str], str] = {}
        out, last, result = [], 0, Redacted(text)
        for s, e, kind in self._spans(text):
            value = text[s:e]
            key = (kind, value.lower())
            if key not in by_value:
                n = counters.get(kind, 0)
                while True:
                    n += 1
                    if (kind, str(n)) not in reserved:
                        break
                counters[kind] = n
                placeholder = f"[{kind}_{n}]"
                by_value[key] = placeholder
                result.mapping[placeholder] = value
                result.kinds[kind] = result.kinds.get(kind, 0) + 1
            out += [text[last:s], by_value[key]]
            last = e
        out.append(text[last:])
        result.text = "".join(out)
        return result
