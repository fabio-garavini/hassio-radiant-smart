"""Topband cloud api handling."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import ssl
from time import time
from typing import Any, cast
import uuid

import aiohttp
import paho.mqtt.client as mqtt
from yarl import URL

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate import HVACMode
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import (
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN  # noqa: F401

TIMEOUT = 10

_LOGGER: logging.Logger = logging.getLogger(__name__)

TOPBAND_MQTT_BROKER_URL = "eu-tsmart-mqtt.topband-cloud.com"
TOPBAND_MQTT_BROKER_PORT = 8883

USER_BASE_URL = "https://eu-tsmart-user-api.topband-cloud.com"
DEVICE_BASE_URL = "https://eu-tsmart-device-api.topband-cloud.com"

HEADERS = {"Content-type": "application/json; charset=UTF-8"}


class TopbandCloudApi:
    """Topband cloud api client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        company_id: str,
        home_id: str | None = None,
        token_data: dict[str, Any] | None = None,
        validate_ssl: bool = True,
    ) -> None:
        """Initialize."""
        self._session = session
        self._email = email
        self._password = password
        self._company_id = company_id
        self._family_id = home_id
        self._token_data = token_data or {}
        self.validate_ssl = validate_ssl
        self.devices: dict[str, SmartDevice] = {}
        self._mqtt: mqtt.Client | None = None
        self._mqtt_serial: int = 0

    def send_mqtt_command(self, device: SmartDevice, command: dict[str, Any]) -> None:
        """Send an MQTT message to the Topband cloud."""
        if self._mqtt is None:
            _LOGGER.error("MQTT client is not initialized")
            return

        self._mqtt_serial += 1

        topic = f"{device.gateway.product_id}/{device.gateway.uid}/download/point/data"

        payload_str = json.dumps(
            {
                "common": {
                    "productId": device.gateway.product_id,
                    "serial": self._mqtt_serial,
                    "timestamp": int(time() * 1000),
                    "uid": device.gateway.uid,
                },
                "data": [{"cmd": 99, "command": [command], "mac": device.mac_address}],
                "method": "command",
            }
        )
        self.publish_mqtt_message(topic, payload_str)

    def publish_mqtt_message(self, topic: str, payload) -> None:
        """Publish MQTT message."""
        self._mqtt.publish(topic, payload)
        _LOGGER.info("Published to topic %s: %s", topic, payload)

    def mqtt_subscribe(self, topic: str) -> None:
        """Subscribe to the Topband cloud MQTT topics."""
        if self._mqtt is None:
            _LOGGER.error("MQTT client is not initialized")
            return

        self._mqtt.subscribe(topic)
        _LOGGER.info("Subscribed to topic: %s", topic)

    def on_mqtt_connect(
        self,
        client: mqtt.Client,
        userdata,
        flags: mqtt.ConnectFlags,
        reason_code,
        properties: mqtt.Properties,
    ):
        """Handle MQTT connection event."""
        if reason_code == 0:
            _LOGGER.info("Connected successfully!")
        else:
            _LOGGER.info("Connection failed with reason code %s", reason_code)

    def on_mqtt_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        """Handle incoming MQTT messages."""
        data: dict[str, Any] = json.loads(msg.payload.decode())
        _LOGGER.debug("Received: %s -> %s", msg.topic, data)

        if data.get("method") == "command":
            for d in cast(list[dict[str, Any]], data.get("data", [])):
                match d.get("cmd"):
                    case 98:
                        self.devices.get(d.get("mac", "")).handle_mqtt_command(d.get("command", {}))
                    case _:
                        _LOGGER.warning(
                            "Unknown MQTT command [%d]: %s",
                            d.get("cmd"),
                            d.get("command"),
                        )
        else:
            _LOGGER.warning("Unknown MQTT message: %s", d)

    def mqtt_connect(self) -> None:
        """Connect to the Topband cloud MQTT."""

        # Create client with Callback API version 2
        self._mqtt = mqtt.Client(
            client_id=f"home-assistant-{uuid.uuid4()}",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        # Set username and password
        self._mqtt.username_pw_set(
            f"app/{self._token_data['token']}", self._token_data["token"]
        )

        # Enable TLS/SSL
        self._mqtt.tls_set_context(ssl.create_default_context())
        self._mqtt.tls_insecure_set(
            False
        )  # Set to True only if using self-signed certificates

        # Attach callbacks
        self._mqtt.on_connect = self.on_mqtt_connect
        self._mqtt.on_message = self.on_mqtt_message

        self._mqtt.connect(
            TOPBAND_MQTT_BROKER_URL, TOPBAND_MQTT_BROKER_PORT, keepalive=60
        )

        self._mqtt.loop_start()

    def mqtt_disconnect(self) -> None:
        """Disconnect to the Topband cloud MQTT."""

        self._mqtt.loop_stop()
        self._mqtt.disconnect()

    async def async_get_devices(self) -> dict[str, SmartDevice]:
        """Get devices in a family from the Topband cloud API."""

        data: list[dict[str, Any]] = (
            await self.api_wrapper(
                method="post",
                url=str(
                    URL(DEVICE_BASE_URL) / "tsmart-device-api/family/v2/device/list"
                ),
                data={"familyId": self._family_id},
                is_login_required=True,
            )
        ).get("rows", [])

        self.devices = {}

        for d in data:
            device = SmartDevice(
                api=self,
                device_type=d.get("deviceType"),
                name=d.get("deviceName"),
                id=d.get("id"),
                product_id=d.get("productId"),
                model=d.get("model"),
                gateway=Gateway(
                    uid=d.get("gateway", {}).get("uid"),
                    product_id=d.get("gateway", {}).get("productId"),
                ),
                mac_address=d.get("extAddr"),
                online=d.get("online"),
                data_points=d.get("pointDataMap", {}),
            )

            self.devices[device.mac_address] = device

        return self.devices

    async def async_get_family_list(self) -> list[dict[str, Any]]:
        """Get family list from the Topband cloud API."""

        return await self.api_wrapper(
            method="post",
            url=str(URL(USER_BASE_URL) / "tsmart-user-api/family/list"),
            data={},
            is_login_required=True,
        )

    async def authenticate(self) -> dict[str, Any]:
        """Authenticate with the Topband cloud API."""

        # if self._token_data.get("token", None) is not None:
        #    try:
        #        await self._refresh_token()
        #    except TopbandApiClientError as err:
        #        _LOGGER.warning("Token refresh failed, attempting to re-authenticate: %s", err)
        #        try:
        #            await self._get_token()
        #        except TopbandApiClientError as e:
        #            raise ConfigEntryAuthFailed(e) from e
        # else:
        try:
            await self._get_token()
        except TopbandApiClientError as err:
            raise ConfigEntryAuthFailed(err) from err

        return self._token_data

    async def async_get_selfinfo(self) -> dict[str, Any]:
        """Get user info from the Topband cloud API."""

        return await self.api_wrapper(
            method="post",
            url=str(URL(USER_BASE_URL) / "tsmart-user-api/account/v2/getSelfInfo"),
            is_login_required=True,
        )

    async def _get_token(self) -> dict[str, Any]:
        """Obtain a new JWT token using the provided username and password.

        Sends a POST request to the login endpoint and extracts the token
        and expiration date from the response headers.
        """

        response = cast(
            dict[str, Any],
            await self.api_wrapper(
                method="post",
                url=str(URL(USER_BASE_URL) / "tsmart-user-api/appLogin/v2/login"),
                data={
                    "userName": self._email,
                    "password": hashlib.md5(self._password.encode()).hexdigest(),
                    "companyId": self._company_id,
                },
                is_login_required=False,
            ),
        )

        self._token_data["token"] = response.get("token")
        self._token_data["refresh_token"] = response.get("refresh_token")

        return self._token_data

    async def _refresh_token(self) -> dict[str, Any]:
        """Obtain a new JWT token using the provided username and password.

        Sends a POST request to the login endpoint and extracts the token
        and expiration date from the response headers.
        """

        response = await self.api_wrapper(
            method="get",
            url=str(
                URL(USER_BASE_URL) / "tsmart-user-api/appLogin/v2/refreshAccessToken"
            ),
            headers={"authorization": self._token_data["refresh_token"]},
            is_login_required=False,
        )

        self._token_data["token"] = response.get("token")
        self._token_data["refresh_token"] = response.get("refresh_token")

        return self._token_data

    async def get_auth_headers(self) -> dict[str, str]:
        """Get headers for API requests, including the JWT token if available.

        Ensures that the token is refreshed if needed.
        """

        headers = {}

        if self._email and self._password:
            # await self._refresh_token_if_needed()

            if "token" in self._token_data:
                headers["authorization"] = self._token_data["token"]

        return headers

    async def api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
        is_login_required: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Get information from the Topband API."""

        if data is None:
            data = {}
        if headers is None:
            headers = {}

        if is_login_required:
            headers.update(await self.get_auth_headers())

        try:
            timeout_value = timeout if timeout is not None else TIMEOUT
            async with asyncio.timeout(timeout_value):
                func = getattr(self._session, method)
                if func:
                    response = cast(
                        aiohttp.ClientResponse,
                        await func(
                            url,
                            headers=headers,
                            raise_for_status=True,
                            json=data,
                            ssl=self.validate_ssl,
                        ),
                    )

                    response.raise_for_status()

                    data = await response.json()

                    _LOGGER.debug(data)

                    if data.get("status") != 0:
                        _LOGGER.error("API error for URL %s: %s", url, data)
                        raise TopbandApiClientError(
                            f"API error: {data.get('message', 'Unknown error')}"
                        )

                    return data.get("data")

        except TimeoutError as exc:
            _LOGGER.error("Timeout error fetching information from %s: %s", url, exc)
            raise TopbandApiClientError from exc

        except aiohttp.ClientResponseError as exc:
            if exc.status == 401:
                _LOGGER.error(
                    "Unauthorized (401) error for URL %s: %s", url, exc.message
                )
                raise TopbandApiClientError(
                    "Unauthorized access - check credentials."
                ) from exc

            if exc.status == 403:
                _LOGGER.error("Forbidden (403) error for URL %s: %s", url, exc.message)
                raise TopbandApiClientError(
                    "Forbidden - insufficient permissions."
                ) from exc

            _LOGGER.error(
                "Client response error (%d) for URL %s: %s",
                exc.status,
                url,
                exc.message,
            )
            raise TopbandApiClientError from exc

        except (KeyError, TypeError) as exc:
            _LOGGER.error("Error parsing information from %s: %s", url, exc)
            raise TopbandApiClientError from exc

        except (aiohttp.ClientError, socket.gaierror) as exc:
            _LOGGER.error("Error fetching information from %s: %s", url, exc)
            raise TopbandApiClientError from exc

class TopbandApiClientError(Exception):
    """General TopbandApiClient error."""

class SmartDevice:
    """Radiant Smart Device."""

    def __init__(
        self,
        api: TopbandCloudApi,
        device_type: int,
        name: str,
        id: str,
        product_id: str,
        model: str,
        gateway: Gateway,
        mac_address: str,
        online: bool,
        data_points: dict[str, dict[str, Any]],
    ) -> None:
        """Initialize the device."""
        self._api = api
        self.device_type = device_type
        self.name = name
        self.id = id
        self.product_id = product_id
        self.model = model
        self.gateway = gateway
        self.mac_address = mac_address
        self.online = online

        self.data_points: dict[str, SmartDeviceDataPoint] = {
            k: SmartDeviceDataPoint(
                device=self,
                index=p.get("pointIndex"),
                name=p.get("pointName", "").replace("PARAM_ID_", ""),
                point_type=p.get("pointType"),
                value=p.get("value"),
            )
            for k, p in data_points.items()
        }

        self.parse_data_points()

        self._api.mqtt_subscribe(f"{self.product_id}/{self.gateway.uid}/business")
        self._api.mqtt_subscribe(f"{self.gateway.product_id}/{self.gateway.uid}/upload/point/data")

    def handle_mqtt_command(self, data: dict[str, dict[str, Any]]) -> None:
        """Handle incoming mqtt command."""
        for k, cmd in data.items():
            if self.data_points.get(k) is not None:
                self.data_points.get(k).update_value(cmd.get("v"))

    def send_data_point_update(self, dp: SmartDeviceDataPoint) -> None:
        """Send MQTT message update data point."""
        cmd = {"i": dp.index, "t": dp.point_type, "len": 0, "v": dp.value}
        self._api.send_mqtt_command(self, cmd)

    def parse_data_points(self) -> None:
        """Parse data points into HA entities."""
        self.unknown_points = self.data_points.copy()

        self.water_heaters = self.get_water_heaters(self.unknown_points)
        self.climate_data = self.get_climate_data(self.unknown_points)
        self.select_data = self.get_select_data(self.unknown_points)
        self.switches_data = self.get_switches_data(self.unknown_points)
        self.numbers_data = self.get_numbers_data(self.unknown_points)
        self.sensors_data = self.get_sensors_data(self.unknown_points)
        self.binary_sensors_data = self.get_binary_sensors_data(self.unknown_points)

        self.remove_unsupported_data(self.unknown_points)

    def remove_unsupported_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> None:
        """Remove unsupported data points."""

        keys = [
            "PARAM_ID_SYS_DEVICE_MODULE",
            "PARAM_ID_TH_DELETE_TH_ADDR",
        ]

        for k in keys:
            data_points.pop(k)

        for k in list(data_points.keys()):
            if k.startswith(("PARAM_ID_BOILER_DHW_PRO_", "PARAM_ID_TH_ROOM_PROG_DAY_")):
                data_points.pop(k)

    def get_water_heaters(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[WaterHeaterData]:
        """Get water heater data points."""

        water_heaters = []

        ch_keys = (
            "PARAM_ID_BOILER_CH_CUR_TEMP",
            "PARAM_ID_BOILER_CH_SET_RANGE_DOWN",
            "PARAM_ID_BOILER_CH_SET_RANGE_UP",
            "PARAM_ID_BOILER_CH_MAX_SETPOINT",
            "PARAM_ID_BOILER_CH_TRG_TEMP",
        )

        if all(k in data_points for k in ch_keys):
            water_heaters.append(
                WaterHeaterData(
                    name="Heating Water Heater",
                    icon="mdi:radiator",
                    min_temp=data_points.pop("PARAM_ID_BOILER_CH_SET_RANGE_DOWN"),
                    max_temp=data_points.pop("PARAM_ID_BOILER_CH_SET_RANGE_UP"),
                    current_temp=data_points.pop("PARAM_ID_BOILER_CH_CUR_TEMP"),
                    target_temp=data_points.pop("PARAM_ID_BOILER_CH_MAX_SETPOINT"),
                    target_temp_step=1.0,
                    temp_unit=UnitOfTemperature.CELSIUS,
                )
            )

        dhw_keys = (
            "PARAM_ID_BOILER_DHW_CUR_TEMP",
            "PARAM_ID_BOILER_DHW_SET_RANGE_DOWN",
            "PARAM_ID_BOILER_DHW_SET_RANGE_UP",
            "PARAM_ID_BOILER_DHW_TRG_TEMP",
        )

        if all(k in data_points for k in dhw_keys):
            water_heaters.append(
                WaterHeaterData(
                    name="Sanitary Water Heater",
                    icon="mdi:faucet",
                    min_temp=data_points.pop("PARAM_ID_BOILER_DHW_SET_RANGE_DOWN"),
                    max_temp=data_points.pop("PARAM_ID_BOILER_DHW_SET_RANGE_UP"),
                    current_temp=data_points.pop("PARAM_ID_BOILER_DHW_CUR_TEMP"),
                    target_temp=data_points.pop("PARAM_ID_BOILER_DHW_TRG_TEMP"),
                    target_temp_step=1.0,
                    temp_unit=UnitOfTemperature.CELSIUS,
                )
            )

        return water_heaters

    def get_climate_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[ClimateData]:
        """Get water heater data points."""

        climate = []

        thermostat_keys = (
            "PARAM_ID_TH_CUR_ROOM_TEMP",
            "PARAM_ID_TH_TRG_ROOM_TEMP",
            "PARAM_ID_TH_WORK_MODE",
        )

        if all(k in data_points for k in thermostat_keys):
            climate.append(
                ClimateData(
                    name="Thermostat",
                    icon="mdi:thermostat",
                    current_temp=data_points.pop("PARAM_ID_TH_CUR_ROOM_TEMP"),
                    target_temp=data_points.pop("PARAM_ID_TH_TRG_ROOM_TEMP"),
                    hvac_modes={0: HVACMode.AUTO, 1: HVACMode.HEAT, 4: HVACMode.OFF},
                    hvac_mode=data_points.pop("PARAM_ID_TH_WORK_MODE"),
                    target_temp_step=0.5,
                    temp_unit=UnitOfTemperature.CELSIUS,
                )
            )

        return climate

    def get_switches_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[SwitchData]:
        """Get known sensors data."""

        switches_data = []

        if data_points.get("PARAM_ID_BOILER_DHW_PRO_EN") is not None:
            switches_data.append(
                SwitchData(
                    data_point=data_points.pop("PARAM_ID_BOILER_DHW_PRO_EN"),
                    name="Domestic Water Heating Program",
                    icon="mdi:home-clock",
                    device_class=SwitchDeviceClass.SWITCH,
                )
            )

        if data_points.get("PARAM_ID_SYS_DST_ENABLE") is not None:
            switches_data.append(
                SwitchData(
                    data_point=data_points.pop("PARAM_ID_SYS_DST_ENABLE"),
                    name="Auto Time Sync",
                    icon="mdi:clock",
                    device_class=SwitchDeviceClass.SWITCH,
                )
            )

        return switches_data

    def get_select_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[SelectData]:
        """Get known select data."""

        select = []

        if data_points.get("SYS_WORK_MODE") is not None:
            select.append(
                SelectData(
                    data_point=data_points.pop("SYS_WORK_MODE"),
                    name="Work Mode",
                    icon="mdi:auto-mode",
                    options={
                        0: "Standby",
                        2: "Sanitary",
                        3: "Heating",
                        10: "Heating & Sanitary",
                    },
                )
            )

        return select

    def get_sensors_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[SensorData]:
        """Get known sensors data."""

        sensors_data = []

        if data_points.get("PARAM_ID_BOILER_CH_TRG_TEMP") is not None:
            sensors_data.append(
                SensorData(
                    data_point=data_points.pop("PARAM_ID_BOILER_CH_TRG_TEMP"),
                    name="Heating Target Temperature",
                    icon="mdi:thermometer",
                    device_class=SensorDeviceClass.TEMPERATURE,
                    state_class=SensorStateClass.MEASUREMENT,
                    unit_of_measurement=UnitOfTemperature.CELSIUS,
                )
            )

        for k, d in list(data_points.items()):
            if k == "PARAM_ID_BOILER_OUTDOOR_TEMP":
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="Outdoor Temperature",
                        icon="mdi:thermometer",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        state_class=SensorStateClass.MEASUREMENT,
                        unit_of_measurement=UnitOfTemperature.CELSIUS,
                    )
                )
            elif k.endswith("_TEMP"):
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name=d.name.title(),
                        icon="mdi:thermometer",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        state_class=SensorStateClass.MEASUREMENT,
                        unit_of_measurement=UnitOfTemperature.CELSIUS,
                    )
                )

            if k.endswith("_RATE"):
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name=d.name.title(),
                        icon="mdi:waves",
                        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                        state_class=SensorStateClass.MEASUREMENT,
                        unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_SECOND,
                    )
                )

            if k.endswith("_PRESSURE"):
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name=d.name.title(),
                        icon="mdi:gauge",
                        device_class=SensorDeviceClass.PRESSURE,
                        state_class=SensorStateClass.MEASUREMENT,
                        unit_of_measurement=UnitOfPressure.BAR,
                    )
                )

            if k.endswith(("WIFI_SIGNAL", "WIFI__SIGNAL")):
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="WiFi Signal",
                        icon="mdi:signal-variant",
                        state_class=SensorStateClass.MEASUREMENT,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    )
                )

            if k.endswith("_FAULT_CODE"):
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name=d.name.title(),
                        icon="mdi:alert",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    )
                )

            if k == "PARAM_ID_BOILER_OT_SLAVE_STATUS":
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="Status",
                        icon="mdi:state-machine",
                        device_class=SensorDeviceClass.ENUM,
                        options={
                            0: "Standby",
                            2: "Radiators",
                            4: "Domestic Water",
                            10: "Flame + Radiators",
                            12: "Flame + Domestic Water",
                        }
                    )
                )

            if k == "PARAM_ID_SYS_SOFT_VER":
                sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="Software Version",
                        icon="mdi:numeric",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    )
                )

        return sensors_data

    def get_numbers_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[NumberData]:
        """Get known sensors data."""

        numbers = []

        if data_points.get("PARAM_ID_TH_POWEROFF_FROZE_TEMP") is not None:
            numbers.append(
                NumberData(
                    data_point=data_points.pop("PARAM_ID_TH_POWEROFF_FROZE_TEMP"),
                    name="Poweroff Froze Temperature",
                    icon="mdi:snowflake-thermometer",
                    device_class=NumberDeviceClass.TEMPERATURE,
                    unit_of_measurement=UnitOfTemperature.CELSIUS,
                    mode=NumberMode.BOX,
                    min_value=5.0,
                    max_value=15.0,
                    step_value=0.5,
                )
            )

        return numbers

    def get_binary_sensors_data(
        self, data_points: dict[str, SmartDeviceDataPoint]
    ) -> list[SensorData]:
        """Get known sensors data."""

        binary_sensors_data = []

        for k, d in list(data_points.items()):
            if k == "PARAM_ID_BOILER_IS_ROOM_SENSOR_ENABL":
                binary_sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="External Temperature Sensor",
                        icon="mdi:thermometer-probe",
                        device_class=BinarySensorDeviceClass.PLUG,
                    )
                )

            elif k == "PARAM_ID_BOILER_IS_OT_CONNECTED":
                binary_sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="Thermostat Connection",
                        icon="mdi:thermostat-box",
                        device_class=BinarySensorDeviceClass.CONNECTIVITY,
                    )
                )

            elif k == "PARAM_ID_BOILER_OTC_ENABLE":
                binary_sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name="Outdoor Temperature Compensation",
                    )
                )

            elif k.endswith(("_ENABLE", "_ENABL", "_CONNECTED")):
                binary_sensors_data.append(
                    SensorData(
                        data_point=data_points.pop(k),
                        name=d.name.title(),
                    )
                )

        return binary_sensors_data

class Gateway:
    """Radiant Smart Gateway."""

    def __init__(self, uid: str, product_id: str) -> None:
        """Initialize the gateway."""
        self.uid = uid
        self.product_id = product_id

class SmartDeviceDataPoint:
    """Radiant Smart Data Point."""

    def __init__(
        self,
        device: SmartDevice,
        index: int,
        name: str,
        point_type: int,
        value: Any,
    ) -> None:
        """Initialize the data point."""
        self.device = device
        self.index = index
        self.name = name
        self.point_type = point_type
        self.value = value
        self._listener = set()

    def add_listener(self, callback) -> None:
        """Add entity callback."""
        self._listener.add(callback)

    def remove_listener(self, callback) -> None:
        """Add entity callback."""
        self._listener.discard(callback)

    def update_value(self, data: Any) -> str:
        """Update coming from MQTT."""
        self.value = data
        for c in self._listener:
            c()

    def get_value(self) -> Any:
        """Parse Topband data point value."""
        match self.point_type:
            case 1:  # int, enum or bool
                return int(self.value)
            case 2:  # float *10
                return float(self.value) / 10
            case 7:  # ? possibly constant value
                return int(self.value)
            case 8:
                return self.value
            case _:
                return self.value

    def set_value(self, value: Any) -> None:
        """Send new value to MQTT."""
        match self.point_type:
            case 1:  # int, enum or bool
                self.value = int(value)
            case 2:  # float *10
                self.value = int(value * 10)
            case 7:  # ? possibly constant value
                self.value = int(value)
            case 8:
                self.value = value
            case _:
                self.value = value
        self.device.send_data_point_update(self)


class SensorData:
    """Knonwn sensor config."""

    def __init__(
        self,
        data_point: SmartDeviceDataPoint,
        name: str,
        device_class: SensorDeviceClass | BinarySensorDeviceClass | None = None,
        state_class: SensorStateClass | None = None,
        icon: str | None = None,
        unit_of_measurement: str | None = None,
        options: dict[int, str] | None = None,
        entity_category: EntityCategory | None = None,
    ) -> None:
        """Initialize."""
        self.data_point = data_point
        self.name = name
        self.icon = icon
        self.device_class = device_class
        self.state_class = state_class
        self.unit_of_measurement = unit_of_measurement
        self.options = options
        self.entity_category = entity_category


class SwitchData:
    """Switch config."""

    def __init__(
        self,
        data_point: SmartDeviceDataPoint,
        name: str,
        device_class: SwitchDeviceClass | None = None,
        icon: str | None = None,
    ) -> None:
        """Initialize."""
        self.data_point = data_point
        self.name = name
        self.icon = icon
        self.device_class = device_class


class NumberData:
    """Switch config."""

    def __init__(
        self,
        data_point: SmartDeviceDataPoint,
        name: str,
        device_class: NumberDeviceClass | None = None,
        mode: NumberMode = NumberMode.AUTO,
        icon: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        step_value: float | None = None,
        unit_of_measurement: str | None = None,
    ) -> None:
        """Initialize."""
        self.data_point = data_point
        self.name = name
        self.icon = icon
        self.device_class = device_class
        self.mode = mode
        self.min_value = min_value
        self.max_value = max_value
        self.step_value = step_value
        self.unit_of_measurement = unit_of_measurement


class SelectData:
    """Select config."""

    def __init__(
        self,
        data_point: SmartDeviceDataPoint,
        name: str,
        options: dict[int, str],
        icon: str | None = None,
    ) -> None:
        """Initialize."""
        self.data_point = data_point
        self.name = name
        self.icon = icon
        self.options = options


class WaterHeaterData:
    """Radiant Smart Water Heater Data."""

    def __init__(
        self,
        name: str,
        min_temp: SmartDeviceDataPoint,
        max_temp: SmartDeviceDataPoint,
        current_temp: SmartDeviceDataPoint,
        target_temp: SmartDeviceDataPoint,
        target_temp_step: float,
        temp_unit: str,
        icon: str | None = None,
    ) -> None:
        """Initialize the water heater data."""
        self.name = name
        self.icon = icon
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.current_temp = current_temp
        self.target_temp = target_temp
        self.target_temp_step = target_temp_step
        self.temp_unit = temp_unit


class ClimateData:
    """Radiant Smart climate Data."""

    def __init__(
        self,
        name: str,
        current_temp: SmartDeviceDataPoint,
        target_temp: SmartDeviceDataPoint,
        hvac_mode: SmartDeviceDataPoint,
        hvac_modes: dict[int, HVACMode],
        target_temp_step: float,
        temp_unit: str,
        icon: str | None = None,
        min_temp: SmartDeviceDataPoint | None = None,
        max_temp: SmartDeviceDataPoint | None = None,
    ) -> None:
        """Initialize the climate data."""
        self.name = name
        self.icon = icon
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.current_temp = current_temp
        self.target_temp = target_temp
        self.target_temp_step = target_temp_step
        self.temp_unit = temp_unit
        self.hvac_mode = hvac_mode
        self.hvac_modes = hvac_modes
