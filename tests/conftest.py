"""Shared test fixtures.

The suite uses ``pytest-homeassistant-custom-component`` (the plugin that
ships Home Assistant's own test utilities) so tests run against the real
Home Assistant machinery: a real ``hass`` instance, the real config flow
manager and the real entity platforms.

Fast pure-unit tests (models, data point mapping, MQTT client internals)
run without a ``hass`` instance and use the lightweight fakes below, which
record interactions instead of talking to the network.

The repository root doubles as the test config directory so Home Assistant
can discover the integration in ``custom_components/``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

# Make the repository root importable before importing the integration;
# bare `pytest` (as used in CI) does not add the rootdir to sys.path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.radiant_smart.api import (
    DEVICE_LIST_URL,
    FAMILY_LIST_URL,
    TOKEN_URL,
)
from custom_components.radiant_smart.const import (
    CONF_COMPANY,
    CONF_HOME,
    CONF_TOKEN_DATA,
    DOMAIN,
)

# The credentials stored in a config entry (the account under test)
ENTRY_DATA: dict[str, Any] = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_COMPANY: "company",
    CONF_HOME: "home-1",
    CONF_TOKEN_DATA: {"token": "stale-token", "refresh_token": "stale-refresh"},
}

LOGIN_RESPONSE = {
    "status": 0,
    "data": {"token": "fresh-token", "refresh_token": "fresh-refresh"},
}

FAMILY_LIST_RESPONSE = {
    "status": 0,
    "data": [
        {"id": "home-1", "familyName": "Casa"},
        {"id": "home-2", "familyName": "Chalet"},
    ],
}

THERMOSTAT_MAC = "F1A399990000"  # TH623 as reported by the cloud (upper case)
BOILER_MAC = "a4cf12f5fe10"  # TH311 as reported by the cloud (lower case)

# Gateway (the TH311) identifiers from the capture
GATEWAY_UID = "12b602c3aa7c329c5021239f76b69585"
BOILER_PRODUCT_ID = "9b2d8fcf2e9d4c9390f5a1fb9749b8a4"
THERMOSTAT_PRODUCT_ID = "044e784511e14d34953c5f2221129a4e"


@pytest.fixture
def hass_config_dir() -> str:
    """Use the repository root as the test config directory.

    Home Assistant discovers custom integrations in
    ``<config dir>/custom_components``, so pointing at the repository root
    makes the integration under test importable by the config entry and
    flow managers.
    """
    return str(PROJECT_ROOT)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Enable loading of the custom integration for all tests."""


# ---------------------------------------------------------------------------
# Integration test helpers (real hass + mocked cloud + patched paho)
# ---------------------------------------------------------------------------


@pytest.fixture
def device_list_rows() -> list[dict[str, Any]]:
    """The real captured device list (TH311 boiler + TH623 thermostat)."""
    return json.loads((FIXTURES_DIR / "device_list.json").read_text())


@pytest.fixture
def mock_topband_cloud(aioclient_mock, device_list_rows) -> None:
    """Mock the Topband cloud REST endpoints used during setup."""
    aioclient_mock.post(TOKEN_URL, json=LOGIN_RESPONSE)
    aioclient_mock.post(
        DEVICE_LIST_URL, json={"status": 0, "data": {"rows": device_list_rows}}
    )
    aioclient_mock.post(FAMILY_LIST_URL, json=FAMILY_LIST_RESPONSE)


