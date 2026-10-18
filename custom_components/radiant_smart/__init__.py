"""The Radiant Smart integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    TopbandApiClientError,
    TopbandAuthenticationError,
    TopbandCloudApi,
)
from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

type TopbandConfigEntry = ConfigEntry[TopbandCloudApi]


async def async_migrate_entry(hass: HomeAssistant, entry: TopbandConfigEntry) -> bool:
    """Migrate old config entries to the current version.

    Entries created before v0.2.0 used config entry version 0 but stored the
    same data fields, so migrating only requires bumping the version.
    """
    if entry.version > 1:
        # Downgrade from a future version is not supported
        return False

    if entry.version == 0:
        _LOGGER.debug("Migrating Radiant Smart config entry to version 1")
        hass.config_entries.async_update_entry(entry, data=dict(entry.data), version=1)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: TopbandConfigEntry) -> bool:
    """Set up Radiant Smart from a config entry."""
    api = TopbandCloudApi(
        hass,
        async_get_clientsession(hass),
        entry.data,
        entry,
    )
    entry.runtime_data = api

    try:
        await api.async_authenticate()
    except TopbandAuthenticationError as err:
        raise ConfigEntryAuthFailed(err) from err
    except TopbandApiClientError as err:
        # Connection problems are retried later instead of prompting for
        # re-authentication.
        raise ConfigEntryNotReady(f"Failed to connect to Topband cloud: {err}") from err

    try:
        await api.mqtt.async_connect()
        devices = await api.async_get_devices()
        await api.async_subscribe_topics()
    except Exception as err:
        await api.async_shutdown()
        raise ConfigEntryNotReady(f"Failed to set up Radiant Smart: {err}") from err

    device_registry = dr.async_get(hass)
    for device in devices.values():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={(dr.CONNECTION_NETWORK_MAC, device.mac_address.upper())},
            identifiers={(DOMAIN, device.mac_address.upper())},
            manufacturer=MANUFACTURER,
            name=device.name,
            model=device.model,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TopbandConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok
