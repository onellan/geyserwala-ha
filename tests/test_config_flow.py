"""Tests for the Geyserwala config and options flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from custom_components.thingwala_geyserwala import config_flow as flow_mod

flow_mod.config_entries.report_usage = lambda *args, **kwargs: None
flow_mod.async_create_clientsession = lambda hass: object()


class FakeGeyserwalaClient:
    """Minimal async client double for config flow tests."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.id = "device-123"
        self.name = "Geyserwala"
        self.hostname = "geyserwala.local"
        self.subscribed: list[str] = []
        self.closed = False
        self.values = {
            "status": "Online",
            "version": "1.2.3",
            "wifi-status": True,
            "cloud-status": False,
            "mqtt-up": True,
            "unixtime": 1_700_000_000,
            "wifi-ssid": "HouseWiFi",
            "wifi-pass": "stored-wifi-pass",
            "cloud-token": "stored-cloud-token",
            "name": "Kitchen Geyser",
            "hostname": "kitchen-geyser",
            "setpoint-max": 55,
            "gw-diff": 7,
            "gw-antifreeze": 10,
            "dc-max-temp": 60,
            "gw-ldr-min": 0,
            "app-enable": True,
            "app-pass": "stored-app-pass",
            "utc-offset": 330,
            "ntp-host": "pool.ntp.org",
            "ntp-port": 123,
            "mqtt-enable": True,
            "mqtt-host": "mqtt.local",
            "mqtt-port": 1883,
            "mqtt-user": "geyser",
            "mqtt-pass": "stored-mqtt-pass",
            "mqtt-topic-tmpl": "geyserwala/%mac%",
            "mqtt-clientid": "geyserwala-%mac%",
            "ip-static": "192.168.1.50",
            "ip-netmask": "255.255.255.0",
            "ip-gateway": "192.168.1.1",
            "ip-dns1": "1.1.1.1",
            "ip-dns2": "8.8.8.8",
            "update-auto": True,
            "usage-reporting": False,
        }

    def subscribe(self, key):
        self.subscribed.append(key)

    async def update(self):
        return True

    def get_value(self, key):
        return self.values.get(key)

    async def close(self):
        self.closed = True


class FakeConfigEntriesManager:
    """Minimal config entry registry used by the flow tests."""

    def __init__(self, entry):
        self._entry = entry
        self.async_update_entry = MagicMock()
        self.async_reload = AsyncMock()

    def async_get_known_entry(self, entry_id):
        if entry_id != self._entry.entry_id:
            raise KeyError(entry_id)
        return self._entry


def _make_flow() -> flow_mod.GeyserwalaOptionsFlow:
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_HOST: "192.168.1.105",
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "existing-password",
        },
        options={
            "wifi-pass": "existing-wifi-pass",
            "mqtt-pass": "existing-mqtt-pass",
            "app-pass": "existing-app-pass",
            "calibrations_json": "",
            "alert_rules_json": "",
        },
    )
    manager = FakeConfigEntriesManager(entry)
    flow = flow_mod.GeyserwalaOptionsFlow(entry)
    flow.handler = entry.entry_id
    flow.hass = SimpleNamespace(
        data={},
        config_entries=manager,
    )
    return flow


@pytest.mark.asyncio
async def test_async_fetch_device_snapshot_reads_all_device_keys(monkeypatch) -> None:
    """The device snapshot should subscribe to and read the mirrored settings keys."""
    flow = _make_flow()
    monkeypatch.setattr(flow_mod, "_DEPENDENCY_AVAILABLE", True)
    monkeypatch.setattr(flow_mod, "GeyserwalaClientAsync", FakeGeyserwalaClient)

    snapshot = await flow._async_fetch_device_snapshot("192.168.1.105", 80, "admin", "password")

    assert snapshot["status"] == "Online"
    assert snapshot["wifi-ssid"] == "HouseWiFi"
    assert snapshot["mqtt-clientid"] == "geyserwala-%mac%"
    assert snapshot["update-auto"] is True
    assert snapshot["usage-reporting"] is False


