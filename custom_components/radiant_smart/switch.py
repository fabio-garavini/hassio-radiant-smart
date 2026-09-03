"""Switch platform for the Radiant Smart integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import SwitchSpec
from .entity import RadiantSmartEntity
from .models import DataPoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart switches from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartSwitch] = []
    for device in api.devices.values():
        for spec, data_point in device.switches:
            entities.append(RadiantSmartSwitch(device, spec, data_point))

    _LOGGER.debug("Adding %d switch entities", len(entities))
    async_add_entities(entities)


class RadiantSmartSwitch(RadiantSmartEntity, SwitchEntity):
    """Representation of a Radiant Smart switch."""

    def __init__(
        self, device, spec: SwitchSpec, data_point: DataPoint
    ) -> None:
        """Initialize the switch."""
        self._device = device
        self._spec = spec
        self._data_point = data_point
        self._attr_unique_id = f"{device.mac_address}_{data_point.index}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class
        self._attr_is_on = bool(data_point.get_value())

    async def async_added_to_hass(self) -> None:
        """Register the data point listener when added to hass."""
        self._data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the data point listener when removed from hass."""
        self._data_point.remove_listener(self._handle_update)

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._attr_is_on = bool(self._data_point.get_value())
        self.schedule_update_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._data_point.async_set_value(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._data_point.async_set_value(0)
