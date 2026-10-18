"""Integration tests for config entry setup, unload and migration.

These run against a real Home Assistant instance (via
pytest-homeassistant-custom-component) with the Topband cloud mocked at
the HTTP layer and paho replaced by recorders.
"""

import pytest
from conftest import (
    BOILER_MAC,
    BOILER_PRODUCT_ID,
    ENTRY_DATA,
    GATEWAY_UID,
    LOGIN_RESPONSE,
    THERMOSTAT_MAC,
    THERMOSTAT_PRODUCT_ID,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.radiant_smart.const import CONF_TOKEN_DATA, DOMAIN


@pytest.fixture(autouse=True)
def _patch_paho_unit(patch_paho) -> None:
    """Patch paho network calls for all tests in this module."""


class TestSetup:
    async def test_setup_and_unload(self, hass, setup_entry) -> None:
        """A full setup loads the entry and registers the devices."""
        entry = setup_entry
        assert entry.state is ConfigEntryState.LOADED
        assert entry.version == 1

        # Fresh tokens from the login are persisted to the entry
        assert entry.data[CONF_TOKEN_DATA] == LOGIN_RESPONSE["data"]

        # Both cloud devices are registered with upper case identifiers
        registry = dr.async_get(hass)
        boiler = registry.async_get_device_by_identifier(
            (DOMAIN, BOILER_MAC.upper()), entry.entry_id
        )
        thermostat = registry.async_get_device_by_identifier(
            (DOMAIN, THERMOSTAT_MAC.upper()), entry.entry_id
        )
        assert boiler is not None
        assert boiler.model == "TH311"
        assert thermostat is not None
        assert thermostat.model == "TH623"
        # Both cloud devices belong to the config entry
        assert {boiler.config_entry_id, thermostat.config_entry_id} == {entry.entry_id}

        api = entry.runtime_data
        assert api.mqtt.connected
        assert api.mqtt.subscriptions == {
            f"{BOILER_PRODUCT_ID}/{GATEWAY_UID}/business",
            f"{BOILER_PRODUCT_ID}/{GATEWAY_UID}/upload/point/data",
            f"{THERMOSTAT_PRODUCT_ID}/{GATEWAY_UID}/business",
            f"{THERMOSTAT_PRODUCT_ID}/{GATEWAY_UID}/upload/point/data",
        }

        # Unloading disconnects the MQTT client
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert not api.mqtt.connected

    async def test_setup_persists_fresh_tokens_only_once(
        self, hass, mock_topband_cloud
    ) -> None:
        """Token data is written back to the config entry only on change."""
        data_with_fresh_tokens = {
            **ENTRY_DATA,
            CONF_TOKEN_DATA: LOGIN_RESPONSE["data"],
        }
        entry = MockConfigEntry(
            domain=DOMAIN, data=data_with_fresh_tokens, version=1, unique_id="home-1"
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # The login response equals the stored tokens: no rewrite
        assert entry.data[CONF_TOKEN_DATA] == LOGIN_RESPONSE["data"]


class TestSetupFailures:
    async def test_invalid_credentials_start_reauth(self, hass, aioclient_mock) -> None:
        """Rejected credentials move the entry to SETUP_ERROR and start a
        reauth flow."""
        from unittest import mock as unittest_mock

        import aiohttp

        from custom_components.radiant_smart.api import TOKEN_URL

        # aiohttp raises ClientResponseError for the 401 (raise_for_status)
        request_info = unittest_mock.Mock(real_url=TOKEN_URL)
        aioclient_mock.post(
            TOKEN_URL,
            exc=aiohttp.ClientResponseError(
                request_info=request_info, history=(), status=401
            ),
        )

        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(ENTRY_DATA), version=1, unique_id="home-1"
        )
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_ERROR
        from homeassistant.config_entries import SOURCE_REAUTH

        assert entry.async_get_active_flows(hass, {SOURCE_REAUTH})

    async def test_network_failure_sets_up_retry(self, hass, aioclient_mock) -> None:
        """A connection problem moves the entry to SETUP_RETRY."""
        import aiohttp

        from custom_components.radiant_smart.api import TOKEN_URL

        aioclient_mock.post(TOKEN_URL, exc=aiohttp.ClientError("network down"))

        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(ENTRY_DATA), version=1, unique_id="home-1"
        )
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestMigration:
    async def test_migrates_version_0(self, hass, mock_topband_cloud) -> None:
        """Entries created before v0.2.0 are migrated by bumping the version."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**ENTRY_DATA, CONF_TOKEN_DATA: {"token": "stale"}},
            version=0,
            unique_id="home-1",
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.version == 1

    async def test_downgrade_is_rejected(self, hass) -> None:
        """Migration from a future version is not supported."""
        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(ENTRY_DATA), version=2, unique_id="home-1"
        )
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.MIGRATION_ERROR
