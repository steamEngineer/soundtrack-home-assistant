"""Soundtrack integration. One config entry is one Soundtrack user."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SoundtrackClient, Tokens
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from .coordinator import SoundtrackConfigEntry, SoundtrackCoordinator

_LOGGER = logging.getLogger(__name__)

_PLATFORMS = [Platform.MEDIA_PLAYER]


def _tokens(entry: SoundtrackConfigEntry) -> Tokens:
    data = entry.data
    return Tokens(
        access_token=data[CONF_ACCESS_TOKEN],
        refresh_token=data[CONF_REFRESH_TOKEN],
        expires_at=data.get(CONF_EXPIRES_AT) or "",
        user_id=data.get(CONF_USER_ID) or "",
        email=data.get(CONF_EMAIL) or "",
    )


async def _persist(hass: HomeAssistant, entry: SoundtrackConfigEntry, tokens: Tokens) -> None:
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_ACCESS_TOKEN: tokens.access_token,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_EXPIRES_AT: tokens.expires_at,
            CONF_USER_ID: tokens.user_id or entry.data.get(CONF_USER_ID, ""),
        },
    )


async def async_setup_entry(hass: HomeAssistant, entry: SoundtrackConfigEntry) -> bool:
    """Set up Soundtrack from a config entry."""
    session = async_get_clientsession(hass)

    async def _on_tokens(tokens: Tokens) -> None:
        await _persist(hass, entry, tokens)

    client = SoundtrackClient(session, _tokens(entry), on_tokens=_on_tokens)
    coordinator = SoundtrackCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Soundtrack",
        model="Account",
        name=entry.title,
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    _LOGGER.debug("Soundtrack ready with %s sound zones", len(coordinator.data.zones))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SoundtrackConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: SoundtrackConfigEntry) -> bool:
    """Keep a stored Soundtrack entry working after an integration update.

    1.1 is the first release and already stores the session fields 1.2 reads.
    The minor bump is what makes Home Assistant run this on upgrade instead of
    refusing the entry when a later change needs a real migration.
    """
    if entry.version != 1:
        return False
    hass.config_entries.async_update_entry(entry, minor_version=2)
    return True
