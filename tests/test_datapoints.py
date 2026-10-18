"""Tests for the data point to entity mapping."""

from conftest import FakeApi

from custom_components.radiant_smart.datapoints import build_entity_configs
from custom_components.radiant_smart.models import SmartDevice

# A realistic set of data points for a boiler controller + thermostat pair
# as reported by the Topband cloud device list.
DEVICE_POINTS = {
    # Shared boiler work mode
    "SYS_WORK_MODE": {"pointIndex": 8270, "pointType": 1, "value": 3},
    # Radiators (CH) water heater
    "PARAM_ID_BOILER_CH_CUR_TEMP": {"pointIndex": 2, "pointType": 2, "value": 450},
    "PARAM_ID_BOILER_CH_SET_RANGE_DOWN": {
        "pointIndex": 3,
        "pointType": 2,
        "value": 300,
    },
    "PARAM_ID_BOILER_CH_SET_RANGE_UP": {"pointIndex": 4, "pointType": 2, "value": 800},
    "PARAM_ID_BOILER_CH_MAX_SETPOINT": {"pointIndex": 5, "pointType": 2, "value": 650},
    # Sanitary (DHW) water heater
    "PARAM_ID_BOILER_DHW_CUR_TEMP": {"pointIndex": 6, "pointType": 2, "value": 480},
    "PARAM_ID_BOILER_DHW_SET_RANGE_DOWN": {
        "pointIndex": 7,
        "pointType": 2,
        "value": 300,
    },
    "PARAM_ID_BOILER_DHW_SET_RANGE_UP": {"pointIndex": 8, "pointType": 2, "value": 750},
    "PARAM_ID_BOILER_DHW_TRG_TEMP": {"pointIndex": 9, "pointType": 2, "value": 550},
    # Thermostat
    "PARAM_ID_TH_CUR_ROOM_TEMP": {"pointIndex": 10, "pointType": 2, "value": 205},
    "PARAM_ID_TH_TRG_ROOM_TEMP": {"pointIndex": 11, "pointType": 2, "value": 210},
    "PARAM_ID_TH_OVERRIDE_ENABLE": {"pointIndex": 12, "pointType": 1, "value": 0},
    "PARAM_ID_TH_OVERRIDE_TEMP": {"pointIndex": 13, "pointType": 2, "value": 220},
    "PARAM_ID_TH_WORK_MODE": {"pointIndex": 14, "pointType": 1, "value": 0},
    "PARAM_ID_TH_CH_STATUS": {"pointIndex": 15, "pointType": 1, "value": 1},
    # Selects
    "PARAM_ID_TH_CH_CALCULATE": {"pointIndex": 16, "pointType": 1, "value": 0},
    # Switches; note the DHW program enable flag shares the ignored prefix
    "PARAM_ID_BOILER_DHW_PRO_EN": {"pointIndex": 17, "pointType": 1, "value": 1},
    "PARAM_ID_SYS_DST_ENABLE": {"pointIndex": 18, "pointType": 1, "value": 0},
    # Numbers
    "PARAM_ID_TH_POWEROFF_FROZE_TEMP": {"pointIndex": 19, "pointType": 2, "value": 70},
    "PARAM_ID_TH_CLIMATE_CURVE": {"pointIndex": 20, "pointType": 2, "value": 15},
    # Buttons
    "RESET_FAULT": {"pointIndex": 21, "pointType": 1, "value": 0},
    # Sensors with an exact mapping
    "PARAM_ID_BOILER_OUTDOOR_TEMP": {"pointIndex": 22, "pointType": 2, "value": 82},
    "PARAM_ID_BOILER_PWM_OUT": {"pointIndex": 23, "pointType": 1, "value": 40},
    "PARAM_ID_SYS_SOFT_VER": {"pointIndex": 24, "pointType": 8, "value": "1.2.3"},
    "PARAM_ID_BOILER_OT_SLAVE_STATUS": {"pointIndex": 25, "pointType": 1, "value": 11},
    # Sensors matched by suffix
    "PARAM_ID_WATER_FLOW_RATE": {"pointIndex": 26, "pointType": 2, "value": 105},
    "PARAM_ID_WATER_PRESSURE": {"pointIndex": 27, "pointType": 2, "value": 16},
    # Binary sensors with an exact mapping
    "PARAM_ID_BOILER_IS_OT_CONNECTED": {"pointIndex": 28, "pointType": 1, "value": 1},
    "PARAM_ID_BOILER_OTC_ENABLE": {"pointIndex": 29, "pointType": 1, "value": 0},
    # Binary sensors matched by suffix
    "PARAM_ID_PUMP_RELAY_STATE": {"pointIndex": 30, "pointType": 1, "value": 2},
    "PARAM_ID_PUMP_ENABLE": {"pointIndex": 31, "pointType": 1, "value": 1},
    # Never exposed
    "PARAM_ID_SYS_DEVICE_MODULE": {"pointIndex": 32, "pointType": 1, "value": 1},
    "PARAM_ID_BOILER_DHW_PRO_MON": {"pointIndex": 33, "pointType": 8, "value": "blob"},
    "PARAM_ID_TH_ROOM_PROG_DAY_MON": {
        "pointIndex": 34,
        "pointType": 8,
        "value": "blob",
    },
    # Unmapped leftovers
    "PARAM_ID_MYSTERY_VALUE": {"pointIndex": 35, "pointType": 1, "value": 7},
}


