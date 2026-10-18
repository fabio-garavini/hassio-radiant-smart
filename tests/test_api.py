"""Tests for the Topband cloud REST API client."""

import pytest
from conftest import FakeEntry, FakeHass

from custom_components.radiant_smart.api import (
    TopbandApiClientError,
    TopbandAuthenticationError,
    TopbandCloudApi,
)
from custom_components.radiant_smart.const import CONF_TOKEN_DATA
from custom_components.radiant_smart.mqtt import TopbandMqttReauthRequired

CONFIG = {"email": "user@example.com", "password": "secret", "company_id": "company"}
LOGIN_RESPONSE = {"token": "fresh-token", "refresh_token": "fresh-refresh"}


def make_api(entry=None, config=None):
    hass = FakeHass()
    api = TopbandCloudApi(hass, None, config or dict(CONFIG), entry)
    return api, hass


def patch_request(monkeypatch, api, responses):
    """Patch _async_request to pop canned responses (or raise callables)."""

    async def fake_request(
        self,
        method,
        url,
        data=None,
        headers=None,
        is_login_required=True,
        _retried_auth=False,
    ):
        result = responses.pop(0)
        if callable(result):
            result = result()
        return result

    monkeypatch.setattr(TopbandCloudApi, "_async_request", fake_request)


class TestLogin:
    async def test_successful_login_stores_tokens(self, monkeypatch) -> None:
        api, hass = make_api()
        patch_request(monkeypatch, api, [LOGIN_RESPONSE])

        await api.async_authenticate()

        assert api.token_data == LOGIN_RESPONSE
        # No config entry: nothing to persist
        assert not hass.config_entries.updates

    async def test_login_response_without_token_rejected(self, monkeypatch) -> None:
        api, _ = make_api()
        patch_request(monkeypatch, api, [{"refresh_token": "x"}])

        with pytest.raises(TopbandAuthenticationError, match="did not include"):
            await api.async_authenticate()

    async def test_login_response_not_a_dict_rejected(self, monkeypatch) -> None:
        api, _ = make_api()
        patch_request(monkeypatch, api, [["unexpected"]])

        # A malformed login response is a client error, not an auth
        # failure: setup must retry instead of prompting for reauth.
        with pytest.raises(TopbandApiClientError, match="Unexpected login"):
            await api.async_authenticate()


class TestTokenPersistence:
    async def test_fresh_tokens_are_persisted(self, monkeypatch) -> None:
        entry = FakeEntry({**CONFIG, CONF_TOKEN_DATA: {"token": "stale"}})
        api, hass = make_api(entry)
        patch_request(monkeypatch, api, [LOGIN_RESPONSE])

        await api.async_authenticate()

        assert hass.config_entries.updates
        assert entry.data[CONF_TOKEN_DATA] == LOGIN_RESPONSE

    async def test_unchanged_tokens_are_not_persisted(self, monkeypatch) -> None:
        entry = FakeEntry({**CONFIG, CONF_TOKEN_DATA: LOGIN_RESPONSE})
        api, hass = make_api(entry)
        patch_request(monkeypatch, api, [LOGIN_RESPONSE])

        await api.async_authenticate()

        assert not hass.config_entries.updates


class TestReauthenticate:
    async def test_success_does_not_start_reauth_flow(self, monkeypatch) -> None:
        entry = FakeEntry(dict(CONFIG))
        api, _ = make_api(entry)
        patch_request(monkeypatch, api, [LOGIN_RESPONSE])

        await api.async_reauthenticate()

        assert entry.async_start_reauth_calls == 0

    async def test_bad_credentials_start_reauth_flow(self, monkeypatch) -> None:
        entry = FakeEntry(dict(CONFIG))
        api, _ = make_api(entry)

        async def failing_login():
            raise TopbandAuthenticationError("401")

        monkeypatch.setattr(api, "_async_login", failing_login)

        with pytest.raises(TopbandMqttReauthRequired):
            await api.async_reauthenticate()

        assert entry.async_start_reauth_calls == 1

    async def test_reauth_flow_started_only_once(self, monkeypatch) -> None:
        entry = FakeEntry(dict(CONFIG))
        api, _ = make_api(entry)

        async def failing_login():
            raise TopbandAuthenticationError("401")

        monkeypatch.setattr(api, "_async_login", failing_login)

        for _ in range(3):
            with pytest.raises(TopbandMqttReauthRequired):
                await api.async_reauthenticate()

        # Multiple connection drops must not spam reauth flow starts
        assert entry.async_start_reauth_calls == 1


class TestGetDevices:
    ROWS = {
        "rows": [
            {
                "deviceType": 0,
                "deviceName": "Boiler",
                "id": "1",
                "productId": "p",
                "model": "TH311",
                "gateway": {"uid": "gw", "productId": "gp"},
                "extAddr": "AA:BB",
                "online": 1,
                "pointDataMap": {"A": {"pointIndex": 1, "pointType": 1, "value": 1}},
            },
            # Missing gateway: skipped with a warning
            {"deviceName": "Broken", "extAddr": "CC:DD"},
            # Not a dict: skipped
            "garbage",
        ]
    }

    async def test_devices_keyed_by_uppercase_mac(self, monkeypatch) -> None:
        api, _ = make_api()
        patch_request(monkeypatch, api, [self.ROWS])

        devices = await api.async_get_devices()

        assert list(devices) == ["AA:BB"]
        assert api.get_device_by_mac("aa:bb") is devices["AA:BB"]

    async def test_malformed_response_rejected(self, monkeypatch) -> None:
        api, _ = make_api()
        patch_request(monkeypatch, api, [["unexpected"]])

        with pytest.raises(TopbandApiClientError, match="Unexpected response"):
            await api.async_get_devices()

    async def test_malformed_family_list_rejected(self, monkeypatch) -> None:
        api, _ = make_api()
        patch_request(monkeypatch, api, [{"not": "a list"}])

        with pytest.raises(TopbandApiClientError, match="Unexpected response"):
            await api.async_get_family_list()
