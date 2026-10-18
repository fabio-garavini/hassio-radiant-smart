"""Data models for the Radiant Smart integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate import HVACMode
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import EntityCategory

if TYPE_CHECKING:
    from .api import TopbandCloudApi

_LOGGER = logging.getLogger(__name__)

PointListener = Callable[[], None]


class PointType(IntEnum):
    """Topband data point types."""

    INTEGER = 1  # int, enum or bool
    FLOAT_X10 = 2  # float, raw value is x10
    CONSTANT = 7  # constant value (read only)
    RAW = 8  # raw hex string


class BoilerMode(IntEnum):
    """Boiler work modes (SYS_WORK_MODE)."""

    STANDBY = 0
    SANITARY = 2
    HEATING = 3
    HEATING_SANITARY = 10


class BoilerFunction(IntFlag):
    """Boiler functions (PARAM_ID_BOILER_OT_SLAVE_STATUS bitfield)."""

    IDLE = 0
    UNKNOWN_1 = 1
    RADIATOR = 2
    DOMESTIC = 4
    FLAME = 8
    UNKNOWN_5 = 16
    UNKNOWN_6 = 32
    UNKNOWN_7 = 64
    UNKNOWN_8 = 128


@dataclass
class Gateway:
    """Radiant Smart gateway (the boiler controller)."""

    uid: str
    product_id: str


@dataclass
class DataPoint:
    """A single Topband data point of a device."""

    device: SmartDevice
    index: int
    name: str
    point_type: int
    value: Any = None
    _listeners: set[PointListener] = field(
        default_factory=set, repr=False, compare=False
    )

    def add_listener(self, callback: PointListener) -> None:
        """Register an entity update callback."""
        self._listeners.add(callback)

    def remove_listener(self, callback: PointListener) -> None:
        """Unregister an entity update callback."""
        self._listeners.discard(callback)

    def notify_listeners(self) -> None:
        """Notify all registered listeners (must run on the event loop)."""
        for callback in list(self._listeners):
            callback()

    def get_value(self) -> Any:
        """Return the decoded value."""
        try:
            match self.point_type:
                case PointType.FLOAT_X10:
                    return float(self.value) / 10
                case PointType.INTEGER | PointType.CONSTANT:
                    return int(self.value)
                case _:
                    return self.value
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid value %r for data point %s", self.value, self.name)
            return None

    def encode_value(self, value: Any) -> Any:
        """Encode a value for sending to the device."""
        match self.point_type:
            case PointType.FLOAT_X10:
                return round(float(value) * 10)
            case PointType.INTEGER | PointType.CONSTANT:
                return int(value)
            case _:
                return value

    async def async_set_value(self, value: Any) -> None:
        """Send a new value to the device.

        The value is applied and propagated only after the command was
        handed to the broker; the device echo (cmd 98) confirms or corrects
        it afterwards.
        """
        encoded = self.encode_value(value)
        await self.device.async_send_data_point(self, encoded)
        self.value = encoded
        self.notify_listeners()

    def handle_update(self, value: Any) -> None:
        """Update the value from MQTT and notify listeners."""
        self.value = value
        self.notify_listeners()


@dataclass
class SmartDevice:
    """Radiant Smart device."""

    api: TopbandCloudApi
    device_type: int
    name: str
    id: str
    product_id: str
    model: str
    gateway: Gateway
    mac_address: str
    online: bool
    data_points: dict[str, DataPoint] = field(default_factory=dict)
    # Entity configs, populated by datapoints.build_entity_configs()
    water_heaters: list[tuple[WaterHeaterSpec, dict[str, DataPoint]]] = field(
        default_factory=list
    )
    climates: list[tuple[ClimateSpec, dict[str, DataPoint]]] = field(
        default_factory=list
    )
    selects: list[tuple[SelectSpec, DataPoint]] = field(default_factory=list)
    switches: list[tuple[SwitchSpec, DataPoint]] = field(default_factory=list)
    numbers: list[tuple[NumberSpec, DataPoint]] = field(default_factory=list)
    buttons: list[tuple[ButtonSpec, DataPoint]] = field(default_factory=list)
    binary_sensors: list[tuple[BinarySensorSpec, DataPoint]] = field(
        default_factory=list
    )
    sensors: list[tuple[SensorSpec, DataPoint]] = field(default_factory=list)
    unknown_points: dict[str, DataPoint] = field(default_factory=dict)

    @classmethod
    def from_api_response(
        cls, api: TopbandCloudApi, source: dict[str, Any]
    ) -> SmartDevice | None:
        """Create a device from a Topband cloud device list entry.

        Returns None for entries that cannot be used, e.g. without a
        gateway or MAC address (they could never receive commands or
        updates).
        """
        gateway = source.get("gateway") or {}
        mac_address = str(source.get("extAddr") or "").strip()
        if not gateway.get("uid") or not mac_address:
            _LOGGER.warning(
                "Skipping device %s without gateway or MAC address",
                source.get("deviceName") or source.get("model") or source.get("id"),
            )
            return None

        device = cls(
            api=api,
            device_type=source.get("deviceType", -1),
            name=source.get("deviceName") or source.get("model") or "Unknown",
            id=source.get("id"),
            product_id=source.get("productId"),
            model=source.get("model"),
            gateway=Gateway(
                uid=gateway.get("uid"),
                product_id=gateway.get("productId"),
            ),
            mac_address=mac_address,
            online=bool(source.get("online")),
        )
        device.data_points = {
            point_name: DataPoint(
                device=device,
                index=point.get("pointIndex"),
                name=point_name,
                point_type=point.get("pointType"),
                value=point.get("value"),
            )
            for point_name, point in (source.get("pointDataMap") or {}).items()
            if isinstance(point, dict)
        }
        return device

    def handle_mqtt_command(self, command: dict[str, dict[str, Any]]) -> None:
        """Handle a cmd 98 status update from MQTT (must run on the event loop)."""
        for point_name, data in command.items():
            data_point = self.data_points.get(point_name)
            if data_point is None:
                _LOGGER.warning(
                    "%s: received unknown data point %s", self.name, point_name
                )
                continue
            if not isinstance(data, dict) or "v" not in data:
                _LOGGER.warning(
                    "%s: received invalid update for data point %s",
                    self.name,
                    point_name,
                )
                continue
            data_point.handle_update(data["v"])

    async def async_send_data_point(self, data_point: DataPoint, value: Any) -> None:
        """Send a data point update to the device."""
        command = {
            "i": data_point.index,
            "t": data_point.point_type,
            "len": 0,
            "v": value,
        }
        await self.api.async_send_mqtt_command(self, command)


@dataclass(frozen=True)
class ButtonSpec:
    """Configuration for a button entity."""

    key: str
    name: str
    icon: str | None = None
    entity_category: EntityCategory | None = None
    press_value: Any = 1


@dataclass(frozen=True)
class SensorSpec:
    """Configuration for a sensor entity."""

    key: str
    name: str
    icon: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    unit: str | None = None
    entity_category: EntityCategory | None = None
    options: dict[int, str] | None = None
    suggested_display_precision: int | None = None
    enabled_default: bool = True


@dataclass(frozen=True)
class BinarySensorSpec:
    """Configuration for a binary sensor entity."""

    key: str
    name: str
    icon: str | None = None
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None
    # Maps data point values to on/off states
    options: dict[int, int] | None = None
    # If set, the sensor is on when (value & bit_mask) is non-zero
    bit_mask: int | None = None


@dataclass(frozen=True)
class SwitchSpec:
    """Configuration for a switch entity."""

    key: str
    name: str
    icon: str | None = None
    device_class: SwitchDeviceClass | None = None


@dataclass(frozen=True)
class SelectSpec:
    """Configuration for a select entity."""

    key: str
    name: str
    icon: str | None = None
    options: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NumberSpec:
    """Configuration for a number entity."""

    key: str
    name: str
    icon: str | None = None
    device_class: NumberDeviceClass | None = None
    mode: NumberMode = NumberMode.AUTO
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class WaterHeaterSpec:
    """Configuration for a water heater entity."""

    key: str
    name: str
    icon: str | None = None
    # Data point names required for this water heater to exist
    required_keys: tuple[str, ...] = ()
    min_temp_key: str = ""
    max_temp_key: str = ""
    current_temp_key: str = ""
    target_temp_key: str = ""
    work_mode_key: str = ""
    work_type: BoilerMode = BoilerMode.HEATING


@dataclass(frozen=True)
class ClimateSpec:
    """Configuration for a climate entity."""

    key: str
    name: str
    icon: str | None = None
    required_keys: tuple[str, ...] = ()
    current_temp_key: str = ""
    target_temp_key: str = ""
    override_enable_key: str = ""
    override_temp_key: str = ""
    hvac_mode_key: str = ""
    current_action_key: str = ""
    hvac_modes: dict[int, HVACMode] = field(default_factory=dict)
    min_temp: float = 5.0
    max_temp: float = 35.0
    target_temp_step: float = 0.5
