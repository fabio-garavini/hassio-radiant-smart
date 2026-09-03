"""Topband cloud REST API client."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
from typing import Any, cast

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant

from .const import (
    DEVICE_API_BASE_URL,
    REQUEST_TIMEOUT,
    USER_API_BASE_URL,
)
from .datapoints import build_entity_configs
from .mqtt import TopbandMqttClient
from .models import SmartDevice

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = str(URL(USER_API_BASE_URL) / "tsmart-user-api/appLogin/v2/login")
REFRESH_TOKEN_URL = str(
    URL(USER_API_BASE_URL) / "tsmart-user-api/appLogin/v2/refreshAccessToken"
)
FAMILY_LIST_URL = str(URL(USER_API_BASE_URL) / "tsmart-user-api/family/list")
SELF_INFO_URL = str(
    URL(USER_API_BASE_URL) / "tsmart-user-api/account/v2/getSelfInfo"
)
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
        email: str,
        password: str,
        company_id: str,
        home_id: str | None = None,
        token_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self._session = session
        self._email = email
        self._password = password
        self._company_id = company_id
        self._home_id = home_id
        self._token_data = token_data or {}
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

        Raises TopbandAuthenticationError on failure.
        """
        try:
            await self._async_get_token()
        except TopbandApiClientError as err:
            raise TopbandAuthenticationError(
                f"Authentication failed: {err}"
            ) from err
        return self._token_data

    async def async_reauthenticate(self) -> None:
        """Re-authenticate to obtain a fresh token (used by MQTT reconnect)."""
        _LOGGER.debug("Re-authenticating with Topband cloud API")
        await self._async_get_token()

    async def async_get_family_list(self) -> list[dict[str, Any]]:
        """Get the list of homes (families) from the cloud API."""
        response = cast(
            list[dict[str, Any]],
            await self._async_request(
                "post", FAMILY_LIST_URL, data={}, is_login_required=True
            ),
        )
        return response

    async def async_get_self_info(self) -> dict[str, Any]:
        """Get user info from the cloud API."""
        return await self._async_request(
            "post", SELF_INFO_URL, is_login_required=True
        )

    async def async_get_devices(self) -> dict[str, SmartDevice]:
        """Get all devices in the configured home."""
        rows = await self._async_request(
            "post", DEVICE_LIST_URL, data={"familyId": self._home_id}
        )
        rows = rows.get("rows", [])

        devices: dict[str, SmartDevice] = {}
        for source in rows:
            device = SmartDevice.from_api_response(self, source)
            if not device.mac_address:
                _LOGGER.warning(
                    "Skipping device without MAC address: %s", device.name
                )
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
        """Subscribe to MQTT topics for all devices."""
        subscribed: set[str] = set()
        for device in self.devices.values():
            topics = (
                f"{device.product_id}/{device.gateway.uid}/business",
                f"{device.gateway.product_id}/{device.gateway.uid}/upload/point/data",
            )
            for topic in topics:
                if topic not in subscribed:
                    subscribed.add(topic)
                    self.mqtt.subscribe(topic)

    async def async_shutdown(self) -> None:
        """Disconnect MQTT and release resources."""
        await self.mqtt.async_disconnect()

    async def _async_get_token(self) -> None:
        """Obtain a new JWT token using email and password."""
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
        self._token_data["token"] = response.get("token")
        self._token_data["refresh_token"] = response.get("refresh_token")
        _LOGGER.debug("Login successful")

    async def _async_refresh_token(self) -> None:
        """Obtain a new JWT token using the refresh token."""
        response = await self._async_request(
            "get",
            REFRESH_TOKEN_URL,
            headers={"authorization": self._token_data.get("refresh_token", "")},
            is_login_required=False,
        )
        self._token_data["token"] = response.get("token")
        self._token_data["refresh_token"] = response.get("refresh_token")

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
                response.raise_for_status()
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
                        await self._async_get_token()
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
        except (aiohttp.ClientError, socket.gaierror) as err:
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
