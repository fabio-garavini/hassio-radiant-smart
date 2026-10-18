"""Helpers shared by the test modules (paho recorders, fakes)."""

from __future__ import annotations

from typing import Any

import paho.mqtt.client as mqtt
from conftest import FakeHass

from custom_components.radiant_smart.const import MQTT_BROKER_HOST, MQTT_BROKER_PORT
from custom_components.radiant_smart.models import Gateway
from custom_components.radiant_smart.mqtt import TopbandMqttClient

KEEPALIVE = 60


class FakeDevice:
    """Stand-in for SmartDevice recording MQTT commands."""

    def __init__(self, mac="AA:BB:CC", gateway_uid="gw", gateway_product="gp"):
        self.mac_address = mac
        self.gateway = Gateway(uid=gateway_uid, product_id=gateway_product)
        self.received_commands: list[dict[str, Any]] = []

    def handle_mqtt_command(self, command: dict[str, Any]) -> None:
        """Record updates dispatched from the MQTT client."""
        self.received_commands.append(command)


def make_mqtt_client(
    token: str | None = "token",
    reauth: Any = None,
    device_lookup: Any = None,
) -> TopbandMqttClient:
    """Create a TopbandMqttClient wired to a FakeHass.

    The re-auth callback records calls into `reauth.calls` by default.
    """
    calls: list[str] = []

    async def default_reauth() -> None:
        calls.append("called")

    client = TopbandMqttClient(
        FakeHass(),
        token_provider=lambda: token,
        reauth_callback=reauth or default_reauth,
        device_lookup=device_lookup or (lambda mac: None),
    )
    client.reauth_calls = calls  # type: ignore[attr-defined]
    return client


async def connect_client(
    mqtt_client: TopbandMqttClient, token: str = "token"
) -> mqtt.Client:
    """Connect the client and return the underlying paho client."""
    await mqtt_client.async_connect()
    return mqtt_client._client


async def disconnect_client(mqtt_client: TopbandMqttClient) -> None:
    """Disconnect the client and drain pending hass jobs."""
    await mqtt_client.async_disconnect()


def expected_connect_args() -> list[tuple[str, int, int]]:
    """The broker arguments our client must connect with."""
    return [(MQTT_BROKER_HOST, MQTT_BROKER_PORT, KEEPALIVE)]
