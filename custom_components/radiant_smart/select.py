"""Select platform for the Radiant Smart integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import SelectSpec
from .entity import RadiantSmartEntity
from .models import DataPoint

if TYPE_CHECKING:
    from .models import SmartDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart selects from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartSelect] = []
    for device in api.devices.values():
        for spec, data_point in device.selects:
            entities.append(RadiantSmartSelect(device, spec, data_point))

    _LOGGER.debug("Adding %d select entities", len(entities))
    async_add_entities(entities)


class RadiantSmartSelect(RadiantSmartEntity, SelectEntity):
    """Representation of a Radiant Smart select."""

    def __init__(
        self,
        device: SmartDevice,
        spec: SelectSpec,
        data_point: DataPoint,
    ) -> None:
        """Initialize the select."""
        self._device = device
        self._spec = spec
        self._data_point = data_point
        self._attr_unique_id = f"{device.mac_address}_{data_point.index}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_options = list(spec.options.values())
        self._update_current_option()

    async def async_added_to_hass(self) -> None:
        """Register the data point listener when added to hass."""
        self._data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the data point listener when removed from hass."""
        self._data_point.remove_listener(self._handle_update)

    def _update_current_option(self) -> None:
        """Compute the current option from the data point."""
        self._attr_current_option = self._spec.options.get(self._data_point.get_value())

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._update_current_option()
        self.schedule_update_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        for value, label in self._spec.options.items():
            if option == label:
                await self._data_point.async_set_value(value)
                return
        _LOGGER.warning("Invalid option %s for %s", option, self._spec.name)
