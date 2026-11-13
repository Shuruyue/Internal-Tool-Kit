import pytest

from internal_tool_kit import i18n
from internal_tool_kit.i18n import KitError, Message


@pytest.fixture(autouse=True)
def english_default():
    i18n.set_default_language("en")
    yield
    i18n.set_default_language("en")


@pytest.fixture
def sample_key():
    key = "test.sample"
    if key not in i18n.MESSAGES:
        i18n.register(key, "Hello {name}", "你好 {name}")
    return key


def test_message_in_both_languages(sample_key):
    assert i18n.text(sample_key, "en", name="Ann") == "Hello Ann"
    assert i18n.text(sample_key, "zh-TW", name="Ann") == "你好 Ann"


def test_default_language_is_used_and_can_be_changed(sample_key):
    assert Message(sample_key, {"name": "Bo"}).text() == "Hello Bo"
    i18n.set_default_language("zh-TW")
    assert Message(sample_key, {"name": "Bo"}).text() == "你好 Bo"


def test_unknown_language_and_key_are_refused(sample_key):
    with pytest.raises(ValueError):
        i18n.text(sample_key, "fr", name="x")
    with pytest.raises(ValueError):
        i18n.set_default_language("de")
    with pytest.raises(KeyError):
        i18n.text("no.such.key")
    with pytest.raises(KeyError):
        KitError("no.such.key")


def test_parameters_may_be_called_key_or_language():
    if "test.names" not in i18n.MESSAGES:
        i18n.register("test.names", "{key} in {language}", "{key}（{language}）")
    assert KitError("test.names", key="k", language="fr").text("zh-TW") == "k（fr）"
    assert i18n.text("test.names", "en", key="k", language="fr") == "k in fr"


def test_a_key_cannot_be_registered_twice(sample_key):
    with pytest.raises(ValueError):
        i18n.register(sample_key, "again", "再一次")


def test_error_carries_key_and_renders_in_any_language(sample_key):
    err = KitError(sample_key, name="Cy")
    assert err.key == sample_key
    assert str(err) == "Hello Cy"          # English for logs, whatever the default language
    i18n.set_default_language("zh-TW")
    assert str(KitError(sample_key, name="Cy")) == "Hello Cy"
    assert err.text() == "你好 Cy"
    assert err.text("en") == "Hello Cy"


def test_every_registered_message_has_both_languages_with_the_same_fields():
    import string

    import internal_tool_kit  # noqa: F401  (modules register their messages on import)

    for key, langs in i18n.MESSAGES.items():
        assert set(langs) == set(i18n.LANGUAGES), key
        fields = {lang: {f for _, f, _, _ in string.Formatter().parse(t) if f} for lang, t in langs.items()}
        assert fields["en"] == fields["zh-TW"], key
