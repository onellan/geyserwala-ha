"""Tests for integration bootstrap helpers."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.thingwala_geyserwala.__init__ import (
    _merge_custom_entities,
    get_update_interval,
)


def test_get_update_interval_uses_default_on_invalid_value() -> None:
    """Fallback to default update interval if options are invalid."""
    entry = SimpleNamespace(options={"update_interval": "abc"})
    interval = get_update_interval(entry)
    assert interval.total_seconds() == 10


def test_get_update_interval_applies_minimum_bound() -> None:
    """Clamp update interval to minimum safe value."""
    entry = SimpleNamespace(options={"update_interval": 1})
    interval = get_update_interval(entry)
    assert interval.total_seconds() == 5


def test_merge_custom_entities_keeps_defaults_and_adds_yaml_entries() -> None:
    """Custom YAML entities should extend, not overwrite, defaults."""
    hass = SimpleNamespace(data={})
    config = {
        "thingwala_geyserwala": {
            "custom_entities": {
                "sensor": [{"name": "X", "key": "x"}],
                "text": [{"name": "Host Alias", "key": "alias", "visible": True}],
            }
        }
    }

    _merge_custom_entities(hass, config)

    merged = hass.data["thingwala_geyserwala_ENTITIES"]
    assert "sensor" in merged
    assert "text" in merged
    assert any(item.get("key") == "x" for item in merged["sensor"])
    assert any(item.get("key") == "alias" for item in merged["text"])
