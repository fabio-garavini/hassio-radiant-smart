"""The Detailed Hello World Push integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import TopbandCloudApi
from .const import CONF_COMPANY, CONF_HOME, CONF_TOKEN_DATA, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.WATER_HEATER]

type TopbandConfigEntry = ConfigEntry[TopbandCloudApi]


async def async_setup_entry(hass: HomeAssistant, entry: TopbandConfigEntry) -> bool:
    """Set up Hello World from a config entry."""

    entry.runtime_data = TopbandCloudApi(
        async_create_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_COMPANY],
        entry.data[CONF_HOME],
        entry.data[CONF_TOKEN_DATA],
    )

    await entry.runtime_data.authenticate()

    # new_data = entry.data.copy()

    #new_data.update({CONF_TOKEN_DATA: await entry.runtime_data.authenticate()})

    # hass.config_entries.async_update_entry(entry=entry, data=new_data)

    await hass.async_add_executor_job(entry.runtime_data.mqtt_connect)

    device_registry = dr.async_get(hass)

    devices = await entry.runtime_data.async_get_devices()

    for device in devices.values():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={(dr.CONNECTION_NETWORK_MAC, device.mac_address)},
            identifiers={(DOMAIN, device.product_id)},
            manufacturer="Radiant Smart",
            name=device.name,
            model=device.model,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TopbandConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    entry.runtime_data.mqtt_disconnect()

    return unload_ok
