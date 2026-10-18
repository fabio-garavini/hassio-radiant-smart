"""Binary sensor platform for the Radiant Smart integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import BinarySensorSpec
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
    """Set up Radiant Smart binary sensors from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartBinarySensor] = []
    for device in api.devices.values():
        for spec, data_point in device.binary_sensors:
            entities.append(RadiantSmartBinarySensor(device, spec, data_point))

    _LOGGER.debug("Adding %d binary sensor entities", len(entities))
    async_add_entities(entities)


class RadiantSmartBinarySensor(RadiantSmartEntity, BinarySensorEntity):
    """Representation of a Radiant Smart binary sensor."""

    def __init__(
        self,
        device: SmartDevice,
        spec: BinarySensorSpec,
        data_point: DataPoint,
    ) -> None:
        """Initialize the binary sensor."""
        self._device = device
        self._spec = spec
        self._data_point = data_point
        self._attr_unique_id = f"{device.mac_address}_{spec.key}_{data_point.index}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        self._update_is_on()

    async def async_added_to_hass(self) -> None:
        """Register the data point listener when added to hass."""
        self._data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the data point listener when removed from hass."""
        self._data_point.remove_listener(self._handle_update)

    def _update_is_on(self) -> None:
        """Compute the on state from the data point."""
        value = self._data_point.get_value()
        if value is None:
            self._attr_is_on = None
        elif self._spec.bit_mask is not None:
            self._attr_is_on = bool(value & self._spec.bit_mask)
        elif (options := self._spec.options) is not None:
            if value in options:
                self._attr_is_on = bool(options[value])
            else:
                # A value outside the known mapping is reported as unknown
                self._attr_is_on = None
        else:
            self._attr_is_on = bool(value)

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._update_is_on()
        self.schedule_update_ha_state()
