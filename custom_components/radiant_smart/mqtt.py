"""MQTT client for the Topband cloud."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

import paho.mqtt.client as mqtt

from .const import MQTT_BROKER_HOST, MQTT_BROKER_PORT

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import SmartDevice

_LOGGER = logging.getLogger(__name__)

# Minimum time between reconnect attempts to avoid reconnect storms
RECONNECT_MIN_INTERVAL = 30.0


class TopbandMqttError(Exception):
    """Raised for MQTT errors."""


class TopbandMqttClient:
    """Handle the MQTT connection to the Topband cloud broker."""

    def __init__(
        self,
        hass: HomeAssistant,
        token_provider: Callable[[], str | None],
        reauth_callback: Callable[[], Any],
        device_lookup: Callable[[str], SmartDevice | None],
    ) -> None:
        """Initialize the MQTT client."""
        self._hass = hass
        self._token_provider = token_provider
        self._reauth_callback = reauth_callback
        self._device_lookup = device_lookup
        self._client: mqtt.Client | None = None
        self._serial = 0
        self._subscriptions: list[str] = []
        self._reconnecting = False
        self._last_reconnect = 0.0

    async def async_connect(self) -> None:
        """Connect to the Topband cloud MQTT broker."""
        if self._client is not None:
            return
        await self._async_create_client()

    async def async_disconnect(self) -> None:
        """Disconnect from the Topband cloud MQTT broker."""
        self._reconnecting = False
        await self._async_teardown_client()

    async def _async_create_client(self) -> None:
        """Create a client with the current token and connect it."""
        token = self._token_provider()
        if not token:
            raise TopbandMqttError("No token available for MQTT connection")

        client = mqtt.Client(
            client_id=f"home-assistant-{uuid.uuid4()}",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        client.username_pw_set(f"app/{token}", token)
        client.tls_set_context(ssl.create_default_context())
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        await self._hass.async_add_executor_job(
            client.connect, MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60
        )
        client.loop_start()
        self._client = client

        for topic in self._subscriptions:
            self._subscribe(client, topic)

    async def _async_teardown_client(self) -> None:
        """Stop and disconnect the current client, if any."""
        client, self._client = self._client, None
        if client is None:
            return

        def _disconnect() -> None:
            client.disconnect()
            client.loop_stop()

        await self._hass.async_add_executor_job(_disconnect)

    async def _async_reconnect(self) -> None:
        """Re-authenticate and reconnect with a fresh token.

        Runs on the event loop, scheduled from the paho thread.
        """
        if self._client is None or self._reconnecting:
            return
        self._reconnecting = True
        try:
            # Throttle reconnect attempts
            elapsed = time.monotonic() - self._last_reconnect
            if elapsed < RECONNECT_MIN_INTERVAL:
                await asyncio.sleep(RECONNECT_MIN_INTERVAL - elapsed)
            self._last_reconnect = time.monotonic()

            _LOGGER.info("Reconnecting to Topband cloud MQTT broker")
            await self._async_teardown_client()
            await self._reauth_callback()
            await self._async_create_client()
            _LOGGER.info("Reconnected to Topband cloud MQTT broker")
        except Exception as err:  # noqa: BLE001 - must not kill the task
            _LOGGER.error(
                "MQTT reconnect failed, retrying in %s seconds: %s",
                RECONNECT_MIN_INTERVAL,
                err,
            )
            # The next attempt is triggered by a new on_disconnect event or
            # scheduled again after the throttle interval.
            asyncio.get_running_loop().call_later(
                RECONNECT_MIN_INTERVAL,
                lambda: self._hass.add_job(self._async_reconnect()),
            )
        finally:
            self._reconnecting = False

    def subscribe(self, topic: str) -> None:
        """Subscribe to a topic, queuing it if not connected yet."""
        self._subscriptions.append(topic)
        if self._client is not None:
            self._subscribe(self._client, topic)

    async def async_send_command(
        self, device: SmartDevice, command: dict[str, Any]
    ) -> None:
        """Send a data point command to a device."""
        if self._client is None:
            raise TopbandMqttError("MQTT client is not connected")

        self._serial += 1
        payload = json.dumps(
            {
                "common": {
                    "productId": device.gateway.product_id,
                    "serial": self._serial,
                    "timestamp": int(time.time() * 1000),
                    "uid": device.gateway.uid,
                },
                "data": [
                    {
                        "cmd": 99,
                        "command": [command],
                        "mac": device.mac_address,
                    }
                ],
                "method": "command",
            }
        )
        topic = (
            f"{device.gateway.product_id}/{device.gateway.uid}/download/point/data"
        )
        await self._hass.async_add_executor_job(
            self._client.publish, topic, payload
        )
        _LOGGER.debug("Published to %s: %s", topic, payload)

    def _subscribe(self, client: mqtt.Client, topic: str) -> None:
        """Subscribe to a topic on a connected client."""
        result, _mid = client.subscribe(topic)
        if result != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.error("Failed to subscribe to %s: %s", topic, result)
        else:
            _LOGGER.debug("Subscribed to %s", topic)

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: None,
        _flags: mqtt.ConnectFlags,
        reason_code,
        _properties: mqtt.Properties | None,
    ) -> None:
        """Handle MQTT connection event (runs on the paho thread)."""
        if reason_code == 0:
            _LOGGER.info("Connected to Topband cloud MQTT broker")
            for topic in self._subscriptions:
                self._subscribe(client, topic)
        else:
            _LOGGER.error(
                "Topband cloud MQTT connection failed: %s", reason_code
            )

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: None,
        _disconnect_flags: mqtt.DisconnectFlags,
        reason_code,
        _properties: mqtt.Properties | None,
    ) -> None:
        """Handle MQTT disconnection (runs on the paho thread)."""
        if self._client is None or self._reconnecting:
            # Intentional disconnect or a reconnect is already in progress
            return
        self._reconnecting = True
        _LOGGER.warning(
            "Lost connection to Topband cloud MQTT broker (%s), reconnecting",
            reason_code,
        )
        self._hass.add_job(self._async_reconnect())

    def _on_message(
        self, _client: mqtt.Client, _userdata: None, msg: mqtt.MQTTMessage
    ) -> None:
        """Handle incoming MQTT messages (runs on the paho thread)."""
        _LOGGER.debug("Received on %s: %s", msg.topic, msg.payload)
        try:
            data: dict[str, Any] = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            _LOGGER.warning("Received invalid MQTT payload on %s", msg.topic)
            return

        if data.get("method") != "command":
            return

        for item in data.get("data") or []:
            if item.get("cmd") != 98:
                _LOGGER.debug("Ignoring MQTT command %s", item.get("cmd"))
                continue

            mac = str(item.get("mac", "")).upper()
            device = self._device_lookup(mac)
            if device is None:
                _LOGGER.warning(
                    "Received MQTT message for unknown device %s", mac
                )
                continue

            command = item.get("command") or {}
            # Data point updates must run on the event loop so that entity
            # state updates are thread safe.
            self._hass.loop.call_soon_threadsafe(
                device.handle_mqtt_command, command
            )
