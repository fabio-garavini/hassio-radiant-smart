"""Config flow tests running through the real Home Assistant flow manager.

The Topband cloud endpoints are mocked at the HTTP level; the flows are
driven exactly like Home Assistant drives them (flow init / configure /
finish), including unique-id handling and the reauth flow.
"""

from conftest import (
    ENTRY_DATA,
    FAMILY_LIST_RESPONSE,
    LOGIN_RESPONSE,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.radiant_smart.api import (
    FAMILY_LIST_URL,
    TOKEN_URL,
)
from custom_components.radiant_smart.const import (
    CONF_COMPANY,
    CONF_HOME,
    CONF_TOKEN_DATA,
    DOMAIN,
)

VALID_USER_INPUT = {
    # Must be one of the brands listed in the brand selector
    CONF_COMPANY: "e4159dd6c88949ecab6feffb1c47efc1",
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
}


async def start_user_flow(hass):
    """Start the config flow and return the flow result."""
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


class TestUserStep:
    async def test_form_is_shown(self, hass) -> None:
        result = await start_user_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    async def test_invalid_credentials(self, hass, aioclient_mock) -> None:
        from unittest import mock as unittest_mock

        import aiohttp

        request_info = unittest_mock.Mock(real_url=TOKEN_URL)
        aioclient_mock.post(
            TOKEN_URL,
            exc=aiohttp.ClientResponseError(
                request_info=request_info, history=(), status=401
            ),
        )

        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(VALID_USER_INPUT)
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_auth"}

    async def test_cannot_connect(self, hass, aioclient_mock) -> None:
        import aiohttp

        aioclient_mock.post(TOKEN_URL, exc=aiohttp.ClientError("network down"))

        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(VALID_USER_INPUT)
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_no_homes(self, hass, aioclient_mock) -> None:
        aioclient_mock.post(TOKEN_URL, json=LOGIN_RESPONSE)
        aioclient_mock.post(FAMILY_LIST_URL, json={"status": 0, "data": []})

        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(VALID_USER_INPUT)
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "no_homes"}


class TestHomeStep:
    async def test_successful_flow_creates_entry(
        self, hass, mock_topband_cloud, patch_paho
    ) -> None:
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(VALID_USER_INPUT)
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "home"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOME: "home-2"}
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Radiant Smart (Chalet)"
        assert result["data"][CONF_HOME] == "home-2"
        assert result["data"][CONF_TOKEN_DATA] == LOGIN_RESPONSE["data"]

        # The created entry is set up and loaded
        await hass.async_block_till_done()
        assert result["result"].state is ConfigEntryState.LOADED

    async def test_entry_title_uses_home_name(
        self, hass, mock_topband_cloud, patch_paho
    ) -> None:
        """The entry is recognizable by its home name."""
        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(VALID_USER_INPUT)
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOME: "home-1"}
        )

        assert result["title"] == "Radiant Smart (Casa)"

    async def test_second_home_aborts_when_already_configured(
        self, hass, aioclient_mock
    ) -> None:
        """The same home cannot be added twice."""
        aioclient_mock.post(TOKEN_URL, json=LOGIN_RESPONSE)
        aioclient_mock.post(FAMILY_LIST_URL, json=FAMILY_LIST_RESPONSE)

        # An entry for home-1 already exists
        existing = MockConfigEntry(
            domain=DOMAIN,
            data=dict(ENTRY_DATA),
            version=1,
            unique_id="home-1",
        )
        existing.add_to_hass(hass)

        result = await start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(VALID_USER_INPUT)
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOME: "home-1"}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestReauth:
    async def test_reauth_flow_updates_credentials_and_reloads(
        self, hass, setup_entry
    ) -> None:
        """A reauth flow started by the integration updates the stored
        credentials and reloads the entry."""
        entry = setup_entry
        assert entry.state is ConfigEntryState.LOADED
        api_before = entry.runtime_data

        entry.async_start_reauth(hass)
        await hass.async_block_till_done()

        flows = list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
        assert flows
        flow = flows[0]
        assert flow["context"]["entry_id"] == entry.entry_id
        assert flow["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            user_input={CONF_EMAIL: "new@example.com", CONF_PASSWORD: "new-secret"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"

        # The entry was reloaded with the new credentials and fresh tokens;
        # company and home selection survive the update.
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not api_before
        assert entry.data[CONF_EMAIL] == "new@example.com"
        assert entry.data[CONF_PASSWORD] == "new-secret"
        assert entry.data[CONF_TOKEN_DATA] == LOGIN_RESPONSE["data"]
        assert entry.data[CONF_HOME] == "home-1"
        assert entry.data[CONF_COMPANY] == "company"

    async def test_reauth_with_bad_credentials_shows_error(
        self, hass, aioclient_mock
    ) -> None:
        """Wrong credentials during reauth keep the form open with an error."""
        from unittest import mock as unittest_mock

        import aiohttp

        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(ENTRY_DATA), version=1, unique_id="home-1"
        )
        entry.add_to_hass(hass)
        entry.async_start_reauth(hass)
        await hass.async_block_till_done()
        flows = list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
        assert flows

        request_info = unittest_mock.Mock(real_url=TOKEN_URL)
        aioclient_mock.post(
            TOKEN_URL,
            exc=aiohttp.ClientResponseError(
                request_info=request_info, history=(), status=401
            ),
        )

        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"],
            user_input={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "wrong"},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "invalid_auth"}
