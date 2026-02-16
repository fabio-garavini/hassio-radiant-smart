"""Number platform for Radiant Smart integration."""

import logging

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .api import NumberData
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: TopbandConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Number entry setup."""
    hub = entry.runtime_data

    entities: list[RadiantSmartNumber] = []

    for device in hub.devices.values():
        for number in device.numbers_data:
            _LOGGER.debug("RADIANT: Setting up number: %s", number.name)
            entities.append(RadiantSmartNumber(number))

    _LOGGER.debug("RADIANT: Adding %d number entities", len(entities))

    async_add_entities(entities)

class RadiantSmartNumber(NumberEntity):
    """Radiant Smart Sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, data: NumberData) -> None:
        """Initialize the sensor."""
        self._data = data
        self.entity_id = f"{NUMBER_DOMAIN}.{self._data.data_point.device.name.lower()}_{self._data.name.lower().replace(' ', '_')}"
        self._attr_unique_id = f"{self._data.data_point.device.name}_{self._data.name.lower().replace(" ", "_")}"
        self._attr_name = self._data.name.replace("_", " ")
        self._attr_icon = self._data.icon
        self._attr_mode = data.mode
        self._attr_device_class = data.device_class
        self._attr_native_unit_of_measurement = data.unit_of_measurement
        self._attr_available = self._data.data_point.device.online
        self._attr_native_value = data.data_point.get_value()
        self._attr_native_min_value = data.min_value
        self._attr_native_max_value = data.max_value
        self._attr_native_step = data.step_value

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
        self._attr_native_value = self._data.data_point.get_value()
        self.schedule_update_ha_state()

    def set_native_value(self, value: float) -> None:
        """Turn the entity on."""
        self._data.data_point.set_value(value)
