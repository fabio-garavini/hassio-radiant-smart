"""Switch platform for Radiant Smart integration."""

import logging
from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .api import SwitchData
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: TopbandConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Switch entry setup."""
    hub = entry.runtime_data

    entities: list[RadiantSmartSwitch] = []

    for device in hub.devices.values():
        for switch in device.switches_data:
            _LOGGER.debug("RADIANT: Setting up switch: %s", switch.name)
            entities.append(RadiantSmartSwitch(switch))

    _LOGGER.debug("RADIANT: Adding %d switch entities", len(entities))

    async_add_entities(entities)

class RadiantSmartSwitch(SwitchEntity):
    """Radiant Smart Sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, data: SwitchData) -> None:
        """Initialize the sensor."""
        self._data = data
        self.entity_id = f"{SWITCH_DOMAIN}.{self._data.data_point.device.name.lower()}_{self._data.name.lower().replace(' ', '_')}"
        self._attr_unique_id = f"{self._data.data_point.device.name}_{self._data.name.lower().replace(" ", "_")}"
        self._attr_name = self._data.name.replace("_", " ")
        self._attr_icon = self._data.icon
        self._attr_is_on = bool(self._data.data_point.get_value())
        self._attr_device_class = self._data.device_class
        self._attr_available = self._data.data_point.device.online

    async def async_added_to_hass(self) -> None:
        """Run when this entity has been added to HA."""
        self._data.data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Run when this entity will be removed from HA."""
        self._data.data_point.remove_listener(self._handle_update)

    @property
    def device_info(self) -> DeviceInfo:
        """Information about this entity/device."""
        return {"identifiers": {(DOMAIN, self._data.data_point.device.product_id)}}

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        return self._data.data_point.device.online

    def _handle_update(self) -> None:
        self._attr_is_on = bool(self._data.data_point.get_value())
        self.schedule_update_ha_state()

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return self._attr_is_on

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self._data.data_point.set_value(1)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self._data.data_point.set_value(0)
