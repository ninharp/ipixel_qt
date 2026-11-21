import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from ipixel.studio import i18n  # noqa: E402
from ipixel.studio.app import OPEN_FILTER  # noqa: E402
from ipixel.studio.editor import Tool  # noqa: E402
from ipixel.studio.tabs import EFFECT_LABELS  # noqa: E402

STUDIO = Path(i18n.__file__).parent


def _tr_literals() -> set[str]:
    found = set()
    for path in STUDIO.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                found.add(node.args[0].value)
    return found


def test_every_ui_string_has_an_english_translation():
    needed = _tr_literals() | {t.value[0] for t in Tool} | set(EFFECT_LABELS.values()) | {OPEN_FILTER}
    needed |= {"getrennt", "verbinde…", "verbunden"}
    missing = sorted(s for s in needed if s not in i18n.EN)
    assert not missing, missing


def test_placeholders_match_between_languages():
    import string

    fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
    bad = [k for k, v in i18n.EN.items() if fields(k) != fields(v)]
    assert not bad, bad


def test_every_placeholder_name_is_accepted():
    """A placeholder may be called like tr()'s own parameter (regression: {text})."""
    import string

    for key in i18n.EN:
        names = {f.split(":")[0] for _, f, _, _ in string.Formatter().parse(key) if f}
        values = {n: 0 for n in names}
        i18n.tr(key, **values)


def test_tr_switches_language():
    try:
        i18n.set_language("en")
        assert i18n.tr("Bild senden") == "Send image"
        assert i18n.tr("Stand {left}:{right}", left=2, right=1) == "Score 2:1"
        i18n.set_language("de")
        assert i18n.tr("Bild senden") == "Bild senden"
        assert i18n.tr("Stand {left}:{right}", left=2, right=1) == "Stand 2:1"
    finally:
        i18n.set_language("de")
