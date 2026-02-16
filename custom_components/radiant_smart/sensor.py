"""Sensor platform for Radiant Smart integration."""

import logging

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TopbandConfigEntry
from .api import SensorData
from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: TopbandConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Sensor entry setup."""
    hub = entry.runtime_data

    entities: list[RadiantSmartSensor] = []

    for device in hub.devices.values():
        for sensor in device.sensors_data:
            _LOGGER.debug("RADIANT: Setting up sensor: %s", sensor.name)
            entities.append(RadiantSmartSensor(sensor))

        for point in device.unknown_points.values():
            _LOGGER.debug("RADIANT: Setting up data point: %s", point.name)
            entities.append(
                RadiantSmartSensor(
                    SensorData(
                        data_point=point,
                        name=point.name,
                    )
                )
            )

    _LOGGER.debug("RADIANT: Adding %d sensor entities", len(entities))

    async_add_entities(entities)

class RadiantSmartSensor(SensorEntity):
    """Radiant Smart Sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, data: SensorData) -> None:
        """Initialize the sensor."""
        self._data = data
        self.entity_id = f"{SENSOR_DOMAIN}.{self._data.data_point.device.name.lower()}_{self._data.name.lower().replace(' ', '_')}"
        self._attr_unique_id = f"{self._data.data_point.device.name}_{self._data.name.lower().replace(" ", "_")}"
        self._attr_name = self._data.name.replace("_", " ")
        self._attr_icon = self._data.icon
        self._attr_native_value = self._data.options.get(self._data.data_point.get_value()) if self._data.options is not None and self._data.options.get(self._data.data_point.get_value()) is not None else self._data.data_point.get_value()
        self._attr_device_class = self._data.device_class
        self._attr_state_class = self._data.state_class
        self._attr_available = self._data.data_point.device.online
        self._attr_native_unit_of_measurement = self._data.unit_of_measurement
        self._attr_options = list(self._data.options.values()) if self._data.options is not None else None
        self._attr_entity_category = self._data.entity_category

    async def async_added_to_hass(self) -> None:
        """Run when this entity has been added to HA."""
        self._data.data_point.add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Run when this entity will be removed from HA."""
        self._data.data_point.remove_listener(self._handle_update)

    @property
    def device_info(self) -> DeviceInfo:
        """Information about this entity/device."""
        return {"identifiers": {(DOMAIN, self._data.data_point.device.product_id)}}

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        return self._data.data_point.device.online

    def _handle_update(self) -> None:
        self._attr_native_value = self._data.options.get(self._data.data_point.get_value()) if self._data.options is not None and self._data.options.get(self._data.data_point.get_value()) is not None else self._data.data_point.get_value()
        self.schedule_update_ha_state()
