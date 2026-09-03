"""Sensor platform for the Radiant Smart integration."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .datapoints import SensorSpec
from .entity import RadiantSmartEntity
from .models import DataPoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TopbandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Radiant Smart sensors from a config entry."""
    api = entry.runtime_data

    entities: list[RadiantSmartSensor] = []
    for device in api.devices.values():
        for spec, data_point in device.sensors:
            entities.append(RadiantSmartSensor(device, spec, data_point))
        for data_point in device.unknown_points.values():
            _LOGGER.debug(
                "Exposing unknown data point: %s", data_point.name
            )
            entities.append(
                RadiantSmartSensor(device, _unknown_spec(data_point), data_point)
            )

    _LOGGER.debug("Adding %d sensor entities", len(entities))
    async_add_entities(entities)


def _unknown_spec(data_point: DataPoint) -> SensorSpec:
    """Build a diagnostic spec for a data point without a known mapping.

    Unknown points are disabled by default to avoid diagnostic clutter;
    they can be enabled per entity in the device page.
    """
    return SensorSpec(
        key=f"unknown_{data_point.index}",
        name=data_point.name,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    )


class RadiantSmartSensor(RadiantSmartEntity, SensorEntity):
    """Representation of a Radiant Smart sensor."""

    def __init__(
        self, device, spec: SensorSpec, data_point: DataPoint
    ) -> None:
        """Initialize the sensor."""
        self._device = device
        self._spec = spec
        self._data_point = data_point
        self._attr_unique_id = f"{device.mac_address}_{data_point.index}"
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_entity_category = spec.entity_category
        self._attr_entity_registry_enabled_default = spec.enabled_default
        self._attr_suggested_display_precision = (
            spec.suggested_display_precision
        )
        if spec.options is not None:
            self._attr_options = list(spec.options.values())
        self._update_native_value()

    async def async_added_to_hass(self) -> None:
        """Register the data point listener when added to hass."""
        self._data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the data point listener when removed from hass."""
        self._data_point.remove_listener(self._handle_update)

    def _update_native_value(self) -> None:
        """Compute the native value from the data point."""
        value = self._data_point.get_value()
        options = self._spec.options
        if options is not None:
            if value in options:
                self._attr_native_value = options[value]
            elif self._spec.device_class == SensorDeviceClass.ENUM:
                # Enum sensors must only report values from their options
                _LOGGER.warning(
                    "Unknown value %s for enum sensor %s", value, self._spec.name
                )
                self._attr_native_value = None
            else:
                self._attr_native_value = value
        else:
            self._attr_native_value = value

    def _handle_update(self) -> None:
        """Handle a data point update."""
        self._update_native_value()
        self.schedule_update_ha_state()