def make_device(points=DEVICE_POINTS) -> SmartDevice:
    """Create a device with the test point data map."""
    source = {
        "deviceType": 1,
        "deviceName": "Boiler",
        "id": "1",
        "productId": "product",
        "model": "TH311",
        "gateway": {"uid": "gw", "productId": "gw-product"},
        "extAddr": "aa:bb:cc",
        "online": True,
        "pointDataMap": dict(points),
    }
    device = SmartDevice.from_api_response(FakeApi(), source)
    assert device is not None
    build_entity_configs(device)
    return device


class TestWaterHeaters:
    def test_both_circuits_built(self) -> None:
        device = make_device()
        keys = [spec.key for spec, _ in device.water_heaters]
        assert keys == ["heating", "sanitary"]

    def test_shared_work_mode_point(self) -> None:
        """Both water heaters read the same SYS_WORK_MODE point."""
        device = make_device()
        modes = {id(points["work_mode"]) for _, points in device.water_heaters}
        assert len(modes) == 1

    def test_not_built_when_required_point_missing(self) -> None:
        points = {k: v for k, v in DEVICE_POINTS.items() if k != "SYS_WORK_MODE"}
        device = make_device(points)
        # Without the shared work mode point no water heater can exist.
        assert device.water_heaters == []


class TestClimate:
    def test_climate_built(self) -> None:
        device = make_device()
        assert len(device.climates) == 1
        spec, points = device.climates[0]
        assert spec.key == "thermostat"
        assert set(points) == {
            "current_temp",
            "target_temp",
            "override_enable",
            "override_temp",
            "hvac_mode",
            "current_action",
        }


class TestSwitches:
    def test_dhw_program_switch_survives_prefix_filter(self) -> None:
        """The DHW program enable flag must not be swallowed by the
        ignored PARAM_ID_BOILER_DHW_PRO_ prefix (weekly programs)."""
        device = make_device()
        keys = {spec.key for spec, _ in device.switches}
        assert keys == {"dhw_program", "auto_time_sync"}


class TestSelects:
    def test_selects_built(self) -> None:
        device = make_device()
        keys = {spec.key for spec, _ in device.selects}
        assert keys == {"work_mode", "control_type"}


class TestBinarySensors:
    def test_ot_status_feeds_multiple_sensors(self) -> None:
        """The OpenTherm status feeds the binary sensors and stays available
        for the enum sensor."""
        device = make_device()
        ot_specs = [
            spec.key
            for spec, dp in device.binary_sensors
            if dp.name == "PARAM_ID_BOILER_OT_SLAVE_STATUS"
        ]
        assert set(ot_specs) == {"flame", "radiators_water_flow", "domestic_water_flow"}

    def test_exact_and_suffix_matches(self) -> None:
        device = make_device()
        keys = {spec.key for spec, _ in device.binary_sensors}
        assert "thermostat_connection" in keys  # exact
        assert "relay_state" in keys  # _RELAY_STATE suffix
        assert "enabled" in keys  # _ENABLE suffix


class TestSensors:
    def test_exact_and_suffix_matches(self) -> None:
        device = make_device()
        keys = {spec.key for spec, _ in device.sensors}
        assert "outdoor_temperature" in keys  # exact
        assert "boiler_status" in keys  # exact (OT status enum)
        assert "flow_rate" in keys  # _RATE suffix
        assert "pressure" in keys  # _PRESSURE suffix
        assert "power" in keys
        assert "software_version" in keys

    def test_unknown_points_are_not_consumed(self) -> None:
        device = make_device()
        assert set(device.unknown_points) == {"PARAM_ID_MYSTERY_VALUE"}


class TestIgnoredPoints:
    def test_ignored_points_not_exposed(self) -> None:
        device = make_device()
        all_names = set(device.data_points)
        for ignored in (
            "PARAM_ID_SYS_DEVICE_MODULE",
            "PARAM_ID_BOILER_DHW_PRO_MON",
            "PARAM_ID_TH_ROOM_PROG_DAY_MON",
        ):
            assert ignored in all_names  # still on the device
        exposed_names = {
            dp.name
            for configs in (
                device.sensors,
                device.binary_sensors,
                device.numbers,
                device.buttons,
                device.switches,
                device.selects,
            )
            for _, dp in configs
        }
        assert not exposed_names & {
            "PARAM_ID_SYS_DEVICE_MODULE",
            "PARAM_ID_BOILER_DHW_PRO_MON",
            "PARAM_ID_TH_ROOM_PROG_DAY_MON",
        }
        assert not set(device.unknown_points) & {
            "PARAM_ID_SYS_DEVICE_MODULE",
            "PARAM_ID_BOILER_DHW_PRO_MON",
            "PARAM_ID_TH_ROOM_PROG_DAY_MON",
        }


class TestNumbersAndButtons:
    def test_numbers_built(self) -> None:
        device = make_device()
        keys = {spec.key for spec, _ in device.numbers}
        assert keys == {"frost_temperature", "climate_curve"}

    def test_buttons_built(self) -> None:
        device = make_device()
        keys = {spec.key for spec, _ in device.buttons}
        assert keys == {"reset_fault"}