@pytest.fixture
async def setup_entry(
    hass,
    mock_topband_cloud,
    patch_paho,
) -> MockConfigEntry:
    """Set up a Radiant Smart config entry against the mocked cloud.

    Returns the loaded entry; the runtime data (api client with the
    connected MQTT recorder) is available as entry.runtime_data.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Radiant Smart (Casa)",
        data=dict(ENTRY_DATA),
        version=1,
        unique_id="home-1",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# Fakes for the fast unit tests (no hass instance involved)
# ---------------------------------------------------------------------------


class FakeConfigEntries:
    """Stand-in for hass.config_entries recording entry updates."""

    def __init__(self) -> None:
        self.updates: list[tuple[Any, dict[str, Any]]] = []
        self.reloads: list[str] = []
        self.flow = FakeFlowManager()
        self.known_entries: dict[str, Any] = {}

    def add_known_entry(self, entry: Any) -> None:
        """Register an entry for lookups (e.g. during reauth flows)."""
        self.known_entries[entry.entry_id] = entry

    def async_get_known_entry(self, entry_id: str) -> Any:
        """Return the registered entry."""
        return self.known_entries[entry_id]

    def async_entry_for_domain_unique_id(self, domain: str, unique_id: str) -> None:
        """No existing entries in tests."""
        return None

    def async_update_entry(
        self, entry: Any, data: dict[str, Any] | None = None, **kwargs: Any
    ) -> bool:
        """Record the update and apply it, like Home Assistant does."""
        self.updates.append((entry, data))
        if data is not None:
            entry.data = data
        return True

    def async_schedule_reload(self, entry_id: str) -> None:
        """Record scheduled reloads."""
        self.reloads.append(entry_id)


class FakeFlowManager:
    """Stand-in for hass.config_entries.flow."""

    def __init__(self) -> None:
        self.aborts: list[str] = []

    def async_progress_by_handler(
        self, handler, *, include_uninitialized=False, match_context=None
    ) -> list:
        """No concurrent flows in tests."""
        return []

    def async_abort(self, flow_id: str) -> None:
        """Record aborted flows."""
        self.aborts.append(flow_id)


class FakeHass:
    """Minimal stand-in for HomeAssistant for the client classes.

    Must be created inside a running event loop (tests run in asyncio
    mode auto); executor jobs run inline and jobs added from callbacks are
    collected as tasks that tests can await.
    """

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.config_entries = FakeConfigEntries()
        self.data: dict[str, Any] = {}
        self.tasks: list[asyncio.Future] = []

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        """Run the job inline (it is a no-op or pure function in tests)."""
        return func(*args)

    def async_run_hass_job(self, job: Any, *args: Any) -> None:
        """Run a timer job; coroutines are collected as tasks."""
        target = getattr(job, "target", job)
        result = target(*args)
        if asyncio.iscoroutine(result):
            self.tasks.append(self.loop.create_task(result))

    def add_job(self, target: Any, *args: Any) -> None:
        """Schedule a coroutine from a callback, like hass.add_job does."""
        self.tasks.append(self.loop.create_task(target))

    async def async_block_till_done(self) -> None:
        """Await all collected tasks."""
        while self.tasks:
            await self.tasks.pop(0)


class FakeEntry:
    """Stand-in for a loaded config entry."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.entry_id = "test_entry"
        self.title = "Radiant Smart"
        self.state = "loaded"
        self.update_listeners: tuple = ()
        self.async_start_reauth_calls = 0

    def async_start_reauth(self, hass: Any) -> None:
        """Record reauth flow starts."""
        self.async_start_reauth_calls += 1


class FakeApi:
    """Stand-in for TopbandCloudApi recording sent MQTT commands."""

    def __init__(self) -> None:
        self.commands: list[tuple[Any, dict[str, Any]]] = []

    async def async_send_mqtt_command(
        self, device: Any, command: dict[str, Any]
    ) -> None:
        """Record the command instead of publishing it."""
        self.commands.append((device, command))


# ---------------------------------------------------------------------------
# paho recorders
# ---------------------------------------------------------------------------


class PahoRecorder:
    """Records the network interactions of a patched paho client."""

    def __init__(self) -> None:
        self.connects: list[tuple[str, int, int]] = []
        self.subscriptions: list[str] = []
        self.published: list[tuple[str, str]] = []
        self.disconnects = 0


@pytest.fixture
def patch_paho(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the paho network calls with per-instance recorders."""
    import paho.mqtt.client as mqtt

    def _recorder(self) -> PahoRecorder:
        if "_test_recorder" not in self.__dict__:
            self.__dict__["_test_recorder"] = PahoRecorder()
        return self.__dict__["_test_recorder"]

    monkeypatch.setattr(
        mqtt.Client,
        "recorder",
        property(_recorder),
        raising=False,
    )
    monkeypatch.setattr(
        mqtt.Client,
        "connect",
        lambda self, host, port=1883, keepalive=60, **kwargs: (
            self.recorder.connects.append((host, port, keepalive))
        ),
    )
    monkeypatch.setattr(mqtt.Client, "loop_start", lambda self: None)
    monkeypatch.setattr(mqtt.Client, "loop_stop", lambda self: None)
    monkeypatch.setattr(
        mqtt.Client,
        "disconnect",
        lambda self: setattr(
            self.recorder, "disconnects", self.recorder.disconnects + 1
        ),
    )
    monkeypatch.setattr(
        mqtt.Client,
        "subscribe",
        lambda self, topic, **kwargs: (
            self.recorder.subscriptions.append(topic) or (mqtt.MQTT_ERR_SUCCESS, 1)
        ),
    )
    monkeypatch.setattr(
        mqtt.Client,
        "publish",
        lambda self, topic, payload=None, **kwargs: (
            self.recorder.published.append((topic, payload))
            or types.SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)
        ),
    )
