"""End-to-end mapping tests against a real Topband cloud device list capture.

The fixture is an unmodified capture of the device list API response for a
home with a TH311 boiler controller (acting as gateway) and a TH623 room
thermostat. It pins the entity mapping, value decoding and MQTT dispatch
against real protocol data.
"""

import json
from pathlib import Path

from conftest import FakeApi, FakeHass

from custom_components.radiant_smart.api import TopbandCloudApi
from custom_components.radiant_smart.datapoints import build_entity_configs
from custom_components.radiant_smart.models import BoilerMode, SmartDevice

FIXTURE = Path(__file__).parent / "fixtures" / "device_list.json"


def load_rows() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def build_devices() -> dict[str, SmartDevice]:
    devices = {}
    for row in load_rows():
        device = SmartDevice.from_api_response(FakeApi(), row)
        assert device is not None
        build_entity_configs(device)
        devices[device.mac_address.upper()] = device
    return devices


class TestRealDeviceList:
    """The entity breakdown for the captured TH311 + TH623 home."""

    @classmethod
    def setup_class(cls) -> None:
        cls.devices = build_devices()
        cls.thermostat = cls.devices["F1A399990000"]
        cls.boiler = cls.devices["A4CF12F5FE10"]

    def test_both_devices_created(self) -> None:
        assert self.thermostat.name == "TH623"
        assert self.boiler.name == "TH311"
        # The boiler controller is the gateway for both devices
        assert self.boiler.gateway.uid == self.thermostat.gateway.uid
        assert self.thermostat.gateway.product_id == self.boiler.product_id

    def test_thermostat_entities(self) -> None:
        device = self.thermostat
        assert [spec.key for spec, _ in device.water_heaters] == []
        assert [spec.key for spec, _ in device.climates] == ["thermostat"]
        assert [spec.key for spec, _ in device.selects] == ["control_type"]
        assert [spec.key for spec, _ in device.switches] == []
        assert [spec.key for spec, _ in device.numbers] == [
            "frost_temperature",
            "climate_curve",
        ]
        assert [spec.key for spec, _ in device.buttons] == []
        assert sorted(spec.key for spec, _ in device.binary_sensors) == [
            "domestic_water_flow",
            "flame",
            "radiators_water_flow",
            "relay_state",
        ]
        assert sorted(spec.key for spec, _ in device.sensors) == [
            "anti_frost_temperature",
            "boiler_ch_temperature",
            "boiler_status",
            "cft_temperature",
            "eco_temperature",
            "fault_code",
            "software_version",
            "wifi_signal",
        ]
        # The holiday program blob is ignored, nothing is unmapped
        assert list(device.unknown_points) == []

    def test_boiler_entities(self) -> None:
        device = self.boiler
        assert [spec.key for spec, _ in device.water_heaters] == [
            "heating",
            "sanitary",
        ]
        assert [spec.key for spec, _ in device.climates] == []
        assert [spec.key for spec, _ in device.selects] == ["work_mode"]
        assert [spec.key for spec, _ in device.switches] == [
            "dhw_program",
            "auto_time_sync",
        ]
        assert [spec.key for spec, _ in device.numbers] == []
        assert [spec.key for spec, _ in device.buttons] == ["reset_fault"]
        assert sorted(spec.key for spec, _ in device.binary_sensors) == [
            "domestic_water_flow",
            "external_temperature_sensor",
            "flame",
            "outdoor_temperature_compensation",
            "radiators_water_flow",
            "relay_state",
            "thermostat_connection",
        ]
        assert sorted(spec.key for spec, _ in device.sensors) == [
            "boiler_status",
            "fault_code",
            "flow_rate",
            "heating_target_temperature",
            "outdoor_temperature",
            "power",
            "pressure",
            "software_version",
            "wifi_signal",
        ]
        # CH_SET / CTRL_TYPE are ignored, nothing is unmapped
        assert list(device.unknown_points) == []

    def test_string_values_are_decoded(self) -> None:
        """The cloud reports all values as strings; decoding must handle it."""
        points = self.boiler.data_points
        assert points["PARAM_ID_BOILER_OUTDOOR_TEMP"].get_value() == 7.0
        assert points["PARAM_ID_BOILER_CH_CUR_TEMP"].get_value() == 52.4
        assert points["PARAM_ID_BOILER_DHW_CUR_TEMP"].get_value() == 50.4
        # CONSTANT (t=7) range points are plain integers, not x10
        assert points["PARAM_ID_BOILER_CH_SET_RANGE_DOWN"].get_value() == 30
        assert points["PARAM_ID_BOILER_DHW_SET_RANGE_UP"].get_value() == 60
        assert points["SYS_WORK_MODE"].get_value() == BoilerMode.HEATING_SANITARY
        # RAW (t=8) values pass through untouched
        assert points["PARAM_ID_SYS_SOFT_VER"].value == "03020400"

    def test_ot_status_bitfield(self) -> None:
        """OT slave status 12 = domestic water (4) + flame (8)."""
        assert (
            self.boiler.data_points["PARAM_ID_BOILER_OT_SLAVE_STATUS"].get_value() == 12
        )

    def test_ignored_weekly_programs(self) -> None:
        """The weekly program blobs must not leak into unknown points."""
        for device in self.devices.values():
            assert not [
                name
                for name in device.unknown_points
                if "PROG" in name or name.endswith("_PRO_")
            ]
        # ...but the DHW program enable flag switch exists
        assert "dhw_program" in {spec.key for spec, _ in self.boiler.switches}


