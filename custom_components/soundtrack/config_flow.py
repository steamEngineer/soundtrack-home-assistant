"""Config flow for Soundtrack.

The password is sent once to loginUser and is not stored. The entry keeps
the access token and rotating refresh token.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SoundtrackAuthError, SoundtrackClient, SoundtrackConnectionError, SoundtrackError, async_login
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)

_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)


def _entry_data(email: str, tokens) -> dict[str, str]:
    return {
        CONF_EMAIL: email,
        CONF_ACCESS_TOKEN: tokens.access_token,
        CONF_REFRESH_TOKEN: tokens.refresh_token,
        CONF_EXPIRES_AT: tokens.expires_at,
        CONF_USER_ID: tokens.user_id,
    }


class SoundtrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Soundtrack config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Ask for the Soundtrack email and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                tokens, title = await self._async_sign_in(email, user_input[CONF_PASSWORD])
            except SoundtrackAuthError:
                _LOGGER.debug("Soundtrack rejected a sign-in for %s", email)
                errors["base"] = "invalid_auth"
            except SoundtrackConnectionError:
                errors["base"] = "cannot_connect"
            except SoundtrackError:
                _LOGGER.exception("Unexpected Soundtrack sign-in error")
                errors["base"] = "unknown"
            else:
                tokens.user_id = tokens.user_id or email.lower()
                await self.async_set_unique_id(tokens.user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=_entry_data(email, tokens))
        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Start the sign-in again after Soundtrack rejects the refresh token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Ask for the password of the account that was already set up."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        email = entry.data[CONF_EMAIL]
        if user_input is not None:
            try:
                tokens, _title = await self._async_sign_in(email, user_input[CONF_PASSWORD])
            except SoundtrackAuthError:
                errors["base"] = "invalid_auth"
            except SoundtrackConnectionError:
                errors["base"] = "cannot_connect"
            except SoundtrackError:
                _LOGGER.exception("Unexpected Soundtrack reauth error")
                errors["base"] = "unknown"
            else:
                user_id = tokens.user_id or entry.unique_id or email.lower()
                tokens.user_id = user_id
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data=_entry_data(email, tokens),
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_REAUTH_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )

    async def _async_sign_in(self, email: str, password: str):
        session = async_get_clientsession(self.hass)
        tokens = await async_login(session, email, password)
        title = email
        try:
            identity = await SoundtrackClient(session, tokens).async_whoami()
        except SoundtrackAuthError:
            raise
        except SoundtrackError:
            _LOGGER.debug("Signed in, but could not read the Soundtrack profile for %s", email)
        else:
            if identity.id and not tokens.user_id:
                tokens.user_id = identity.id
            title = identity.name or email
        return tokens, title