@pytest.mark.asyncio
async def test_async_step_init_stores_state_and_advances_to_device(monkeypatch) -> None:
    """Submitting the connection step should validate and hand off to the mirrored device step."""
    flow = _make_flow()
    monkeypatch.setattr(flow_mod, "_DEPENDENCY_AVAILABLE", True)
    monkeypatch.setattr(flow_mod, "GeyserwalaClientAsync", FakeGeyserwalaClient)
    validate_mock = AsyncMock(return_value=None)
    snapshot_mock = AsyncMock(return_value={"status": "Online", "wifi-ssid": "HouseWiFi"})
    device_step_mock = AsyncMock(return_value={"type": "form", "step_id": "device"})
    monkeypatch.setattr(flow, "_async_validate_connection", validate_mock)
    monkeypatch.setattr(flow, "_async_fetch_device_snapshot", snapshot_mock)
    monkeypatch.setattr(flow, "async_step_device", device_step_mock)

    result = await flow.async_step_init(
        {
            CONF_HOST: "192.168.1.106",
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "existing-password",
            "update_interval": 15,
            "enable_services": True,
            "enable_mqtt": True,
            "enable_calibration": False,
            "enable_alerts": True,
        }
    )

    assert result == {"type": "form", "step_id": "device"}
    validate_mock.assert_awaited_once_with(
        host="192.168.1.106",
        port=80,
        username="admin",
        password="existing-password",
    )
    snapshot_mock.assert_awaited_once_with(
        host="192.168.1.106",
        port=80,
        username="admin",
        password="existing-password",
    )
    device_step_mock.assert_awaited_once()
    assert flow.hass.data["_geyserwala_config_flow_state"]["entry-1"]["update_interval"] == 15
    assert flow.hass.data["_geyserwala_config_flow_state"]["entry-1"]["_device_snapshot"] == {
        "status": "Online",
        "wifi-ssid": "HouseWiFi",
    }


@pytest.mark.asyncio
async def test_async_step_device_preserves_existing_secrets_and_reloads(monkeypatch) -> None:
    """Saving the mirrored settings should preserve existing secrets when left blank."""
    flow = _make_flow()
    flow.hass.data["_geyserwala_config_flow_state"] = {
        "entry-1": {
            CONF_HOST: "192.168.1.105",
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "existing-password",
            "_device_snapshot": {
                "status": "Online",
                "wifi-ssid": "HouseWiFi",
                "mqtt-enable": True,
            },
        }
    }
    monkeypatch.setattr(flow_mod, "_DEPENDENCY_AVAILABLE", True)

    result = await flow.async_step_device(
        {
            "wifi-ssid": "UpdatedWiFi",
            "wifi-pass": "",
            "cloud-token": "new-cloud-token",
            "name": "Updated Name",
            "hostname": "updated-hostname",
            "setpoint-max": 58,
            "gw-diff": 8,
            "gw-antifreeze": 11,
            "dc-max-temp": 61,
            "gw-ldr-min": 5,
            "app-enable": False,
            "app-pass": "",
            "utc-offset": 330,
            "ntp-host": "time.google.com",
            "ntp-port": 123,
            "mqtt-enable": True,
            "mqtt-host": "mqtt.local",
            "mqtt-port": 1883,
            "mqtt-user": "geyser",
            "mqtt-pass": "",
            "mqtt-topic-tmpl": "geyserwala/%mac%",
            "mqtt-clientid": "geyserwala-%mac%",
            "ip-static": "192.168.1.50",
            "ip-netmask": "255.255.255.0",
            "ip-gateway": "192.168.1.1",
            "ip-dns1": "1.1.1.1",
            "ip-dns2": "8.8.8.8",
            "update-auto": True,
            "usage-reporting": False,
            "transport": "http",
            "mqtt_base_topic": "geyserwala",
            "calibrations_json": "{}",
            "alert_rules_json": "[]",
        }
    )

    assert result["type"] == "create_entry"
    assert result["title"] == ""
    assert result["data"]["wifi-ssid"] == "UpdatedWiFi"
    assert result["data"]["wifi-pass"] == "existing-wifi-pass"
    assert result["data"]["app-pass"] == "existing-app-pass"
    assert result["data"]["mqtt-pass"] == "existing-mqtt-pass"
    assert result["data"]["calibrations_json"] == "{}"
    assert result["data"]["alert_rules_json"] == "[]"

    flow.hass.config_entries.async_update_entry.assert_not_called()
    flow.hass.config_entries.async_reload.assert_awaited_once_with("entry-1")
    assert "entry-1" not in flow.hass.data["_geyserwala_config_flow_state"]