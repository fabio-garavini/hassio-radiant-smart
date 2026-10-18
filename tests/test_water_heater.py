"""Tests for the water heater entity logic."""

from conftest import FakeApi
from homeassistant.components.water_heater import STATE_GAS, STATE_OFF

from custom_components.radiant_smart.datapoints import WATER_HEATERS
from custom_components.radiant_smart.models import (
    BoilerMode,
    DataPoint,
    Gateway,
    SmartDevice,
)
from custom_components.radiant_smart.water_heater import (
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    RadiantSmartWaterHeater,
)

HEATING_SPEC = WATER_HEATERS[0]  # radiators
SANITARY_SPEC = WATER_HEATERS[1]  # domestic hot water


def make_water_heater(
    spec=HEATING_SPEC,
    work_mode=BoilerMode.STANDBY,
    min_temp=30.0,
    max_temp=80.0,
    current=45.0,
    target=60.0,
):
    """Create a water heater entity with the given data point values."""
    device = SmartDevice(
        api=FakeApi(),
        device_type=1,
        name="Boiler",
        id="1",
        product_id="p",
        model="TH311",
        gateway=Gateway(uid="gw", product_id="gp"),
        mac_address="aa:bb",
        online=True,
    )

    def point(name, value, point_type=1, index=0):
        return DataPoint(
            device=device,
            index=index,
            name=name,
            point_type=point_type,
            value=value,
        )

    points = {
        "min_temp": point(
            spec.min_temp_key, None if min_temp is None else min_temp * 10, 2
        ),
        "max_temp": point(
            spec.max_temp_key, None if max_temp is None else max_temp * 10, 2
        ),
        "current_temp": point(spec.current_temp_key, current * 10, 2),
        "target_temp": point(spec.target_temp_key, target * 10, 2),
        "work_mode": point(spec.work_mode_key, work_mode),
    }
    entity = RadiantSmartWaterHeater(device, spec, points)
    return entity, device.api


class TestState:
    def test_operation_off_when_standby(self) -> None:
        entity, _ = make_water_heater()
        assert entity._attr_current_operation == STATE_OFF

    def test_operation_gas_for_own_circuit(self) -> None:
        entity, _ = make_water_heater(HEATING_SPEC, work_mode=BoilerMode.HEATING)
        assert entity._attr_current_operation == STATE_GAS
        entity, _ = make_water_heater(SANITARY_SPEC, work_mode=BoilerMode.SANITARY)
        assert entity._attr_current_operation == STATE_GAS

    def test_operation_gas_for_combined_mode(self) -> None:
        entity, _ = make_water_heater(
            SANITARY_SPEC, work_mode=BoilerMode.HEATING_SANITARY
        )
        assert entity._attr_current_operation == STATE_GAS

    def test_operation_off_for_other_circuit(self) -> None:
        entity, _ = make_water_heater(SANITARY_SPEC, work_mode=BoilerMode.HEATING)
        assert entity._attr_current_operation == STATE_OFF

    def test_temperatures(self) -> None:
        entity, _ = make_water_heater()
        assert entity._attr_current_temperature == 45.0
        assert entity._attr_target_temperature == 60.0
        assert entity._attr_min_temp == 30.0
        assert entity._attr_max_temp == 80.0

    def test_temperature_range_fallbacks(self) -> None:
        entity, _ = make_water_heater(min_temp=None, max_temp=None)
        assert entity._attr_min_temp == DEFAULT_MIN_TEMP
        assert entity._attr_max_temp == DEFAULT_MAX_TEMP


class TestCommands:
    async def test_set_temperature(self) -> None:
        entity, api = make_water_heater()
        await entity.async_set_temperature(temperature=65.0)
        assert api.commands == [(entity._device, {"i": 0, "t": 2, "len": 0, "v": 650})]

    async def test_turn_on_from_standby(self) -> None:
        entity, api = make_water_heater(SANITARY_SPEC, work_mode=BoilerMode.STANDBY)
        await entity.async_turn_on()
        assert api.commands == [
            (entity._device, {"i": 0, "t": 1, "len": 0, "v": BoilerMode.SANITARY})
        ]

    async def test_turn_on_while_other_circuit_running(self) -> None:
        """Turning on a circuit while the other one runs switches to the
        combined heating & sanitary mode."""
        entity, api = make_water_heater(HEATING_SPEC, work_mode=BoilerMode.SANITARY)
        await entity.async_turn_on()
        assert api.commands == [
            (
                entity._device,
                {"i": 0, "t": 1, "len": 0, "v": BoilerMode.HEATING_SANITARY},
            )
        ]

    async def test_turn_on_when_already_on(self) -> None:
        entity, api = make_water_heater(HEATING_SPEC, work_mode=BoilerMode.HEATING)
        await entity.async_turn_on()
        assert api.commands == []

    async def test_turn_off_switches_to_other_circuit(self) -> None:
        """Turning off one circuit while both run keeps the other one."""
        entity, api = make_water_heater(
            HEATING_SPEC, work_mode=BoilerMode.HEATING_SANITARY
        )
        await entity.async_turn_off()
        assert api.commands == [
            (entity._device, {"i": 0, "t": 1, "len": 0, "v": BoilerMode.SANITARY})
        ]

    async def test_turn_off_from_own_circuit(self) -> None:
        entity, api = make_water_heater(HEATING_SPEC, work_mode=BoilerMode.HEATING)
        await entity.async_turn_off()
        assert api.commands == [
            (entity._device, {"i": 0, "t": 1, "len": 0, "v": BoilerMode.STANDBY})
        ]

    async def test_operation_mode_mapping(self) -> None:
        entity, api = make_water_heater()
        await entity.async_set_operation_mode(STATE_GAS)
        assert api.commands == [
            (entity._device, {"i": 0, "t": 1, "len": 0, "v": BoilerMode.HEATING})
        ]
