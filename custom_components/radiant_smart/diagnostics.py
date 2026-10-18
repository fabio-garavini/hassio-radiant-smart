"""Diagnostics support for the Radiant Smart integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import TopbandConfigEntry
from .const import CONF_HOME, CONF_TOKEN_DATA

# Config entry keys containing credentials and identifiers
TO_REDACT_ENTRY = {
    CONF_EMAIL,
    CONF_HOME,
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
}

# Device fields identifying the user's hardware and account
TO_REDACT_DEVICE = {
    "gateway",
    "id",
    "mac_address",
    "product_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TopbandConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    api = entry.runtime_data

    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT_ENTRY),
        },
        "mqtt": {
            "connected": api.mqtt.connected,
            "subscriptions": sorted(api.mqtt.subscriptions),
        },
        "devices": [
            async_redact_data(
                {
                    "name": device.name,
                    "model": device.model,
                    "device_type": device.device_type,
                    "online": device.online,
                    "id": device.id,
                    "product_id": device.product_id,
                    "mac_address": device.mac_address,
                    "gateway": {
                        "uid": device.gateway.uid,
                        "product_id": device.gateway.product_id,
                    },
                    "data_points": {
                        name: {
                            "index": data_point.index,
                            "type": data_point.point_type,
                            "value": data_point.value,
                        }
                        for name, data_point in device.data_points.items()
                    },
                },
                TO_REDACT_DEVICE,
            )
            for device in api.devices.values()
        ],
    }
