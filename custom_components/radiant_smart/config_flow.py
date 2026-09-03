"""Config flow for the Radiant Smart integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import TopbandAuthenticationError, TopbandApiClientError, TopbandCloudApi
from .const import CONF_COMPANY, CONF_HOME, CONF_TOKEN_DATA, DOMAIN, MANUFACTURERS

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COMPANY): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=key, label=name)
                    for key, name in MANUFACTURERS.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class RadiantSmartConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Radiant Smart."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._company_id: str | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._token_data: dict[str, Any] | None = None
        self._homes: list[dict[str, Any]] = []

    async def _async_authenticate(
        self, company_id: str, email: str, password: str
    ) -> list[dict[str, Any]]:
        """Authenticate and return the list of homes."""
        client = TopbandCloudApi(
            self.hass,
            async_get_clientsession(self.hass),
            email,
            password,
            company_id,
        )
        self._token_data = await client.async_authenticate()
        return await client.async_get_family_list()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the login step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            company_id = user_input[CONF_COMPANY]
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            try:
                self._homes = await self._async_authenticate(
                    company_id, email, password
                )
            except TopbandAuthenticationError:
                errors["base"] = "invalid_auth"
            except TopbandApiClientError:
                errors["base"] = "cannot_connect"
            else:
                self._company_id = company_id
                self._email = email
                self._password = password
                return await self.async_step_home()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the home selection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # One entry per home, prevents adding the same home twice
            await self.async_set_unique_id(str(user_input[CONF_HOME]))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Radiant Smart",
                data={
                    CONF_COMPANY: self._company_id,
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_HOME: user_input[CONF_HOME],
                    CONF_TOKEN_DATA: self._token_data,
                },
            )

        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOME): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=home["id"], label=home["familyName"]
                                )
                                for home in self._homes
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        """Handle a reauthentication flow initiation."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the reauthentication form."""
        errors: dict[str, str] = {}
        entry_id = self.context.get("entry_id")
        entry = (
            self.hass.config_entries.async_get_entry(entry_id)
            if entry_id
            else None
        )
        assert entry is not None
        # Bind the flow to the home of the entry being reauthenticated
        await self.async_set_unique_id(
            str(entry.data[CONF_HOME]), raise_on_progress=False
        )

        if user_input is not None:
            try:
                await self._async_authenticate(
                    entry.data[CONF_COMPANY],
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                )
            except TopbandAuthenticationError:
                errors["base"] = "invalid_auth"
            except TopbandApiClientError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_TOKEN_DATA: self._token_data,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )
