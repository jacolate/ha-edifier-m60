"""Tests for the translation files."""

import json
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "edifier_m60"


def test_english_translation_matches_strings() -> None:
    """translations/en.json must be a copy of strings.json."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert english == strings
