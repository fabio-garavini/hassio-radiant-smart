"""Tests for the binary sensor value mapping."""

from conftest import FakeApi

from custom_components.radiant_smart.binary_sensor import RadiantSmartBinarySensor
from custom_components.radiant_smart.datapoints import (
    BINARY_SENSOR_SPECS,
    BINARY_SENSOR_SUFFIX_SPECS,
    OT_SLAVE_STATUS_BINARY_SPECS,
)
from custom_components.radiant_smart.models import (
    BoilerFunction,
    DataPoint,
    Gateway,
    SmartDevice,
)

FLAME_SPEC = OT_SLAVE_STATUS_BINARY_SPECS[0]
RELAY_SPEC = BINARY_SENSOR_SUFFIX_SPECS[0][1]


def make_device() -> SmartDevice:
    return SmartDevice(
        api=FakeApi(),
        device_type=0,
        name="Boiler",
        id="1",
        product_id="p",
        model="TH311",
        gateway=Gateway(uid="gw", product_id="gp"),
        mac_address="aa:bb",
        online=True,
    )


def make_sensor(spec, value):
    device = make_device()
    point = DataPoint(device=device, index=1, name="POINT", point_type=1, value=value)
    return RadiantSmartBinarySensor(device, spec, point)


class TestBitMaskMapping:
    """The OT slave status feeds the flame / water flow binary sensors."""

    def test_flame_mask(self) -> None:
        assert FLAME_SPEC.bit_mask == BoilerFunction.FLAME

    def test_flame_on(self) -> None:
        assert make_sensor(FLAME_SPEC, BoilerFunction.FLAME)._attr_is_on is True

    def test_flame_off(self) -> None:
        assert make_sensor(FLAME_SPEC, 0)._attr_is_on is False

    def test_combined_status(self) -> None:
        """Status 12 = domestic water (4) + flame (8)."""
        assert make_sensor(FLAME_SPEC, 12)._attr_is_on is True

    def test_unparseable_value_reports_unknown(self) -> None:
        assert make_sensor(FLAME_SPEC, "not-a-number")._attr_is_on is None


class TestOptionMapping:
    """The relay state maps 1 -> off, 2 -> on."""

    def test_relay_on(self) -> None:
        assert make_sensor(RELAY_SPEC, 2)._attr_is_on is True

    def test_relay_off(self) -> None:
        assert make_sensor(RELAY_SPEC, 1)._attr_is_on is False

    def test_relay_unknown_value(self) -> None:
        """A value outside the known mapping reports unknown, not a guess."""
        assert make_sensor(RELAY_SPEC, 3)._attr_is_on is None


class TestSimpleBoolMapping:
    SPEC = BINARY_SENSOR_SPECS["PARAM_ID_BOILER_OTC_ENABLE"]

    def test_on(self) -> None:
        assert make_sensor(self.SPEC, 1)._attr_is_on is True

    def test_off(self) -> None:
        assert make_sensor(self.SPEC, 0)._attr_is_on is False
