"""Button platform for the Radiant Smart integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import ButtonSpec
from .entity import RadiantSmartEntity
from .models import DataPoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart buttons from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartButton] = []
    for device in api.devices.values():
        for spec, data_point in device.buttons:
            entities.append(RadiantSmartButton(device, spec, data_point))

    _LOGGER.debug("Adding %d button entities", len(entities))
    async_add_entities(entities)


class RadiantSmartButton(RadiantSmartEntity, ButtonEntity):
    """Representation of a Radiant Smart button."""

    def __init__(
        self, device, spec: ButtonSpec, data_point: DataPoint
    ) -> None:
        """Initialize the button."""
        self._device = device
        self._spec = spec
        self._data_point = data_point
        self._attr_unique_id = f"{device.mac_address}_{spec.key}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_entity_category = spec.entity_category

    async def async_press(self) -> None:
        """Press the button."""
        await self._data_point.async_set_value(self._spec.press_value)
