"""MQTT client for the Topband cloud."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import (
    CMD_POINT_COMMAND,
    CMD_POINT_UPDATE,
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
)

if TYPE_CHECKING:
    from .models import SmartDevice

_LOGGER = logging.getLogger(__name__)

# Minimum time between reconnect attempts to avoid reconnect storms
RECONNECT_MIN_INTERVAL = 30.0


class TopbandMqttError(Exception):
    """Raised for MQTT errors."""


class TopbandMqttReauthRequired(Exception):
    """Raised when reconnecting needs new credentials from the user.

    The MQTT client stops retrying when this is raised by the re-auth
    callback; the config entry reauth flow takes over and reloads the
    entry once the credentials are fixed.
    """


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
        self._subscriptions: set[str] = set()
        self._reconnecting = False
        self._reconnect_scheduled = False
        self._stopped = True
        self._last_reconnect = 0.0
        self._ssl_context: ssl.SSLContext | None = None
        self._retry_cancel: Callable[[], None] | None = None

    @property
    def connected(self) -> bool:
        """Return True if a client is active (used by diagnostics)."""
        return self._client is not None

    @property
    def subscriptions(self) -> set[str]:
        """Return the set of subscribed topics (used by diagnostics)."""
        return set(self._subscriptions)

    async def async_connect(self) -> None:
        """Connect to the Topband cloud MQTT broker."""
        self._stopped = False
        if self._client is not None:
            return
        await self._async_create_client()

    async def async_disconnect(self) -> None:
        """Disconnect from the Topband cloud MQTT broker."""
        self._stopped = True
        self._reconnecting = False
        self._cancel_retry()
        await self._async_teardown_client()

    def _cancel_retry(self) -> None:
        """Cancel a pending reconnect retry timer, if any."""
        if self._retry_cancel is not None:
            self._retry_cancel()
            self._retry_cancel = None

    async def _async_get_ssl_context(self) -> ssl.SSLContext:
        """Return the TLS context for the broker connection.

        Creating the default context loads the system CA bundle from disk,
        which blocks, so it must not run in the event loop. The context is
        cached because building it again on every reconnect is wasteful.
        """
        if self._ssl_context is None:
            self._ssl_context = await self._hass.async_add_executor_job(
                ssl.create_default_context
            )
        return self._ssl_context

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
        client.tls_set_context(await self._async_get_ssl_context())
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

        Runs on the event loop, scheduled from the paho thread. Retries
        with a fixed interval until the connection is re-established or
        the entry is unloaded or needs a user reauth.
        """
        if self._stopped or self._reconnecting:
            # Reconnect intent has been cancelled (unload) or another task
            # is already handling it; reset the scheduling marker so a
            # future drop can reconnect.
            self._reconnect_scheduled = False
            return
        self._reconnecting = True
        self._reconnect_scheduled = False
        try:
            # Throttle reconnect attempts
            elapsed = time.monotonic() - self._last_reconnect
            if elapsed < RECONNECT_MIN_INTERVAL:
                await asyncio.sleep(RECONNECT_MIN_INTERVAL - elapsed)
            self._last_reconnect = time.monotonic()

            _LOGGER.info("Reconnecting to Topband cloud MQTT broker")
            await self._async_teardown_client()
            try:
                await self._reauth_callback()
            except TopbandMqttReauthRequired:
                _LOGGER.error(
                    "Re-authentication with the Topband cloud failed; "
                    "waiting for the user to re-authenticate Radiant Smart"
                )
                return
            await self._async_create_client()
            _LOGGER.info("Reconnected to Topband cloud MQTT broker")
        except Exception as err:  # noqa: BLE001 - must not kill the task
            _LOGGER.error(
                "MQTT reconnect failed, retrying in %s seconds: %s",
                RECONNECT_MIN_INTERVAL,
                err,
            )
            if not self._stopped:
                # Schedule a tracked retry; the timer is cancelled when the
                # client is disconnected or a newer retry supersedes it.
                self._cancel_retry()
                self._retry_cancel = async_call_later(
                    self._hass,
                    RECONNECT_MIN_INTERVAL,
                    self._async_retry_reconnect,
                )
        finally:
            self._reconnecting = False

    @callback
    def _async_retry_reconnect(self, _now: datetime) -> None:
        """Attempt another reconnect when the retry timer fires."""
        self._retry_cancel = None
        self._hass.add_job(self._async_reconnect())

    def subscribe(self, topic: str) -> None:
        """Subscribe to a topic, queuing it if not connected yet."""
        if topic in self._subscriptions:
            return
        self._subscriptions.add(topic)
        if self._client is not None:
            self._subscribe(self._client, topic)

    async def async_send_command(
        self, device: SmartDevice, command: dict[str, Any]
    ) -> None:
        """Send a data point command to a device."""
        client = self._client
        if client is None:
            raise TopbandMqttError("MQTT client is not connected")

        self._serial += 1
        payload = json.dumps(
            {
                "common": {
                    "productId": device.gateway.product_id,
                    # The app sends the serial as a string
                    "serial": str(self._serial),
                    "timestamp": int(time.time() * 1000),
                    "uid": device.gateway.uid,
                },
                "data": [
                    {
                        "cmd": CMD_POINT_COMMAND,
                        "command": [command],
                        "mac": device.mac_address,
                    }
                ],
                "method": "command",
            }
        )
        topic = f"{device.gateway.product_id}/{device.gateway.uid}/download/point/data"

        def _publish() -> None:
            info = client.publish(topic, payload)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise TopbandMqttError(f"Failed to publish to {topic}: rc={info.rc}")

        await self._hass.async_add_executor_job(_publish)
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
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        """Handle MQTT connection event (runs on the paho thread)."""
        if reason_code == 0:
            _LOGGER.info("Connected to Topband cloud MQTT broker")
            for topic in self._subscriptions:
                self._subscribe(client, topic)
        else:
            _LOGGER.error("Topband cloud MQTT connection failed: %s", reason_code)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: None,
        _disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        """Handle MQTT disconnection (runs on the paho thread)."""
        if (
            self._stopped
            or self._client is None
            or self._reconnecting
            or self._reconnect_scheduled
        ):
            # Intentional disconnect or a reconnect is already scheduled
            return
        self._reconnect_scheduled = True
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
            if not isinstance(item, dict):
                continue
            if item.get("cmd") != CMD_POINT_UPDATE:
                _LOGGER.debug("Ignoring MQTT command %s", item.get("cmd"))
                continue

            mac = str(item.get("mac", "")).upper()
            device = self._device_lookup(mac)
            if device is None:
                _LOGGER.warning("Received MQTT message for unknown device %s", mac)
                continue

            command = item.get("command") or {}
            # Data point updates must run on the event loop so that entity
            # state updates are thread safe.
            self._hass.loop.call_soon_threadsafe(device.handle_mqtt_command, command)
