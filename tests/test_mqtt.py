"""Tests for the Topband cloud MQTT client.

The paho network calls are replaced with recorders; the tests focus on the
connection lifecycle, the reconnect state machine and message dispatch.
"""

import asyncio
import json
import types
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from tests_helpers import (
    FakeDevice,
    connect_client,
    disconnect_client,
    expected_connect_args,
    make_mqtt_client,
)

import custom_components.radiant_smart.mqtt as mqtt_mod
from custom_components.radiant_smart.const import (
    CMD_POINT_COMMAND,
    CMD_POINT_UPDATE,
)
from custom_components.radiant_smart.mqtt import (
    TopbandMqttError,
    TopbandMqttReauthRequired,
)


@pytest.fixture(autouse=True)
def _paho_recorder(patch_paho) -> None:
    """Patch the paho network calls for every MQTT test."""


@pytest.fixture(autouse=True)
def _fast_reconnect(monkeypatch):
    """Remove the reconnect throttle so tests do not wait."""
    monkeypatch.setattr(mqtt_mod, "RECONNECT_MIN_INTERVAL", 0)


class TestConnect:
    async def test_connect_without_token_raises(self) -> None:
        client = make_mqtt_client(token=None)
        with pytest.raises(TopbandMqttError, match="No token available"):
            await client.async_connect()

    async def test_connect_uses_tls_and_credentials(self) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)

        assert paho.recorder.connects == expected_connect_args()
        # The Topband broker authenticates with the JWT as user and password
        assert paho._username == b"app/the-token"
        assert paho._password == b"the-token"
        assert paho._ssl_context is not None

    async def test_ssl_context_is_cached(self, monkeypatch) -> None:
        """The CA bundle load must run in the executor, and only once."""
        client = make_mqtt_client(token="the-token")
        executor_calls = []

        async def record_executor(func, *args):
            executor_calls.append(func)
            return func(*args)

        monkeypatch.setattr(client._hass, "async_add_executor_job", record_executor)
        await connect_client(client)
        await disconnect_client(client)
        await connect_client(client)

        context_builds = [
            call
            for call in executor_calls
            if getattr(call, "__name__", "") == "create_default_context"
        ]
        assert len(context_builds) == 1  # built once, reused afterwards
        assert client.connected

    async def test_disconnect_stops_client(self) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)

        await client.async_disconnect()

        assert not client.connected
        assert paho.recorder.disconnects == 1
        # A second disconnect is a no-op
        await client.async_disconnect()


class TestSubscriptions:
    async def test_subscribe_deduplicates(self) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)

        client.subscribe("a/b")
        client.subscribe("a/b")
        client.subscribe("c/d")

        assert client.subscriptions == {"a/b", "c/d"}
        assert sorted(paho.recorder.subscriptions) == ["a/b", "c/d"]

    async def test_queued_subscriptions_sent_on_connect(self) -> None:
        client = make_mqtt_client(token="the-token")
        client.subscribe("a/b")

        paho = await connect_client(client)

        assert paho.recorder.subscriptions == ["a/b"]


class TestSendCommand:
    async def test_send_command_without_client_raises(self) -> None:
        client = make_mqtt_client(token="the-token")
        with pytest.raises(TopbandMqttError, match="not connected"):
            await client.async_send_command(
                FakeDevice(), {"i": 1, "t": 1, "len": 0, "v": 1}
            )

    async def test_send_command_payload(self) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)
        device = FakeDevice(mac="aa:bb:cc", gateway_uid="gw", gateway_product="gp")

        await client.async_send_command(device, {"i": 42, "t": 2, "len": 0, "v": 205})

        topic, payload = paho.recorder.published[0]
        assert topic == "gp/gw/download/point/data"
        data = json.loads(payload)
        assert data["method"] == "command"
        assert data["common"]["productId"] == "gp"
        assert data["common"]["uid"] == "gw"
        assert data["common"]["serial"] == "1"
        assert data["data"] == [
            {
                "cmd": CMD_POINT_COMMAND,
                "command": [{"i": 42, "t": 2, "len": 0, "v": 205}],
                "mac": "aa:bb:cc",
            }
        ]

    async def test_send_command_fails_when_publish_fails(self, monkeypatch) -> None:
        client = make_mqtt_client(token="the-token")
        await connect_client(client)

        def failing_publish(self, topic, payload):
            return types.SimpleNamespace(rc=mqtt.MQTT_ERR_NO_CONN)

        monkeypatch.setattr(mqtt.Client, "publish", failing_publish)
        with pytest.raises(TopbandMqttError, match="Failed to publish"):
            await client.async_send_command(
                FakeDevice(), {"i": 1, "t": 1, "len": 0, "v": 1}
            )


