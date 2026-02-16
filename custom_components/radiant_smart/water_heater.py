"""Radian Smart water_heater platform."""

import logging
from typing import Any

from homeassistant.components.water_heater import (
    DOMAIN as WATER_HEATER_DOMAIN,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .api import SmartDevice, WaterHeaterData
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: TopbandConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Sensor entry setup."""
    hub = entry.runtime_data

    entities: list[RadiantSmartWaterHeater] = []
    for device in hub.devices.values():
        for water_heater in device.water_heaters:
            _LOGGER.debug("RADIANT: Setting up water_heater: %s", water_heater.name)
            entities.append(RadiantSmartWaterHeater(hass, device, water_heater))

    _LOGGER.debug("RADIANT: Adding %d sensor entities", len(entities))

    async_add_entities(entities)

class RadiantSmartWaterHeater(WaterHeaterEntity):
    """Radiant Smart Sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, device: SmartDevice, data: WaterHeaterData) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._device = device
        self._data = data
        self.entity_id = f"{WATER_HEATER_DOMAIN}.{self._device.name.lower()}_{self._data.name.lower().replace(' ', '_')}"
        self._attr_unique_id = f"{self._device.name}_{data.name.lower()}"
        self._attr_name = data.name.replace("_", " ")
        self._attr_icon = data.icon
        self._attr_min_temp = data.min_temp.get_value()
        self._attr_max_temp = data.max_temp.get_value()
        self._attr_current_temperature = data.current_temp.get_value()
        self._attr_target_temperature = data.target_temp.get_value()
        self._attr_target_temperature_step = data.target_temp_step
        self._attr_temperature_unit = data.temp_unit
        self._attr_supported_features = (WaterHeaterEntityFeature.TARGET_TEMPERATURE)
        self._attr_current_operation = STATE_ON
        self._attr_operation_list = [STATE_ON, STATE_OFF]

    async def async_added_to_hass(self) -> None:
        """Run when this entity has been added to HA."""
        self._data.current_temp.add_listener(self._handle_update)
        self._data.target_temp.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Run when this entity will be removed from HA."""
        self._data.current_temp.remove_listener(self._handle_update)
        self._data.target_temp.remove_listener(self._handle_update)

    @property
    def device_info(self) -> DeviceInfo:
        """Information about this entity/device."""
        return {"identifiers": {(DOMAIN, self._device.product_id)}}

    @property
    def available(self) -> bool:
        """Return True if roller and hub is available."""
        return self._device.online

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._attr_current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self._attr_target_temperature

    @property
    def current_operation(self) -> str | None:
        """Return current operation ie. eco, electric, performance, ..."""
        return self._attr_current_operation

    def set_temperature(self, **kwargs: Any) -> None:
        """Turn the water heater on."""
        self._data.target_temp.set_value(kwargs.get(ATTR_TEMPERATURE))

    # def set_operation_mode(self, operation_mode: str) -> None:
    #     """Turn the water heater on."""
    #     return

    def _handle_update(self) -> None:
        self._attr_current_temperature = self._data.current_temp.get_value()
        self._attr_target_temperature = self._data.target_temp.get_value()
        self.schedule_update_ha_state()
