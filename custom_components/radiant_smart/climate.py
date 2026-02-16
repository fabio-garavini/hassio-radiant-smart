"""Radiant Smart climate platform."""

import logging
from typing import Any

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .api import ClimateData, SmartDevice
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: TopbandConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Climate entry setup."""
    hub = entry.runtime_data

    entities: list[RadiantSmartThermostat] = []
    for device in hub.devices.values():
        for climate in device.climate_data:
            _LOGGER.debug("RADIANT: Setting up climate: %s", climate.name)
            entities.append(RadiantSmartThermostat(hass, device, climate))

    _LOGGER.debug("RADIANT: Adding %d climate entities", len(entities))

    async_add_entities(entities)

class RadiantSmartThermostat(ClimateEntity):
    """Radiant Smart Sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, device: SmartDevice, data: ClimateData) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._device = device
        self._data = data
        self.entity_id = f"{CLIMATE_DOMAIN}.{self._device.name.lower()}_{self._data.name.lower().replace(' ', '_')}"
        self._attr_unique_id = f"{self._device.name}_{data.name.lower()}"
        self._attr_name = data.name.replace("_", " ")
        self._attr_icon = data.icon
        self._attr_min_temp = data.min_temp.get_value() if data.min_temp is not None else 5.0
        self._attr_max_temp = data.max_temp.get_value() if data.min_temp is not None else 35.0
        self._attr_current_temperature = data.current_temp.get_value()
        self._attr_target_temperature = data.target_temp.get_value()
        self._attr_target_temperature_step = data.target_temp_step
        self._attr_temperature_unit = data.temp_unit
        self._attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF)
        self._attr_hvac_modes = list(data.hvac_modes.values())
        self._attr_hvac_mode = data.hvac_modes.get(data.hvac_mode.get_value())

    async def async_added_to_hass(self) -> None:
        """Run when this entity has been added to HA."""
        self._data.current_temp.add_listener(self._handle_update)
        self._data.target_temp.add_listener(self._handle_update)
        self._data.hvac_mode.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Run when this entity will be removed from HA."""
        self._data.current_temp.remove_listener(self._handle_update)
        self._data.target_temp.remove_listener(self._handle_update)
        self._data.hvac_mode.remove_listener(self._handle_update)

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

    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the water heater on."""
        for v, m in self._data.hvac_modes.items():
            if hvac_mode == m:
                self._data.hvac_mode.set_value(v)

    def turn_on(self) -> None:
        """Turn the water heater on."""
        return

    def turn_off(self) -> None:
        """Turn the water heater on."""
        return

    def _handle_update(self) -> None:
        self._attr_current_temperature = self._data.current_temp.get_value()
        self._attr_target_temperature = self._data.target_temp.get_value()
        self._attr_hvac_mode = self._data.hvac_modes.get(self._data.hvac_mode.get_value())
        self.schedule_update_ha_state()
