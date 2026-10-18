"""Entity integration tests: full setup against the captured device list.

The devices come from the real Topband cloud capture (see
tests/fixtures/device_list.json), so the expected states below are the
expected decoded values for that capture.

The tests cover:
- the entities created for both devices and their initial states,
- end-to-end push updates: an MQTT echo (cmd 98) updates HA states,
- end-to-end commands: an HA service call publishes the protocol payload.
"""

import asyncio
import json

import paho.mqtt.client as mqtt
from conftest import BOILER_MAC, THERMOSTAT_MAC
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from custom_components.radiant_smart.const import CMD_POINT_UPDATE, DOMAIN
from custom_components.radiant_smart.models import BoilerMode

GATEWAY_UID = "12b602c3aa7c329c5021239f76b69585"
BOILER_PRODUCT_ID = "9b2d8fcf2e9d4c9390f5a1fb9749b8a4"


def entity_id_for(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    """Look up an entity id by its unique id."""
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def state_of(hass: HomeAssistant, platform: str, unique_id: str):
    """Return the state object for an entity unique id."""
    entity_id = entity_id_for(hass, platform, unique_id)
    assert entity_id is not None, f"no {platform} entity with unique id {unique_id}"
    return hass.states.get(entity_id)


async def flush_hass(hass: HomeAssistant) -> None:
    """Let queued loop callbacks (entity state writes) run.

    Pushed updates schedule their state write as a raw loop callback;
    async_block_till_done alone may return before those run.
    """
    for _ in range(3):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


def publish_echo(setup_entry, message: dict) -> None:
    """Deliver a captured upload message to the MQTT client."""
    api = setup_entry.runtime_data
    client = api.mqtt._client
    assert client is not None
    msg = mqtt.MQTTMessage()
    msg.topic = f"{BOILER_PRODUCT_ID}/{GATEWAY_UID}/upload/point/data".encode()
    msg.payload = json.dumps(message).encode()
    api.mqtt._on_message(client, None, msg)


class TestThermostatDevice:
    """Entities created from the captured TH623 room thermostat."""

    async def test_climate_entity(self, hass, setup_entry) -> None:
        """Room temperature 18.2, override active in auto mode -> target 19.5."""
        state = state_of(hass, "climate", f"{THERMOSTAT_MAC}_thermostat")
        assert state is not None
        assert state.state == "auto"  # PARAM_ID_TH_WORK_MODE = 0
        assert state.attributes["current_temperature"] == 18.2
        assert state.attributes["temperature"] == 19.5  # override temp active
        assert state.attributes["hvac_action"] == "heating"  # TH_CH_STATUS = 1
        assert state.attributes["min_temp"] == 5.0
        assert state.attributes["max_temp"] == 35.0

    async def test_numbers(self, hass, setup_entry) -> None:
        frost = state_of(hass, "number", f"{THERMOSTAT_MAC}_4153")
        assert frost.state == "5.0"  # PARAM_ID_TH_POWEROFF_FROZE_TEMP = 50 (x10)

        curve = state_of(hass, "number", f"{THERMOSTAT_MAC}_4114")
        assert curve.state == "54"  # PARAM_ID_TH_CLIMATE_CURVE = 54 (int)

    async def test_select(self, hass, setup_entry) -> None:
        control = state_of(hass, "select", f"{THERMOSTAT_MAC}_4160")
        assert control.state == "Room & Outdoor Temperature"  # TH_CH_CALCULATE = 3

    async def test_sensors(self, hass, setup_entry) -> None:
        """Sensor unique ids are <mac>_<point index>."""
        eco = state_of(hass, "sensor", f"{THERMOSTAT_MAC}_4101")
        assert eco.state == "16.0"

        wifi = state_of(hass, "sensor", f"{THERMOSTAT_MAC}_24")
        assert wifi.state == "4"  # PARAM_ID_SYS_WIFI__SIGNAL

        fault = state_of(hass, "sensor", f"{THERMOSTAT_MAC}_8206")
        assert fault.state == "18"  # PARAM_ID_BOILER_FAULT_CODE

        # The room temperature (4096) is owned by the climate entity and
        # has no separate sensor
        assert entity_id_for(hass, "sensor", f"{THERMOSTAT_MAC}_4096") is None

    async def test_binary_sensors(self, hass, setup_entry) -> None:
        """OT slave status on the thermostat is 10 = radiator + flame."""
        flame = state_of(hass, "binary_sensor", f"{THERMOSTAT_MAC}_flame_8212")
        assert flame.state == STATE_ON

        relay = state_of(hass, "binary_sensor", f"{THERMOSTAT_MAC}_relay_state_4106")
        assert relay.state == STATE_ON  # PARAM_ID_TH_RELAY_STATE = 2 -> on


class TestBoilerDevice:
    """Entities created from the captured TH311 boiler controller."""

    async def test_water_heaters(self, hass, setup_entry) -> None:
        """Boiler runs in combined mode (SYS_WORK_MODE = 10)."""
        heating = state_of(hass, "water_heater", f"{BOILER_MAC}_heating")
        assert heating.state == "gas"
        assert heating.attributes["current_temperature"] == 52.4
        assert heating.attributes["temperature"] == 50.0
        assert heating.attributes["min_temp"] == 30.0
        assert heating.attributes["max_temp"] == 80.0

        sanitary = state_of(hass, "water_heater", f"{BOILER_MAC}_sanitary")
        assert sanitary.state == "gas"
        assert sanitary.attributes["current_temperature"] == 50.4
        assert sanitary.attributes["temperature"] == 54.0
        assert sanitary.attributes["min_temp"] == 35.0
        assert sanitary.attributes["max_temp"] == 60.0

    async def test_selects_switches_buttons(self, hass, setup_entry) -> None:
        work_mode = state_of(hass, "select", f"{BOILER_MAC}_8270")
        assert work_mode.state == "Heating & Sanitary"

        dhw_program = state_of(hass, "switch", f"{BOILER_MAC}_8273")
        assert dhw_program.state == STATE_OFF  # PARAM_ID_BOILER_DHW_PRO_EN = 0

        time_sync = state_of(hass, "switch", f"{BOILER_MAC}_5")
        assert time_sync.state == STATE_OFF

        reset_fault = state_of(hass, "button", f"{BOILER_MAC}_reset_fault")
        assert reset_fault is not None  # buttons have no meaningful state

    async def test_sensors(self, hass, setup_entry) -> None:
        outdoor = state_of(hass, "sensor", f"{BOILER_MAC}_8204")
        assert outdoor.state == "7.0"
        assert outdoor.attributes["device_class"] == "temperature"
        assert outdoor.attributes["state_class"] == "measurement"

        power = state_of(hass, "sensor", f"{BOILER_MAC}_8227")
        assert power.state == "43"  # PARAM_ID_BOILER_PWM_OUT

        status = state_of(hass, "sensor", f"{BOILER_MAC}_8212")
        assert status.state == "Flame + Domestic Water"  # OT status 12

        software = state_of(hass, "sensor", f"{BOILER_MAC}_0")
        assert software.state == "03020400"  # raw passthrough

        # The CH current temperature (8192) is owned by the water heater
        assert entity_id_for(hass, "sensor", f"{BOILER_MAC}_8192") is None

    async def test_binary_sensors(self, hass, setup_entry) -> None:
        """OT slave status 12 = domestic water (4) + flame (8)."""
        flame = state_of(hass, "binary_sensor", f"{BOILER_MAC}_flame_8212")
        assert flame.state == STATE_ON

        domestic = state_of(
            hass, "binary_sensor", f"{BOILER_MAC}_domestic_water_flow_8212"
        )
        assert domestic.state == STATE_ON

        radiators = state_of(
            hass, "binary_sensor", f"{BOILER_MAC}_radiators_water_flow_8212"
        )
        assert radiators.state == STATE_OFF

        thermostat_connection = state_of(
            hass, "binary_sensor", f"{BOILER_MAC}_thermostat_connection_8208"
        )
        assert thermostat_connection.state == STATE_ON

    async def test_unknown_points_are_disabled_diagnostics(
        self, hass, setup_entry
    ) -> None:
        """Unmapped points (e.g. PARAM_ID_BOILER_CH_SET) become disabled
        diagnostic sensors."""
        registry = er.async_get(hass)
        entry = registry.async_get_entity_id("sensor", DOMAIN, f"{BOILER_MAC}_8272")
        assert entry is not None
        registry_entry = registry.async_get(entry)
        assert registry_entry is not None
        assert registry_entry.disabled_by is RegistryEntryDisabler.INTEGRATION
        # Disabled entities have no state
        assert hass.states.get(entry) is None


class TestAvailability:
    async def test_devices_online(self, hass, setup_entry) -> None:
        """The captured devices are online; entities are available."""
        climate = state_of(hass, "climate", f"{THERMOSTAT_MAC}_thermostat")
        assert climate.state != STATE_UNAVAILABLE

    async def test_offline_device_entities_unavailable(
        self, hass, aioclient_mock, patch_paho, device_list_rows
    ) -> None:
        """Entities of an offline device are unavailable."""
        from conftest import ENTRY_DATA, LOGIN_RESPONSE

        from custom_components.radiant_smart.api import DEVICE_LIST_URL, TOKEN_URL

        aioclient_mock.post(TOKEN_URL, json=LOGIN_RESPONSE)
        for row in device_list_rows:
            row["online"] = 0
        aioclient_mock.post(
            DEVICE_LIST_URL, json={"status": 0, "data": {"rows": device_list_rows}}
        )

        from pytest_homeassistant_custom_component.common import MockConfigEntry

        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(ENTRY_DATA), version=1, unique_id="home-1"
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        climate = state_of(hass, "climate", f"{THERMOSTAT_MAC}_thermostat")
        assert climate.state == STATE_UNAVAILABLE


class TestMqttPushUpdates:
    """End-to-end: captured MQTT echoes update Home Assistant states."""

    async def test_work_mode_change_updates_entities(self, hass, setup_entry) -> None:
        """Captured message 1.json: work mode 10 -> 3 (heating)."""
        assert (
            state_of(hass, "select", f"{BOILER_MAC}_8270").state == "Heating & Sanitary"
        )

        publish_echo(
            setup_entry,
            {
                "method": "command",
                "data": [
                    {
                        "cmd": CMD_POINT_UPDATE,
                        "mac": BOILER_MAC,
                        "command": {
                            "SYS_WORK_MODE": {
                                "i": 8270,
                                "t": 1,
                                "v": BoilerMode.HEATING,
                            }
                        },
                    }
                ],
            },
        )
        await flush_hass(hass)

        # The select reflects the new work mode
        assert state_of(hass, "select", f"{BOILER_MAC}_8270").state == "Heating"
        # Both water heaters react to the mode change
        assert state_of(hass, "water_heater", f"{BOILER_MAC}_heating").state == "gas"
        # the sanitary circuit is no longer on
        assert state_of(hass, "water_heater", f"{BOILER_MAC}_sanitary").state == "off"

    async def test_temperature_echo_updates_water_heater(
        self, hass, setup_entry
    ) -> None:
        """Captured message 20.json: CH current temperature 47.6."""
        assert (
            state_of(hass, "water_heater", f"{BOILER_MAC}_heating").attributes[
                "current_temperature"
            ]
            == 52.4
        )

        publish_echo(
            setup_entry,
            {
                "method": "command",
                "data": [
                    {
                        "cmd": CMD_POINT_UPDATE,
                        "mac": BOILER_MAC,
                        "command": {
                            "PARAM_ID_BOILER_CH_CUR_TEMP": {
                                "i": 8192,
                                "t": 2,
                                "v": 476,
                            }
                        },
                    }
                ],
            },
        )
        await flush_hass(hass)

        # The water heater follows the pushed temperature
        assert (
            state_of(hass, "water_heater", f"{BOILER_MAC}_heating").attributes[
                "current_temperature"
            ]
            == 47.6
        )


class TestCommandRoundTrips:
    """End-to-end: HA service calls publish the protocol payloads."""

    @staticmethod
    def published_commands(setup_entry) -> list[dict]:
        """Return the command payloads published so far."""
        api = setup_entry.runtime_data
        client = api.mqtt._client
        assert client is not None
        return [json.loads(payload) for _topic, payload in client.recorder.published]

    async def test_set_temperature_in_auto_mode_sets_override(
        self, hass, setup_entry
    ) -> None:
        """Setting a temperature in auto mode updates the override."""
        entity_id = entity_id_for(hass, "climate", f"{THERMOSTAT_MAC}_thermostat")
        state = hass.states.get(entity_id)
        assert state.state == "auto"

        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": 21.5},
            blocking=True,
        )

        await flush_hass(hass)
        commands = self.published_commands(setup_entry)
        # Override temperature first, then the override enable flag
        assert commands[0]["data"][0]["command"] == [
            {"i": 4119, "t": 2, "len": 0, "v": 215}
        ]
        assert commands[1]["data"][0]["command"] == [
            {"i": 4118, "t": 1, "len": 0, "v": 1}
        ]
        # The optimistic update reflects the new target
        assert hass.states.get(entity_id).attributes["temperature"] == 21.5

    async def test_turn_on_switch(self, hass, setup_entry) -> None:
        entity_id = entity_id_for(hass, "switch", f"{BOILER_MAC}_8273")

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )

        await flush_hass(hass)
        commands = self.published_commands(setup_entry)
        assert commands[0]["data"][0]["command"] == [
            {"i": 8273, "t": 1, "len": 0, "v": 1}
        ]
        assert hass.states.get(entity_id).state == STATE_ON

    async def test_press_button(self, hass, setup_entry) -> None:
        entity_id = entity_id_for(hass, "button", f"{BOILER_MAC}_reset_fault")

        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )

        await flush_hass(hass)
        commands = self.published_commands(setup_entry)
        assert commands[0]["data"][0]["command"] == [
            {"i": 8233, "t": 1, "len": 0, "v": 1}
        ]

    async def test_set_water_heater_temperature(self, hass, setup_entry) -> None:
        entity_id = entity_id_for(hass, "water_heater", f"{BOILER_MAC}_heating")

        await hass.services.async_call(
            "water_heater",
            "set_temperature",
            {"entity_id": entity_id, "temperature": 55.0},
            blocking=True,
        )

        await flush_hass(hass)
        commands = self.published_commands(setup_entry)
        assert commands[0]["data"][0]["command"] == [
            {"i": 8203, "t": 2, "len": 0, "v": 550}
        ]
        assert hass.states.get(entity_id).attributes["temperature"] == 55.0
