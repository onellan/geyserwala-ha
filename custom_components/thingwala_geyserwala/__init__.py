####################################################################################
# Copyright (c) 2023 Thingwala                                                     #
####################################################################################
"""Geyserwala integration by Thingwala."""

from __future__ import annotations

import asyncio
import importlib
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

try:
    GeyserwalaClientAsync = importlib.import_module(
        "thingwala.geyserwala.aio.client"
    ).GeyserwalaClientAsync
    geyserwala_errors = importlib.import_module("thingwala.geyserwala.errors")
    GeyserwalaException = geyserwala_errors.GeyserwalaException
    Unauthorized = geyserwala_errors.Unauthorized
except ModuleNotFoundError:  # pragma: no cover - lets local tests import the module.
    GeyserwalaClientAsync = Any  # type: ignore[assignment]

    class GeyserwalaException(Exception):
        """Fallback exception used when the external client package is unavailable."""

    class Unauthorized(Exception):
        """Fallback exception used when the external client package is unavailable."""

from .binary_sensor import BINARY_SENSOR_SCHEMA
from .const import (
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
    MIN_UPDATE_INTERVAL_SECONDS,
    _LOGGER,
)
from .entities import ENTITIES
from .number import NUMBER_SCHEMA
from .sensor import SENSOR_SCHEMA
from .switch import SWITCH_SCHEMA
from .text import TEXT_SCHEMA
MAX_UPDATE_RETRIES = 2

PLATFORMS: list[Platform] = [
    Platform.TEXT,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
]

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Schema(
            {
                vol.Optional("custom_entities"): vol.Schema(
                    {
                        vol.Optional(Platform.SENSOR.value): vol.All(cv.ensure_list, [SENSOR_SCHEMA]),
                        vol.Optional(Platform.BINARY_SENSOR.value): vol.All(
                            cv.ensure_list, [BINARY_SENSOR_SCHEMA]
                        ),
                        vol.Optional(Platform.SWITCH.value): vol.All(cv.ensure_list, [SWITCH_SCHEMA]),
                        vol.Optional(Platform.NUMBER.value): vol.All(cv.ensure_list, [NUMBER_SCHEMA]),
                        vol.Optional(Platform.TEXT.value): vol.All(cv.ensure_list, [TEXT_SCHEMA]),
                    }
                ),
            }
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


def get_update_interval(entry: ConfigEntry) -> timedelta:
    """Get the poll interval from options with safe bounds and fallback."""
    seconds = entry.options.get("update_interval", DEFAULT_UPDATE_INTERVAL_SECONDS)
    try:
        parsed = max(int(seconds), MIN_UPDATE_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        parsed = DEFAULT_UPDATE_INTERVAL_SECONDS
    return timedelta(seconds=parsed)


def _merge_custom_entities(hass: HomeAssistant, config: dict[str, Any]) -> None:
    """Merge optional YAML-defined custom entities into the default entity map."""
    yaml_config = config.get(DOMAIN)
    merged = {key: list(value) for key, value in ENTITIES.items()}

    if yaml_config and "custom_entities" in yaml_config:
        for entity_type, entities in yaml_config["custom_entities"].items():
            merged.setdefault(entity_type, [])
            merged[entity_type].extend(entities)

    hass.data[f"{DOMAIN}_ENTITIES"] = merged


async def _async_update_status(gwc: Any, entry: ConfigEntry) -> Any:
    """Update device status with bounded retries and clearer failures."""
    host = entry.data["host"]
    port = entry.data["port"]

    for attempt in range(1, MAX_UPDATE_RETRIES + 2):
        try:
            async with asyncio.timeout(20):
                updated = await gwc.update()

            if not updated:
                raise UpdateFailed("Device did not return updated data")

            return gwc
        except Unauthorized as err:
            _LOGGER.error("[Geyserwala] Authentication failed for %s:%s", host, port)
            raise ConfigEntryAuthFailed from err
        except (asyncio.TimeoutError, GeyserwalaException, OSError) as err:
            if attempt > MAX_UPDATE_RETRIES:
                raise UpdateFailed(
                    f"Update failed after {MAX_UPDATE_RETRIES + 1} attempts: {err}"
                ) from err
            delay = attempt * 0.5
            _LOGGER.warning(
                "[Geyserwala] Update attempt %s/%s failed for %s:%s (%s). Retrying in %.1fs",
                attempt,
                MAX_UPDATE_RETRIES + 1,
                host,
                port,
                err,
                delay,
            )
            await asyncio.sleep(delay)

    raise UpdateFailed("Unexpected update loop termination")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Geyserwala from a config entry."""
    session = async_create_clientsession(hass)
    gwc = GeyserwalaClientAsync(
        host=entry.data["host"],
        port=entry.data["port"],
        username=entry.data["username"],
        password=entry.data["password"],
        session=session,
    )

    coordinator = DataUpdateCoordinator[Any](
        hass,
        _LOGGER,
        name=DOMAIN.title(),
        update_method=lambda: _async_update_status(gwc, entry),
        update_interval=get_update_interval(entry),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=gwc.id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration from YAML for custom entities support."""
    _merge_custom_entities(hass, config)
    return True
