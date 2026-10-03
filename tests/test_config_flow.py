"""Config flow against a Home Assistant 2026.9 runtime and the local Soundtrack API."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState

from custom_components.soundtrack.const import CONF_ACCESS_TOKEN, CONF_EMAIL, DOMAIN
from dev.mock_soundtrack import USER_ID
from tests.hass_fixture import soundtrack_entry


async def test_user_flow_creates_entry(hass, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "ada@example.com", "password": "soundtrack"},
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Ada"
    assert result["result"].unique_id == USER_ID
    assert result["data"][CONF_EMAIL] == "ada@example.com"
    assert "password" not in result["data"]
    assert result["data"][CONF_ACCESS_TOKEN] == "access-1"


async def test_user_flow_rejects_a_bad_password(hass, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "ada@example.com", "password": "wrong-password"},
    )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


async def test_user_flow_reports_a_connection_error(hass, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("SOUNDTRACK_API_URL", "http://127.0.0.1:1/")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "ada@example.com", "password": "soundtrack"},
    )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


async def test_user_flow_aborts_when_the_account_exists(hass, mock_api) -> None:
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    created = await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {CONF_EMAIL: "ada@example.com", "password": "soundtrack"},
    )
    assert created["type"] == "create_entry"

    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        second["flow_id"],
        {CONF_EMAIL: "ada@example.com", "password": "soundtrack"},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reauth_replaces_the_session(hass, mock_api) -> None:
    entry = soundtrack_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id, "unique_id": entry.unique_id},
    )
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "soundtrack"})
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_ACCESS_TOKEN] == "access-1"
    assert "password" not in entry.data
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_rejects_a_different_user(hass, mock_api) -> None:
    entry = soundtrack_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id, "unique_id": entry.unique_id},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "other-account"})
    assert result["type"] == "abort"
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_ACCESS_TOKEN] == "access-1"
