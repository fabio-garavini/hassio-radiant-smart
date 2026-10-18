"""Config flow for the Radiant Smart integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    TopbandApiClientError,
    TopbandAuthenticationError,
    TopbandCloudApi,
)
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
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.EMAIL,
                autocomplete="email",
            )
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
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

    async def _async_login(
        self, company_id: str, email: str, password: str
    ) -> dict[str, Any]:
        """Log in against the cloud API and return the fresh token data.

        Raises TopbandAuthenticationError for bad credentials and
        TopbandApiClientError for connection problems.
        """
        client = TopbandCloudApi(
            self.hass,
            async_get_clientsession(self.hass),
            {
                CONF_COMPANY: company_id,
                CONF_EMAIL: email,
                CONF_PASSWORD: password,
            },
        )
        return await client.async_authenticate()

    def _home_select_schema(self) -> vol.Schema:
        """Build the home selector schema from the fetched homes."""
        return vol.Schema(
            {
                vol.Required(CONF_HOME): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=home["id"], label=home["familyName"])
                            for home in self._homes
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

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
                self._token_data = await self._async_login(company_id, email, password)
                client = TopbandCloudApi(
                    self.hass,
                    async_get_clientsession(self.hass),
                    {
                        CONF_COMPANY: company_id,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_TOKEN_DATA: self._token_data,
                    },
                )
                self._homes = await client.async_get_family_list()
            except TopbandAuthenticationError:
                errors["base"] = "invalid_auth"
            except TopbandApiClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"
            else:
                if not self._homes:
                    errors["base"] = "no_homes"
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
        if user_input is not None:
            home_id = str(user_input[CONF_HOME])
            # One entry per home, prevents adding the same home twice
            await self.async_set_unique_id(home_id)
            self._abort_if_unique_id_configured()
            home_name = next(
                (
                    home["familyName"]
                    for home in self._homes
                    if str(home.get("id")) == home_id
                ),
                "Radiant Smart",
            )
            return self.async_create_entry(
                title=f"Radiant Smart ({home_name})",
                data={
                    CONF_COMPANY: self._company_id,
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_HOME: home_id,
                    CONF_TOKEN_DATA: self._token_data,
                },
            )

        return self.async_show_form(
            step_id="home",
            data_schema=self._home_select_schema(),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a reauthentication flow initiation."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the reauthentication form."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        # Bind the flow to the home of the entry being reauthenticated
        await self.async_set_unique_id(
            str(entry.data[CONF_HOME]), raise_on_progress=False
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL, default=entry.data[CONF_EMAIL]): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        if user_input is not None:
            try:
                self._token_data = await self._async_login(
                    entry.data[CONF_COMPANY],
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                )
            except TopbandAuthenticationError:
                errors["base"] = "invalid_auth"
            except TopbandApiClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                # data_updates merges into the existing entry data, keeping
                # the company, home selection and unique id intact.
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_TOKEN_DATA: self._token_data,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )
