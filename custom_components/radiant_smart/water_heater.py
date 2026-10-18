"""Water heater platform for the Radiant Smart integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    STATE_GAS,
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import WaterHeaterSpec
from .entity import RadiantSmartEntity
from .models import BoilerMode, DataPoint, SmartDevice

_LOGGER = logging.getLogger(__name__)

# Fallbacks used when the device has not reported its configured range yet
DEFAULT_MIN_TEMP = 30.0
DEFAULT_MAX_TEMP = 80.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart water heaters from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartWaterHeater] = []
    for device in api.devices.values():
        for spec, points in device.water_heaters:
            entities.append(RadiantSmartWaterHeater(device, spec, points))

    _LOGGER.debug("Adding %d water heater entities", len(entities))
    async_add_entities(entities)


class RadiantSmartWaterHeater(RadiantSmartEntity, WaterHeaterEntity):
    """Representation of a Radiant Smart water heater."""

    def __init__(
        self,
        device: SmartDevice,
        spec: WaterHeaterSpec,
        points: dict[str, DataPoint],
    ) -> None:
        """Initialize the water heater."""
        self._device = device
        self._spec = spec
        self._points = points
        self._attr_unique_id = f"{device.mac_address}_{spec.key}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = self._get_range_value("min_temp", DEFAULT_MIN_TEMP)
        self._attr_max_temp = self._get_range_value("max_temp", DEFAULT_MAX_TEMP)
        self._attr_target_temperature_step = 1.0
        self._attr_operation_list = [STATE_OFF, STATE_GAS]
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.ON_OFF
            | WaterHeaterEntityFeature.OPERATION_MODE
        )
        self._update_attrs()

    def _get_range_value(self, key: str, default: float) -> float:
        """Return a configured temperature limit with a safe fallback."""
        value = self._points[key].get_value()
        return value if isinstance(value, (int, float)) and value > 0 else default

    async def async_added_to_hass(self) -> None:
        """Register data point listeners when added to hass."""
        for data_point in self._points.values():
            data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove data point listeners when removed from hass."""
        for data_point in self._points.values():
            data_point.remove_listener(self._handle_update)

    def _update_attrs(self) -> None:
        """Update state attributes from the data points."""
        self._attr_current_temperature = self._points["current_temp"].get_value()
        self._attr_target_temperature = self._points["target_temp"].get_value()
        self._attr_current_operation = self._parse_current_operation()

    def _parse_current_operation(self) -> str:
        """Return the current operation based on the boiler work mode."""
        mode = self._points["work_mode"].get_value()
        if mode is not None and mode in (
            BoilerMode.HEATING_SANITARY,
            self._spec.work_type,
        ):
            return STATE_GAS
        return STATE_OFF

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._update_attrs()
        self.schedule_update_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._points["target_temp"].async_set_value(temperature)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the water heater on."""
        mode = self._points["work_mode"].get_value()
        if mode == BoilerMode.STANDBY:
            await self._points["work_mode"].async_set_value(self._spec.work_type)
        elif mode in (BoilerMode.HEATING_SANITARY, self._spec.work_type):
            return
        else:
            await self._points["work_mode"].async_set_value(BoilerMode.HEATING_SANITARY)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the water heater off."""
        mode = self._points["work_mode"].get_value()
        if mode == self._spec.work_type:
            await self._points["work_mode"].async_set_value(BoilerMode.STANDBY)
        elif mode == BoilerMode.HEATING_SANITARY:
            # Switch to the other circuit only, keeping it running
            if self._spec.work_type == BoilerMode.SANITARY:
                await self._points["work_mode"].async_set_value(BoilerMode.HEATING)
            else:
                await self._points["work_mode"].async_set_value(BoilerMode.SANITARY)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set the operation mode."""
        if operation_mode == STATE_GAS:
            await self.async_turn_on()
        else:
            await self.async_turn_off()
