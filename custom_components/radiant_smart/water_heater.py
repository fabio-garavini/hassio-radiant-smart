"""Radian Smart water_heater platform."""

import logging
from typing import Any

from homeassistant.components.water_heater import (
    DOMAIN as WATER_HEATER_DOMAIN,
    STATE_GAS,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .api import BoilerMode, SmartDevice, WaterHeaterData
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
        self._attr_supported_features = (WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.ON_OFF | WaterHeaterEntityFeature.OPERATION_MODE)
        self._attr_operation_list = [STATE_OFF, STATE_GAS]
        self._attr_current_operation = self._parse_current_operation()

    def _parse_current_operation(self) -> str:
        if self._data.work_mode.get_value() == BoilerMode.HEATING_SANITARY or self._data.work_mode.get_value() == self._data.work_type:
            return STATE_GAS
        return STATE_OFF

    async def async_added_to_hass(self) -> None:
        """Run when this entity has been added to HA."""
        self._data.current_temp.add_listener(self._handle_update)
        self._data.target_temp.add_listener(self._handle_update)
        self._data.work_mode.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Run when this entity will be removed from HA."""
        self._data.current_temp.remove_listener(self._handle_update)
        self._data.target_temp.remove_listener(self._handle_update)
        self._data.work_mode.remove_listener(self._handle_update)

    @property
    def device_info(self) -> DeviceInfo:
        """Information about this entity/device."""
        return {"identifiers": {(DOMAIN, self._device.product_id)}}

    @property
    def available(self) -> bool:
        """Return True if roller and hub is available."""
        return self._device.online

    def set_temperature(self, **kwargs: Any) -> None:
        """Turn the water heater on."""
        self._data.target_temp.set_value(kwargs.get(ATTR_TEMPERATURE))

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the water heater on."""
        match self._data.work_mode.get_value():
            case BoilerMode.STANDBY:
                self._data.work_mode.set_value(self._data.work_type)
            case BoilerMode.HEATING_SANITARY, self._data.work_type:
                return
            case _:
                self._data.work_mode.set_value(BoilerMode.HEATING_SANITARY)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the water heater off."""
        match self._data.work_mode.get_value():
            case self._data.work_type:
                self._data.work_mode.set_value(BoilerMode.STANDBY)
            case BoilerMode.HEATING_SANITARY:
                self._data.work_mode.set_value(BoilerMode.HEATING) if self._data.work_type == BoilerMode.SANITARY else self._data.work_mode.set_value(BoilerMode.SANITARY)
            case BoilerMode.STANDBY, _:
                return

    def set_operation_mode(self, operation_mode: str) -> None:
        """Set water heater operation mode."""
        if operation_mode == STATE_GAS:
            self.turn_on()
        else:
            self.turn_off()

    def _handle_update(self) -> None:
        self._attr_current_temperature = self._data.current_temp.get_value()
        self._attr_target_temperature = self._data.target_temp.get_value()
        self._attr_current_operation = self._parse_current_operation()
        self.schedule_update_ha_state()
