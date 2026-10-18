"""Tests for the Radiant Smart data models."""

import pytest
from conftest import FakeApi

from custom_components.radiant_smart.models import (
    BoilerMode,
    DataPoint,
    Gateway,
    PointType,
    SmartDevice,
)


def make_device(api=None, mac="AA:BB:CC:DD:EE:FF") -> SmartDevice:
    """Create a minimal device for testing."""
    return SmartDevice(
        api=api or FakeApi(),
        device_type=1,
        name="Test Thermostat",
        id="device-1",
        product_id="product",
        model="TH623",
        gateway=Gateway(uid="gateway-uid", product_id="gateway-product"),
        mac_address=mac,
        online=True,
    )


def make_point(device, name, value, point_type=PointType.INTEGER, index=1):
    """Create a data point bound to a device."""
    return DataPoint(
        device=device,
        index=index,
        name=name,
        point_type=point_type,
        value=value,
    )


class TestDataPointDecoding:
    """Tests for DataPoint.get_value."""

    def test_float_x10(self) -> None:
        point = make_point(make_device(), "TEMP", 182, PointType.FLOAT_X10)
        assert point.get_value() == 18.2

    def test_integer(self) -> None:
        point = make_point(make_device(), "MODE", "3", PointType.INTEGER)
        assert point.get_value() == 3

    def test_constant(self) -> None:
        point = make_point(make_device(), "CONST", 7, PointType.CONSTANT)
        assert point.get_value() == 7

    def test_raw_passthrough(self) -> None:
        point = make_point(make_device(), "SOFT", "1.2.3", PointType.RAW)
        assert point.get_value() == "1.2.3"

    def test_invalid_value_returns_none(self) -> None:
        point = make_point(make_device(), "TEMP", "abc", PointType.FLOAT_X10)
        assert point.get_value() is None


class TestDataPointEncoding:
    """Tests for DataPoint.encode_value."""

    def test_float_x10(self) -> None:
        point = make_point(make_device(), "TEMP", 0, PointType.FLOAT_X10)
        assert point.encode_value(20.5) == 205

    def test_integer(self) -> None:
        point = make_point(make_device(), "MODE", 0, PointType.INTEGER)
        assert point.encode_value("3") == 3

    def test_raw_passthrough(self) -> None:
        point = make_point(make_device(), "SOFT", "", PointType.RAW)
        assert point.encode_value("9.9.9") == "9.9.9"


class TestAsyncSetValue:
    """Tests for the optimistic-but-ordered value update flow."""

    async def test_value_applied_after_send(self) -> None:
        device = make_device()
        api = device.api
        point = make_point(device, "TEMP", 100, PointType.FLOAT_X10, index=42)

        await point.async_set_value(20.5)

        # The encoded value was sent to the device...
        assert api.commands == [
            (device, {"i": 42, "t": PointType.FLOAT_X10, "len": 0, "v": 205})
        ]
        # ...and only applied afterwards.
        assert point.value == 205

    async def test_value_unchanged_when_send_fails(self) -> None:
        device = make_device()

        class FailingApi(FakeApi):
            async def async_send_mqtt_command(self, device, command):
                raise RuntimeError("broker gone")

        device.api = FailingApi()
        point = make_point(device, "TEMP", 100, PointType.FLOAT_X10)

        with pytest.raises(RuntimeError, match="broker gone"):
            await point.async_set_value(20.5)
        assert point.value == 100

    async def test_listeners_notified(self) -> None:
        device = make_device()
        point = make_point(device, "MODE", 0)
        calls = []
        point.add_listener(lambda: calls.append(point.get_value()))

        await point.async_set_value(2)

        assert calls == [2]


class TestHandleUpdate:
    """Tests for MQTT-driven data point updates."""

    def test_handle_mqtt_command(self) -> None:
        device = make_device()
        point = make_point(device, "SYS_WORK_MODE", 0)
        device.data_points["SYS_WORK_MODE"] = point
        notified = []
        point.add_listener(lambda: notified.append(point.get_value()))

        device.handle_mqtt_command({"SYS_WORK_MODE": {"i": 1, "t": 1, "v": 2}})

        assert point.value == 2
        assert notified == [2]

    def test_handle_mqtt_command_unknown_point(self) -> None:
        device = make_device()
        # Must not raise for unknown points or malformed updates
        device.handle_mqtt_command({"PARAM_UNKNOWN": {"v": 1}})
        device.handle_mqtt_command({"PARAM_NODATA": "not-a-dict"})

    def test_boiler_mode_values(self) -> None:
        """The boiler mode enum must match the protocol values."""
        assert BoilerMode.STANDBY == 0
        assert BoilerMode.SANITARY == 2
        assert BoilerMode.HEATING == 3
        assert BoilerMode.HEATING_SANITARY == 10


class TestFromApiResponse:
    """Tests for building devices from cloud API responses."""

    def test_valid_response(self) -> None:
        device = SmartDevice.from_api_response(
            FakeApi(),
            {
                "deviceType": 2,
                "deviceName": "Thermostat",
                "id": "42",
                "productId": "product",
                "model": "TH623",
                "gateway": {"uid": "gw", "productId": "gw-product"},
                "extAddr": "aa:bb",
                "online": True,
                "pointDataMap": {
                    "SYS_WORK_MODE": {"pointIndex": 1, "pointType": 1, "value": 3},
                    "BAD": "not-a-dict",
                },
            },
        )
        assert device is not None
        assert device.name == "Thermostat"
        assert device.gateway.uid == "gw"
        assert set(device.data_points) == {"SYS_WORK_MODE"}

    def test_missing_gateway(self) -> None:
        assert (
            SmartDevice.from_api_response(
                FakeApi(), {"deviceName": "x", "extAddr": "aa"}
            )
            is None
        )

    def test_missing_mac(self) -> None:
        assert (
            SmartDevice.from_api_response(
                FakeApi(),
                {"deviceName": "x", "gateway": {"uid": "gw"}},
            )
            is None
        )