class TestMessageDispatch:
    @staticmethod
    def _message(payload: Any) -> mqtt.MQTTMessage:
        message = mqtt.MQTTMessage()
        message.topic = b"gp/gw/upload/point/data"
        message.payload = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        return message

    async def test_cmd_98_updates_device(self) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)
        device = FakeDevice(mac="AA:BB:CC")
        client._device_lookup = lambda mac: device if mac == "AA:BB:CC" else None

        client._on_message(
            paho,
            None,
            self._message(
                {
                    "method": "command",
                    "data": [
                        {
                            "cmd": CMD_POINT_UPDATE,
                            "mac": "aa:bb:cc",
                            "command": {"SYS_WORK_MODE": {"i": 1, "t": 1, "v": 10}},
                        }
                    ],
                }
            ),
        )
        # The update is handed to the event loop...
        await asyncio.sleep(0)

        assert device.received_commands == [
            {"SYS_WORK_MODE": {"i": 1, "t": 1, "v": 10}}
        ]

    async def test_unknown_device_ignored(self, caplog) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)
        client._device_lookup = lambda mac: None

        client._on_message(
            paho,
            None,
            self._message(
                {
                    "method": "command",
                    "data": [
                        {
                            "cmd": CMD_POINT_UPDATE,
                            "mac": "nope",
                            "command": {},
                        }
                    ],
                }
            ),
        )
        await asyncio.sleep(0)
        assert "unknown device NOPE" in caplog.text

    async def test_invalid_payload_ignored(self) -> None:
        client = make_mqtt_client(token="the-token")
        paho = await connect_client(client)
        client._device_lookup = lambda mac: None

        client._on_message(paho, None, self._message(b"not json"))  # must not raise
        client._on_message(  # other methods are ignored
            paho,
            None,
            self._message({"method": "event", "data": [{"cmd": 1}]}),
        )
        await asyncio.sleep(0)


class TestReconnect:
    """The reconnect state machine (regression tests).

    Bugs fixed here: on_disconnect used to set the reconnecting flag which
    made the scheduled reconnect a no-op, and a failed reconnect left the
    client torn down with no further retry ever scheduled.
    """

    async def test_reconnects_with_fresh_token_after_drop(self) -> None:
        client = make_mqtt_client(token="token-1")
        paho1 = await connect_client(client)

        # Simulate the broker dropping the connection (paho thread context).
        client._on_disconnect(paho1, None, None, 7, None)
        await client._hass.async_block_till_done()

        assert client.reauth_calls == ["called"]
        assert client.connected  # reconnected with a new client instance
        assert client._client is not paho1

    async def test_no_reconnect_after_intentional_disconnect(self) -> None:
        client = make_mqtt_client(token="token-1")
        paho = await connect_client(client)

        await client.async_disconnect()
        # A stray late on_disconnect event must not resurrect the client.
        client._on_disconnect(paho, None, None, 0, None)
        await client._hass.async_block_till_done()

        assert client.reauth_calls == []
        assert not client.connected

    async def test_reauth_required_stops_retrying(self) -> None:
        """When credentials are bad, stop retrying: the reauth flow reloads
        the entry once the user has fixed them."""
        client = make_mqtt_client(token="token-1")
        paho = await connect_client(client)
        attempts: list[str] = []

        async def failing_reauth() -> None:
            attempts.append("called")
            raise TopbandMqttReauthRequired("bad credentials")

        client._reauth_callback = failing_reauth

        client._on_disconnect(paho, None, None, 7, None)
        await client._hass.async_block_till_done()
        # Give any (wrongly) scheduled retry a chance to run.
        for _ in range(4):
            await asyncio.sleep(0)

        assert attempts == ["called"]  # no retry loop
        assert not client.connected

    async def test_generic_failure_is_retried(self) -> None:
        client = make_mqtt_client(token="token-1")
        paho = await connect_client(client)
        attempts: list[str] = []

        async def failing_once() -> None:
            attempts.append("called")
            if len(attempts) < 2:
                raise TopbandMqttError("broker down")

        client._reauth_callback = failing_once

        client._on_disconnect(paho, None, None, 7, None)
        for _ in range(6):
            await asyncio.sleep(0)

        # First attempt failed, the scheduled retry succeeded.
        assert attempts == ["called", "called"]
        assert client.connected

    async def test_concurrent_drops_reconnect_once(self) -> None:
        """Multiple rapid drops must not trigger concurrent reconnects."""
        client = make_mqtt_client(token="token-1")
        paho = await connect_client(client)

        # Several on_disconnect events arriving before the job runs.
        client._on_disconnect(paho, None, None, 7, None)
        client._on_disconnect(paho, None, None, 7, None)
        client._on_disconnect(paho, None, None, 7, None)
        await client._hass.async_block_till_done()

        assert client.reauth_calls == ["called"]
        assert client.connected

    async def test_reauth_failure_during_setup_is_retried_later(
        self, monkeypatch
    ) -> None:
        """A failed reconnect must schedule a retry even though the client
        was already torn down (the old code never retried in this state)."""
        client = make_mqtt_client(token="token-1")
        paho = await connect_client(client)
        attempts: list[str] = []

        async def always_failing() -> None:
            attempts.append("called")
            raise TopbandMqttError("broker down")

        client._reauth_callback = always_failing

        client._on_disconnect(paho, None, None, 7, None)
        for _ in range(6):
            await asyncio.sleep(0)

        # The retry loop keeps trying (at least twice here).
        assert len(attempts) >= 2
        assert not client.connected
        # Unloading cancels the pending retry timer.
        assert client._retry_cancel is not None
        await client.async_disconnect()

    async def test_disconnect_cancels_pending_retry(self, monkeypatch) -> None:
        """Unloading the client must cancel a pending retry timer."""
        client = make_mqtt_client(token="token-1")
        paho = await connect_client(client)
        # Long retry interval so the timer stays pending
        monkeypatch.setattr(mqtt_mod, "RECONNECT_MIN_INTERVAL", 9999.0)
        attempts: list[str] = []

        async def always_failing() -> None:
            attempts.append("called")
            raise TopbandMqttError("broker down")

        client._reauth_callback = always_failing

        client._on_disconnect(paho, None, None, 7, None)
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(attempts) == 1
        assert client._retry_cancel is not None

        await client.async_disconnect()
        await client._hass.async_block_till_done()
        for _ in range(3):
            await asyncio.sleep(0)

        # No further reconnect attempts were scheduled after the unload
        assert len(attempts) == 1
        assert not client.connected
