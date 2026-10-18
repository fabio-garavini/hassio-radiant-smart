"""Topband cloud REST API client."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from yarl import URL

from .const import (
    CONF_COMPANY,
    CONF_HOME,
    CONF_TOKEN_DATA,
    DEVICE_API_BASE_URL,
    REQUEST_TIMEOUT,
    USER_API_BASE_URL,
)
from .datapoints import build_entity_configs
from .models import SmartDevice
from .mqtt import TopbandMqttClient, TopbandMqttReauthRequired

if TYPE_CHECKING:
    from . import TopbandConfigEntry

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = str(URL(USER_API_BASE_URL) / "tsmart-user-api/appLogin/v2/login")
FAMILY_LIST_URL = str(URL(USER_API_BASE_URL) / "tsmart-user-api/family/list")
DEVICE_LIST_URL = str(
    URL(DEVICE_API_BASE_URL) / "tsmart-device-api/family/v2/device/list"
)


class TopbandApiClientError(Exception):
    """General Topband cloud API error."""


class TopbandAuthenticationError(TopbandApiClientError):
    """Raised when authentication fails or the session expires."""


class TopbandCloudApi:
    """Client for the Topband cloud REST API and MQTT broker."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        config: Mapping[str, Any],
        entry: TopbandConfigEntry | None = None,
    ) -> None:
        """Initialize the client.

        `config` holds the credentials and home selection (the config entry
        data or an equivalent dict from the config flow); `entry` is passed
        when the client belongs to a config entry so fresh tokens can be
        persisted and a reauth flow can be started when needed.
        """
        self._hass = hass
        self._session = session
        self._email = config[CONF_EMAIL]
        self._password = config[CONF_PASSWORD]
        self._company_id = config[CONF_COMPANY]
        self._home_id = config.get(CONF_HOME)
        # Never mutate the config entry data in place; persist through
        # async_update_entry instead (see _async_persist_tokens).
        self._token_data: dict[str, Any] = dict(config.get(CONF_TOKEN_DATA) or {})
        self._entry = entry
        self._reauth_started = False
        self.devices: dict[str, SmartDevice] = {}
        self.mqtt = TopbandMqttClient(
            hass,
            token_provider=lambda: self._token_data.get("token"),
            reauth_callback=self.async_reauthenticate,
            device_lookup=self.get_device_by_mac,
        )

    @property
    def token_data(self) -> dict[str, Any]:
        """Return the current token data."""
        return self._token_data

    def get_device_by_mac(self, mac: str) -> SmartDevice | None:
        """Return a device by MAC address (case insensitive)."""
        return self.devices.get(mac.upper())

    async def async_authenticate(self) -> dict[str, Any]:
        """Authenticate with the Topband cloud API.

        Raises TopbandAuthenticationError for rejected credentials and
        TopbandApiClientError for connection problems, so setup can
        distinguish a reauth prompt from a retry.
        """
        await self._async_login()
        return self._token_data

    async def async_reauthenticate(self) -> None:
        """Re-authenticate to obtain a fresh token (used by MQTT reconnect).

        Raises TopbandMqttReauthRequired when the stored credentials are no
        longer valid, so the MQTT client stops retrying and the user is asked
        to re-authenticate instead.
        """
        try:
            await self._async_login()
        except TopbandAuthenticationError as err:
            _LOGGER.error("Re-authentication with the Topband cloud failed: %s", err)
            self._start_reauth_flow()
            raise TopbandMqttReauthRequired(str(err)) from err
        self._reauth_started = False

    async def async_get_family_list(self) -> list[dict[str, Any]]:
        """Get the list of homes (families) from the cloud API."""
        response = await self._async_request(
            "post", FAMILY_LIST_URL, data={}, is_login_required=True
        )
        if not isinstance(response, list):
            raise TopbandApiClientError("Unexpected response fetching family list")
        return response

    async def async_get_devices(self) -> dict[str, SmartDevice]:
        """Get all devices in the configured home."""
        rows = await self._async_request(
            "post", DEVICE_LIST_URL, data={"familyId": self._home_id}
        )
        if not isinstance(rows, dict):
            raise TopbandApiClientError("Unexpected response fetching device list")

        devices: dict[str, SmartDevice] = {}
        for source in rows.get("rows") or []:
            if not isinstance(source, dict):
                _LOGGER.warning("Skipping malformed device entry: %s", source)
                continue
            device = SmartDevice.from_api_response(self, source)
            if device is None:
                continue
            build_entity_configs(device)
            devices[device.mac_address.upper()] = device

        self.devices = devices
        return devices

    async def async_send_mqtt_command(
        self, device: SmartDevice, command: dict[str, Any]
    ) -> None:
        """Send a command to a device over MQTT."""
        await self.mqtt.async_send_command(device, command)

    async def async_subscribe_topics(self) -> None:
        """Subscribe to MQTT topics for all devices.

        The app subscribes the upload topic under the gateway product id
        and, per device, also under the device product id; echoes for all
        devices observed so far arrive on the gateway upload topic.
        """
        for device in self.devices.values():
            self.mqtt.subscribe(f"{device.product_id}/{device.gateway.uid}/business")
            self.mqtt.subscribe(
                f"{device.product_id}/{device.gateway.uid}/upload/point/data"
            )
            self.mqtt.subscribe(
                f"{device.gateway.product_id}/{device.gateway.uid}/upload/point/data"
            )

    async def async_shutdown(self) -> None:
        """Disconnect MQTT and release resources."""
        await self.mqtt.async_disconnect()

    async def _async_login(self) -> None:
        """Log in with email and password and store the fresh tokens."""
        response = await self._async_request(
            "post",
            TOKEN_URL,
            data={
                "userName": self._email,
                # The API expects an MD5 hash of the password
                "password": hashlib.md5(self._password.encode()).hexdigest(),
                "companyId": self._company_id,
            },
            is_login_required=False,
        )
        if not isinstance(response, dict):
            raise TopbandApiClientError("Unexpected login response")
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise TopbandAuthenticationError("Login response did not include a token")
        self._token_data = {
            "token": token,
            "refresh_token": response.get("refresh_token"),
        }
        self._async_persist_tokens()
        _LOGGER.debug("Login successful")

    def _async_persist_tokens(self) -> None:
        """Persist fresh tokens to the config entry.

        The cloud may rotate refresh tokens on every login; storing them
        keeps the entry usable across restarts without a fresh login.
        Runs on the event loop only.
        """
        if self._entry is None:
            return
        entry_data = self._entry.data
        if self._token_data == entry_data.get(CONF_TOKEN_DATA):
            return
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**entry_data, CONF_TOKEN_DATA: dict(self._token_data)},
        )

    def _start_reauth_flow(self) -> None:
        """Start the reauth flow for the config entry, at most once."""
        if self._entry is None or self._reauth_started:
            return
        self._reauth_started = True
        self._entry.async_start_reauth(self._hass)

    async def _async_request(
        self,
        method: str,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        is_login_required: bool = True,
        _retried_auth: bool = False,
    ) -> Any:
        """Perform a request against the Topband cloud API."""
        request_headers: dict[str, str] = dict(headers or {})
        if is_login_required:
            token = self._token_data.get("token")
            if token:
                request_headers["authorization"] = token

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                func = getattr(self._session, method)
                response = await func(
                    url,
                    headers=request_headers,
                    raise_for_status=True,
                    json=data if data is not None else {},
                    ssl=True,
                )
                payload = await response.json()
        except TimeoutError as err:
            _LOGGER.error("Timeout requesting %s", url)
            raise TopbandApiClientError(f"Timeout requesting {url}") from err
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                if is_login_required and not _retried_auth:
                    # Token may have expired: re-authenticate and retry once
                    _LOGGER.debug("Session expired for %s, re-authenticating", url)
                    try:
                        await self._async_login()
                    except TopbandApiClientError as auth_err:
                        raise TopbandAuthenticationError(
                            f"Re-authentication failed: {auth_err}"
                        ) from auth_err
                    return await self._async_request(
                        method,
                        url,
                        data=data,
                        headers=headers,
                        is_login_required=is_login_required,
                        _retried_auth=True,
                    )
                raise TopbandAuthenticationError(
                    "Unauthorized access - check credentials"
                ) from err
            _LOGGER.error(
                "Client response error (%d) for %s: %s",
                err.status,
                url,
                err.message,
            )
            raise TopbandApiClientError(f"Request to {url} failed") from err
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.error("Error requesting %s: %s", url, err)
            raise TopbandApiClientError(f"Error requesting {url}") from err

        return self._check_response(url, payload)

    def _check_response(self, url: str, payload: Any) -> Any:
        """Validate a Topband API response and unwrap the data field."""
        if not isinstance(payload, dict):
            return payload

        if payload.get("status") != 0:
            _LOGGER.error("API error for %s: %s", url, payload)
            raise TopbandApiClientError(
                f"API error: {payload.get('message', 'Unknown error')}"
            )
        return payload.get("data")
