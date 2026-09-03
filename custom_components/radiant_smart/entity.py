"""Shared base entity for the Radiant Smart integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from .models import SmartDevice


class RadiantSmartEntity:
    """Mixin providing common device info and availability."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    _device: SmartDevice

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.mac_address.upper())},
            manufacturer=MANUFACTURER,
            name=self._device.name,
            model=self._device.model,
        )

    @property
    def available(self) -> bool:
        """Return True if the device is online."""
        return self._device.online
