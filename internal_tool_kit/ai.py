"""One interface to any text-generation service, with redaction on the way out and restoration on the way back.

The kit does not depend on any AI SDK. Wrap the one you use in a :class:`Provider` (a class with a ``complete``
method), keep its API key in the deployment platform's secret store, and call :func:`ask`::

    class MyProvider:
        def complete(self, prompt: str, *, system: str | None = None) -> str:
            ...  # call the SDK and return the text of the answer

    answer = ask(MyProvider(), prompt, redactor=Redactor({"NAME": staff_names, "CLIENT": client_names}))
    show(answer.text)                                # originals restored
    show(answer.outgoing.redacted.summary().text())  # "3 item(s) were replaced before sending: EMAIL 1, NAME 2."

To let a person check what leaves before it does, split :func:`ask` in two: :func:`prepare`, show
``outgoing.prompt`` (and ``outgoing.system``), then :func:`send`. The provider only ever sees those two strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from internal_tool_kit.redaction import Redacted, Redactor

_SEPARATOR = "\n⁣⁣⁣\n"      # invisible separator that no pattern or term matches


class Provider(Protocol):
    def complete(self, prompt: str, *, system: str | None = None) -> str: ...


@dataclass(frozen=True)
class Outgoing:
    prompt: str              # exactly what the provider will receive
    system: str | None
    redacted: Redacted       # placeholders of both texts


@dataclass(frozen=True)
class Answer:
    text: str                # the answer with the originals put back
    outgoing: Outgoing


def prepare(prompt: str, *, redactor: Redactor, system: str | None = None) -> Outgoing:
    """Redact the system text and the prompt together, so one value gets one placeholder in both."""
    if system is None:
        redacted = redactor.redact(prompt)
        return Outgoing(redacted.text, None, redacted)
    redacted = redactor.redact(system + _SEPARATOR + prompt)
    system_out, prompt_out = redacted.text.split(_SEPARATOR, 1)
    return Outgoing(prompt_out, system_out, redacted)


def send(provider: Provider, outgoing: Outgoing) -> Answer:
    return Answer(outgoing.redacted.restore(provider.complete(outgoing.prompt, system=outgoing.system)), outgoing)


def ask(provider: Provider, prompt: str, *, redactor: Redactor, system: str | None = None) -> Answer:
    return send(provider, prepare(prompt, redactor=redactor, system=system))
