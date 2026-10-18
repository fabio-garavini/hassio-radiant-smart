"""Climate platform for the Radiant Smart integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import ClimateSpec
from .entity import RadiantSmartEntity
from .models import DataPoint, SmartDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart climates from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartThermostat] = []
    for device in api.devices.values():
        for spec, points in device.climates:
            entities.append(RadiantSmartThermostat(device, spec, points))

    _LOGGER.debug("Adding %d climate entities", len(entities))
    async_add_entities(entities)


class RadiantSmartThermostat(RadiantSmartEntity, ClimateEntity):
    """Representation of a Radiant Smart thermostat."""

    def __init__(
        self,
        device: SmartDevice,
        spec: ClimateSpec,
        points: dict[str, DataPoint],
    ) -> None:
        """Initialize the thermostat."""
        self._device = device
        self._spec = spec
        self._points = points
        self._attr_unique_id = f"{device.mac_address}_{spec.key}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = spec.min_temp
        self._attr_max_temp = spec.max_temp
        self._attr_target_temperature_step = spec.target_temp_step
        self._attr_hvac_modes = list(spec.hvac_modes.values())
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._update_attrs()

    async def async_added_to_hass(self) -> None:
        """Register data point listeners when added to hass."""
        for data_point in self._points.values():
            data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove data point listeners when removed from hass."""
        for data_point in self._points.values():
            data_point.remove_listener(self._handle_update)

    def _update_attrs(self) -> None:
        """Update all state attributes from the data points."""
        hvac_mode = self._spec.hvac_modes.get(self._points["hvac_mode"].get_value())
        self._attr_hvac_mode = hvac_mode
        self._attr_hvac_action = (
            HVACAction.HEATING
            if self._points["current_action"].get_value()
            else HVACAction.IDLE
        )
        # The room temperature is always the measured current temperature.
        self._attr_current_temperature = self._points["current_temp"].get_value()
        # In AUTO mode with an override active, the override temperature is
        # the effective target temperature.
        if hvac_mode == HVACMode.AUTO and self._points["override_enable"].get_value():
            self._attr_target_temperature = self._points["override_temp"].get_value()
        else:
            self._attr_target_temperature = self._points["target_temp"].get_value()

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._update_attrs()
        self.schedule_update_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self._attr_hvac_mode == HVACMode.AUTO:
            # Set the override temperature before enabling it, so the
            # override takes effect with the new value.
            await self._points["override_temp"].async_set_value(temperature)
            await self._points["override_enable"].async_set_value(1)
        else:
            await self._points["target_temp"].async_set_value(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        for value, mode in self._spec.hvac_modes.items():
            if mode == hvac_mode:
                await self._points["hvac_mode"].async_set_value(value)
                return
        _LOGGER.warning("Unsupported HVAC mode: %s", hvac_mode)

    async def async_turn_on(self) -> None:
        """Turn the thermostat on (auto mode)."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the thermostat off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
