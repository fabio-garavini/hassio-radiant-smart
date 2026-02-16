"""Config flow for the Radiant Smart integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    CONN_CLASS_CLOUD_PUSH,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import TopbandCloudApi
from .const import CONF_COMPANY, CONF_HOME, CONF_TOKEN_DATA, DOMAIN, MANUFACTURERS

_LOGGER = logging.getLogger(__name__)

LOGIN_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COMPANY): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=k, label=v["name"]) for k, v in MANUFACTURERS.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Radiant Smart."""

    VERSION = 0
    MINOR_VERSION = 1

    CONNECTION_CLASS = CONN_CLASS_CLOUD_PUSH

    _company_id: str
    _email: str
    _password: str
    _token_data: dict[str, Any]
    _homes: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the Radiant Smart login step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _LOGGER.info(user_input)
                client = TopbandCloudApi(
                    async_create_clientsession(self.hass),
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_COMPANY],
                )

                self._token_data = await client.authenticate()

                self._homes = await client.async_get_family_list()

                self._company_id = user_input[CONF_COMPANY]
                self._email = user_input[CONF_EMAIL]
                self._password = user_input[CONF_PASSWORD]

                return await self.async_step_home()
            except CannotConnect:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=LOGIN_DATA_SCHEMA, errors=errors
        )

    async def async_step_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the home selection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="Radiant Smart", data={ CONF_COMPANY: self._company_id, CONF_EMAIL: self._email, CONF_PASSWORD: self._password, CONF_TOKEN_DATA: self._token_data, **user_input, })

        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema({
                vol.Required(CONF_HOME): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=f["id"], label=f["familyName"]) for f in self._homes
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
