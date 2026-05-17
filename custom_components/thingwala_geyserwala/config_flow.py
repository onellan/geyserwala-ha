####################################################################################
# Copyright (c) 2023 Thingwala                                                     #
####################################################################################
"""Geyserwala config flow."""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.network import is_ipv6_address

try:
    GeyserwalaClientAsync = importlib.import_module(
        "thingwala.geyserwala.aio.client"
    ).GeyserwalaClientAsync
    geyserwala_errors = importlib.import_module("thingwala.geyserwala.errors")
    GeyserwalaException = geyserwala_errors.GeyserwalaException
    Unauthorized = geyserwala_errors.Unauthorized
    _DEPENDENCY_AVAILABLE = True
    _DEPENDENCY_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as err:  # pragma: no cover - depends on runtime package installation.
    GeyserwalaClientAsync = Any  # type: ignore[assignment]

    class GeyserwalaException(Exception):
        """Fallback exception used when the external client package is unavailable."""

    class Unauthorized(Exception):
        """Fallback exception used when the external client package is unavailable."""

    _DEPENDENCY_AVAILABLE = False
    _DEPENDENCY_IMPORT_ERROR = err
except Exception as err:  # pragma: no cover - defensive to avoid config flow import crashes.
    GeyserwalaClientAsync = Any  # type: ignore[assignment]

    class GeyserwalaException(Exception):
        """Fallback exception used when the external client package fails to load."""

    class Unauthorized(Exception):
        """Fallback exception used when the external client package fails to load."""

    _DEPENDENCY_AVAILABLE = False
    _DEPENDENCY_IMPORT_ERROR = err

from .const import (
    _LOGGER,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_USERNAME,
    DOMAIN,
    MIN_UPDATE_INTERVAL_SECONDS,
)

_DEPENDENCY_ERROR_LOGGED = False


class GeyserwalaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Geyserwala config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> GeyserwalaOptionsFlow:
        """Return the options flow handler."""
        return GeyserwalaOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Init."""
        self._config: dict[str, Any] = {}
        self._errors: dict[str, str] = {}

    def _dependency_ready(self) -> bool:
        """Check whether runtime client dependency is available for validation calls."""
        global _DEPENDENCY_ERROR_LOGGED

        if _DEPENDENCY_AVAILABLE:
            return True

        if not _DEPENDENCY_ERROR_LOGGED:
            if isinstance(_DEPENDENCY_IMPORT_ERROR, ModuleNotFoundError):
                _LOGGER.error(
                    "[Geyserwala] Missing dependency 'thingwala-geyserwala' while loading config flow. "
                    "Home Assistant should install this automatically from manifest requirements. "
                    "Restart Home Assistant and check dependency installation logs. error=%s",
                    _DEPENDENCY_IMPORT_ERROR,
                )
            else:
                _LOGGER.error(
                    "[Geyserwala] Failed to load dependency 'thingwala-geyserwala' while loading config flow: %r",
                    _DEPENDENCY_IMPORT_ERROR,
                    exc_info=_DEPENDENCY_IMPORT_ERROR,
                )
            _DEPENDENCY_ERROR_LOGGED = True

        if isinstance(_DEPENDENCY_IMPORT_ERROR, ModuleNotFoundError):
            self._errors["base"] = "dependency_not_installed"
        else:
            self._errors["base"] = "dependency_load_failed"
        return False

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> FlowResult:
        """Handle zeroconf discovery."""
        properties = discovery_info.properties
        ip_address = discovery_info.host
        port = discovery_info.port
        if is_ipv6_address(ip_address):
            return self.async_abort(reason="ipv6_not_supported")
        uuid = properties["id"]
        await self.async_set_unique_id(uuid)
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: ip_address, CONF_PORT: port})
        self._config.update({
            CONF_HOST: ip_address,
            CONF_PORT: port,
            CONF_USERNAME: None,
            CONF_PASSWORD: None,
        })
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle user input."""
        if user_input is not None:
            self._async_abort_entries_match(
                {
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                }
            )
            self._config.update(user_input)
            return await self.async_step_validate()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._config.get(CONF_HOST, '')): str,
                vol.Required(CONF_PORT, default=self._config.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Required(CONF_USERNAME, default=self._config.get(CONF_USERNAME, None) or DEFAULT_USERNAME): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
            }
        )

        return self.async_show_form(step_id="user",
                                    data_schema=data_schema,
                                    errors=self._errors,
                                    )

    async def async_step_validate(self) -> FlowResult:
        """Handle device validation with detailed logging."""
        if not self._dependency_ready():
            return await self.async_step_user()

        session = async_create_clientsession(self.hass)
        host = self._config[CONF_HOST]
        port = self._config[CONF_PORT]
        username = self._config[CONF_USERNAME]
        _LOGGER.debug("[Geyserwala] Starting validation for host=%s port=%s username=%s", host, port, username)
        api = GeyserwalaClientAsync(host=host,
                                    port=port,
                                    username=username,
                                    password=self._config[CONF_PASSWORD],
                                    session=session,
                                    )
        try:
            async with asyncio.timeout(20):
                connected = await api.update()
            if not connected:
                _LOGGER.error("[Geyserwala] Device unreachable at %s:%s", host, port)
                return self.async_abort(reason="unreachable")
        except Unauthorized as ex:
            _LOGGER.error("[Geyserwala] Unauthorized for %s:%s - %s", host, port, ex)
            self._errors['base'] = 'invalid_auth'
            return await self.async_step_user()
        except GeyserwalaException as ex:
            _LOGGER.error("[Geyserwala] Cannot connect to %s:%s - %s", host, port, ex)
            self._errors['base'] = 'cannot_connect'
            return await self.async_step_user()
        except TimeoutError:
            _LOGGER.error("[Geyserwala] Timeout while validating %s:%s", host, port)
            self._errors['base'] = 'cannot_connect'
            return await self.async_step_user()
        except Exception:
            _LOGGER.exception("[Geyserwala] Unexpected error during validation for %s:%s", host, port)
            self._errors['base'] = 'cannot_connect'
            return await self.async_step_user()
        self._config['id'] = api.id
        self._config['name'] = api.name
        self._config['hostname'] = api.hostname

        _LOGGER.info("[Geyserwala] Successfully validated device at %s:%s", host, port)
        return self.async_create_entry(
            title=self._config['name'],
            data=self._config
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration of device connection settings."""
        config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if config_entry is None:
            return self.async_abort(reason="reconfigure_failed")

        if user_input is not None:
            if not self._dependency_ready():
                return await self.async_step_reconfigure()

            # Validate new connection settings
            session = async_create_clientsession(self.hass)
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            _LOGGER.debug(
                "[Geyserwala] Starting reconfiguration validation for host=%s port=%s username=%s",
                host, port, username
            )

            api = GeyserwalaClientAsync(
                host=host,
                port=port,
                username=username,
                password=password,
                session=session,
            )

            try:
                async with asyncio.timeout(20):
                    connected = await api.update()
                if not connected:
                    _LOGGER.error("[Geyserwala] Device unreachable at %s:%s", host, port)
                    return self.async_abort(reason="unreachable")
            except Unauthorized as ex:
                _LOGGER.error("[Geyserwala] Unauthorized for %s:%s - %s", host, port, ex)
                self._errors['base'] = 'invalid_auth'
                return await self.async_step_reconfigure()
            except GeyserwalaException as ex:
                _LOGGER.error("[Geyserwala] Cannot connect to %s:%s - %s", host, port, ex)
                self._errors['base'] = 'cannot_connect'
                return await self.async_step_reconfigure()
            except TimeoutError:
                _LOGGER.error("[Geyserwala] Timeout while validating %s:%s", host, port)
                self._errors['base'] = 'cannot_connect'
                return await self.async_step_reconfigure()
            except Exception:
                _LOGGER.exception(
                    "[Geyserwala] Unexpected error during reconfiguration validation for %s:%s",
                    host, port
                )
                self._errors['base'] = 'cannot_connect'
                return await self.async_step_reconfigure()

            # Validation successful - update config entry
            new_data = {
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                'id': api.id,
                'name': api.name,
                'hostname': api.hostname,
            }
            self.hass.config_entries.async_update_entry(config_entry, data=new_data)
            await self.hass.config_entries.async_reload(config_entry.entry_id)
            _LOGGER.info("[Geyserwala] Device reconfigured successfully at %s:%s", host, port)
            return self.async_abort(reason="reconfigure_successful")

        # Load current configuration
        current_host = config_entry.data.get(CONF_HOST, '')
        current_port = config_entry.data.get(CONF_PORT, DEFAULT_PORT)
        current_username = config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
        current_password = config_entry.data.get(CONF_PASSWORD, '')

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_host): str,
                vol.Required(CONF_PORT, default=current_port): int,
                vol.Required(CONF_USERNAME, default=current_username): str,
                vol.Optional(CONF_PASSWORD, default=current_password): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=self._errors,
            description_placeholders={
                "device_name": config_entry.data.get('name', 'Geyserwala'),
            },
        )


class GeyserwalaOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Options flow for Geyserwala integration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage integration options - polling and feature selection."""
        if user_input is not None:
            # Store the main options and proceed to feature-specific configuration
            self.hass.data.setdefault("_geyserwala_config_flow_state", {})[
                self.config_entry.entry_id
            ] = user_input

            # Check if any features are enabled - if so, show feature config step
            if user_input.get("enable_mqtt") or user_input.get("enable_calibration") or user_input.get("enable_alerts"):
                return await self.async_step_features()

            # Merge with existing options to preserve any prior feature configs
            final_options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=final_options)

        # Load current values
        current_options = self.config_entry.options
        default_interval = current_options.get("update_interval", DEFAULT_UPDATE_INTERVAL_SECONDS)
        enable_mqtt = current_options.get("enable_mqtt", False)
        enable_calibration = current_options.get("enable_calibration", False)
        enable_alerts = current_options.get("enable_alerts", False)
        enable_services = current_options.get("enable_services", True)  # Services enabled by default

        schema = vol.Schema(
            {
                # Polling Configuration
                vol.Required("update_interval", default=default_interval): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_UPDATE_INTERVAL_SECONDS, max=600),
                ),
                # Feature Toggles
                vol.Required("enable_services", default=enable_services): bool,
                vol.Required("enable_mqtt", default=enable_mqtt): bool,
                vol.Required("enable_calibration", default=enable_calibration): bool,
                vol.Required("enable_alerts", default=enable_alerts): bool,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "services_info": "Set boost, mode, read/clear error codes",
                "mqtt_info": "Use MQTT transport instead of HTTP",
                "calibration_info": "Per-entity offset and multiplier adjustments",
                "alerts_info": "Threshold and state-change notifications",
            },
        )

    async def async_step_features(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure enabled features - MQTT, calibration, alerts."""
        # Load current feature configurations
        current_options = self.config_entry.options
        base_options = self.hass.data.get("_geyserwala_config_flow_state", {}).get(
            self.config_entry.entry_id, {}
        )

        enable_mqtt = base_options.get("enable_mqtt", current_options.get("enable_mqtt", False))
        enable_calibration = base_options.get("enable_calibration", current_options.get("enable_calibration", False))
        enable_alerts = base_options.get("enable_alerts", current_options.get("enable_alerts", False))

        default_mqtt_transport = current_options.get("transport", "http")
        default_mqtt_topic = current_options.get("mqtt_base_topic", "geyserwala")

        schema_fields = {}

        # MQTT Configuration (if enabled)
        if enable_mqtt:
            schema_fields.update({
                vol.Required("transport", default=default_mqtt_transport): vol.In(["http", "mqtt"]),
                vol.Optional("mqtt_base_topic", default=default_mqtt_topic): str,
            })

        # Calibration Configuration (if enabled)
        if enable_calibration:
            schema_fields[vol.Optional("calibrations_json", default="")] = str

        # Alert Rules Configuration (if enabled)
        if enable_alerts:
            schema_fields[vol.Optional("alert_rules_json", default="")] = str

        if not schema_fields:
            # No enabled features with configuration
            return self.async_create_entry(
                title="",
                data={**self.config_entry.options, **base_options}
            )

        schema = vol.Schema(schema_fields)

        if user_input is not None:
            errors: dict[str, str] = {}

            calibrations_json = user_input.get("calibrations_json", "").strip()
            if enable_calibration and calibrations_json:
                try:
                    parsed_calibrations = json.loads(calibrations_json)
                    if not isinstance(parsed_calibrations, dict):
                        errors["calibrations_json"] = "invalid_calibrations_json"
                except json.JSONDecodeError:
                    errors["calibrations_json"] = "invalid_calibrations_json"

            alert_rules_json = user_input.get("alert_rules_json", "").strip()
            if enable_alerts and alert_rules_json:
                try:
                    parsed_alert_rules = json.loads(alert_rules_json)
                    if not isinstance(parsed_alert_rules, list):
                        errors["alert_rules_json"] = "invalid_alert_rules_json"
                except json.JSONDecodeError:
                    errors["alert_rules_json"] = "invalid_alert_rules_json"

            if errors:
                return self.async_show_form(
                    step_id="features",
                    data_schema=schema,
                    errors=errors,
                    description_placeholders={
                        "mqtt_help": "Select MQTT as transport and specify the base topic for device communication",
                        "calibration_help": "Enter JSON with per-entity calibration settings: {\"entity_key\": {\"offset\": 0, \"multiplier\": 1}}",
                        "alerts_help": 'Enter JSON array of alert rules: [{"rule_id": "rule1", "entity_key": "...", "condition_type": "threshold", "condition_value": 50, "severity": "warning", "message_template": "...", "enabled": true}]',
                    },
                )

            # Merge feature configurations with base options
            final_options = {**self.config_entry.options, **base_options, **user_input}

            # Clean up temporary state
            self.hass.data.get("_geyserwala_config_flow_state", {}).pop(
                self.config_entry.entry_id, None
            )

            return self.async_create_entry(title="", data=final_options)

        return self.async_show_form(
            step_id="features",
            data_schema=schema,
            description_placeholders={
                "mqtt_help": "Select MQTT as transport and specify the base topic for device communication",
                "calibration_help": "Enter JSON with per-entity calibration settings: {\"entity_key\": {\"offset\": 0, \"multiplier\": 1}}",
                "alerts_help": 'Enter JSON array of alert rules: [{"rule_id": "rule1", "entity_key": "...", "condition_type": "threshold", "condition_value": 50, "severity": "warning", "message_template": "...", "enabled": true}]',
            },
        )
