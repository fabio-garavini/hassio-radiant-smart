"""Tests for the climate entity logic."""

from conftest import FakeApi
from homeassistant.components.climate import HVACAction, HVACMode

from custom_components.radiant_smart.climate import RadiantSmartThermostat
from custom_components.radiant_smart.datapoints import CLIMATES
from custom_components.radiant_smart.models import DataPoint, Gateway, SmartDevice

SPEC = CLIMATES[0]


def make_thermostat(
    room_temp=20.5,
    target_temp=21.0,
    override_enable=0,
    override_temp=22.0,
    work_mode=0,
    heating=1,
):
    """Create a thermostat with the given data point values."""
    device = SmartDevice(
        api=FakeApi(),
        device_type=1,
        name="Thermostat",
        id="1",
        product_id="p",
        model="TH623",
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

    entity = RadiantSmartThermostat(
        device,
        SPEC,
        {
            "current_temp": point("PARAM_ID_TH_CUR_ROOM_TEMP", room_temp * 10, 2),
            "target_temp": point("PARAM_ID_TH_TRG_ROOM_TEMP", target_temp * 10, 2),
            "override_enable": point("PARAM_ID_TH_OVERRIDE_ENABLE", override_enable),
            "override_temp": point("PARAM_ID_TH_OVERRIDE_TEMP", override_temp * 10, 2),
            "hvac_mode": point("PARAM_ID_TH_WORK_MODE", work_mode),
            "current_action": point("PARAM_ID_TH_CH_STATUS", heating),
        },
    )
    return entity, device.api


class TestState:
    def test_auto_mode_without_override(self) -> None:
        entity, _ = make_thermostat()
        assert entity._attr_hvac_mode == HVACMode.AUTO
        assert entity._attr_hvac_action == HVACAction.HEATING
        assert entity._attr_current_temperature == 20.5
        assert entity._attr_target_temperature == 21.0

    def test_auto_mode_with_override_shows_override_target(self) -> None:
        """Regression: the room temperature must stay the measured one.

        The old implementation replaced current_temperature with the
        override target in auto mode.
        """
        entity, _ = make_thermostat(override_enable=1)
        assert entity._attr_current_temperature == 20.5  # room temp!
        assert entity._attr_target_temperature == 22.0

    def test_heat_mode_ignores_override(self) -> None:
        entity, _ = make_thermostat(work_mode=1, override_enable=1)
        assert entity._attr_hvac_mode == HVACMode.HEAT
        assert entity._attr_target_temperature == 21.0

    def test_unknown_work_mode(self) -> None:
        entity, _ = make_thermostat(work_mode=99)
        assert entity._attr_hvac_mode is None

    def test_idle_action(self) -> None:
        entity, _ = make_thermostat(heating=0)
        assert entity._attr_hvac_action == HVACAction.IDLE


class TestSetTemperature:
    async def test_auto_mode_sets_override_then_enables_it(self) -> None:
        entity, api = make_thermostat(override_enable=0)
        await entity.async_added_to_hass()
        # The HA state machinery is not under test here
        state_updates: list[int] = []
        entity.schedule_update_ha_state = lambda: state_updates.append(  # type: ignore[method-assign]
            1
        )

        await entity.async_set_temperature(temperature=23.5)

        # The override temperature must be sent before enabling the
        # override so it never flashes the previous value.
        assert api.commands == [
            (entity._device, {"i": 0, "t": 2, "len": 0, "v": 235}),
            (entity._device, {"i": 0, "t": 1, "len": 0, "v": 1}),
        ]
        # The listener wiring recomputes the state from the data points
        assert state_updates  # both points notified their listeners
        assert entity._attr_target_temperature == 23.5

    async def test_heat_mode_sets_schedule_target(self) -> None:
        entity, api = make_thermostat(work_mode=1)

        await entity.async_set_temperature(temperature=25.0)

        assert api.commands == [(entity._device, {"i": 0, "t": 2, "len": 0, "v": 250})]

    async def test_no_temperature_is_a_no_op(self) -> None:
        entity, api = make_thermostat()
        await entity.async_set_temperature()
        assert api.commands == []


class TestSetHvacMode:
    async def test_turn_off_sends_off_value(self) -> None:
        entity, api = make_thermostat(work_mode=0)
        await entity.async_turn_off()
        assert api.commands == [(entity._device, {"i": 0, "t": 1, "len": 0, "v": 4})]

    async def test_turn_on_sends_auto_value(self) -> None:
        entity, api = make_thermostat(work_mode=1)
        await entity.async_turn_on()
        assert api.commands == [(entity._device, {"i": 0, "t": 1, "len": 0, "v": 0})]

    async def test_unsupported_mode_is_ignored(self) -> None:
        entity, api = make_thermostat()
        await entity.async_set_hvac_mode(HVACMode.COOL)
        assert api.commands == []
