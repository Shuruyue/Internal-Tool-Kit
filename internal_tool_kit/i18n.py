"""Bilingual messages (English and Traditional Chinese) for everything the modules report to a user.

A module never formats a sentence itself: it raises :class:`KitError` (or returns a :class:`Message`) with a message
key and parameters, and the caller turns it into text in the language it needs::

    try:
        auth.verify_login(...)
    except KitError as exc:
        show(exc.text("zh-TW"))     # or exc.text("en"), or exc.text() for the default language

The default language is English and can be changed once at start-up with :func:`set_default_language`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LANGUAGES = ("en", "zh-TW")
_default_language = "en"

# key -> {language: template}; templates use str.format fields
MESSAGES: dict[str, dict[str, str]] = {}


def register(key: str, en: str, zh_tw: str) -> None:
    """Add a message. Every message must exist in both languages; a key can be registered only once."""
    if key in MESSAGES:
        raise ValueError(f"message {key!r} is already registered")
    MESSAGES[key] = {"en": en, "zh-TW": zh_tw}


def set_default_language(language: str) -> None:
    """Language used when :meth:`Message.text` is called without one."""
    global _default_language
    _default_language = _check_language(language)


def default_language() -> str:
    return _default_language


def _check_language(language: str) -> str:
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language {language!r}; use one of {', '.join(LANGUAGES)}")
    return language


def text(key: str, language: str | None = None, /, **params) -> str:
    """The message ``key`` in ``language`` (default: :func:`default_language`), with ``params`` filled in."""
    language = _check_language(language or _default_language)
    try:
        template = MESSAGES[key][language]
    except KeyError:
        raise KeyError(f"unknown message {key!r}") from None
    return template.format(**params)


@dataclass(frozen=True)
class Message:
    """A message key with its parameters, to be rendered in any language later."""

    key: str
    params: dict = field(default_factory=dict)

    def text(self, language: str | None = None) -> str:
        return text(self.key, language, **self.params)


class KitError(Exception):
    """An error meant to be shown to a user. ``str(error)`` gives the English text (for logs)."""

    def __init__(self, key: str, /, **params):
        if key not in MESSAGES:
            raise KeyError(f"unknown message {key!r}")
        self.message = Message(key, params)
        super().__init__(self.message.text("en"))

    @property
    def key(self) -> str:
        return self.message.key

    def text(self, language: str | None = None) -> str:
        return self.message.text(language)