class TestRealMqttFlow:
    """Dispatching captured cmd 98 messages updates the devices."""

    @classmethod
    def setup_class(cls) -> None:
        cls.devices = build_devices()
        cls.boiler = cls.devices["A4CF12F5FE10"]

    def test_captured_multi_point_update(self) -> None:
        """Message 11 from the captures: two points updated in one echo."""
        echo = {
            "PARAM_ID_BOILER_CH_CUR_TEMP": {"i": 8192, "t": 2, "v": 440},
            "PARAM_ID_BOILER_CH_MAX_SETPOINT": {"i": 8203, "t": 2, "v": 530},
        }
        self.boiler.handle_mqtt_command(echo)

        points = self.boiler.data_points
        assert points["PARAM_ID_BOILER_CH_CUR_TEMP"].get_value() == 44.0
        assert points["PARAM_ID_BOILER_CH_MAX_SETPOINT"].get_value() == 53.0

    def test_captured_work_mode_update(self) -> None:
        """Message 2 from the captures: work mode switched to sanitary."""
        self.boiler.handle_mqtt_command({"SYS_WORK_MODE": {"i": 8270, "t": 1, "v": 2}})
        assert (
            self.boiler.data_points["SYS_WORK_MODE"].get_value() == BoilerMode.SANITARY
        )

    async def test_topic_subscriptions_cover_both_products(self) -> None:
        """The app subscribes upload topics under both the gateway and the
        device product id, plus the per-device business topic."""
        api = TopbandCloudApi(
            FakeHass(),
            None,
            {"email": "e", "password": "p", "company_id": "c"},
        )
        api.devices = self.devices
        await api.async_subscribe_topics()

        subscriptions = api.mqtt.subscriptions
        assert subscriptions == {
            # TH311 (gateway/boiler)
            "9b2d8fcf2e9d4c9390f5a1fb9749b8a4/12b602c3aa7c329c5021239f76b69585/business",
            "9b2d8fcf2e9d4c9390f5a1fb9749b8a4/12b602c3aa7c329c5021239f76b69585/upload/point/data",
            # TH623 (thermostat)
            "044e784511e14d34953c5f2221129a4e/12b602c3aa7c329c5021239f76b69585/business",
            "044e784511e14d34953c5f2221129a4e/12b602c3aa7c329c5021239f76b69585/upload/point/data",
        }
