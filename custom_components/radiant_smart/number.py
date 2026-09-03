"""Number platform for the Radiant Smart integration."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import NumberSpec
from .entity import RadiantSmartEntity
from .models import DataPoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart numbers from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartNumber] = []
    for device in api.devices.values():
        for spec, data_point in device.numbers:
            entities.append(RadiantSmartNumber(device, spec, data_point))

    _LOGGER.debug("Adding %d number entities", len(entities))
    async_add_entities(entities)


class RadiantSmartNumber(RadiantSmartEntity, NumberEntity):
    """Representation of a Radiant Smart number."""

    def __init__(
        self, device, spec: NumberSpec, data_point: DataPoint
    ) -> None:
        """Initialize the number."""
        self._device = device
        self._spec = spec
        self._data_point = data_point
        self._attr_unique_id = f"{device.mac_address}_{data_point.index}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_mode = spec.mode
        self._attr_device_class = spec.device_class
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_native_min_value = spec.min_value
        self._attr_native_max_value = spec.max_value
        self._attr_native_step = spec.step
        self._attr_native_value = data_point.get_value()

    async def async_added_to_hass(self) -> None:
        """Register the data point listener when added to hass."""
        self._data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the data point listener when removed from hass."""
        self._data_point.remove_listener(self._handle_update)

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._attr_native_value = self._data_point.get_value()
        self.schedule_update_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set a new value."""
        await self._data_point.async_set_value(value)
