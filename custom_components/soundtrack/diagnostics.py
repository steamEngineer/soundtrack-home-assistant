"""Diagnostics for Soundtrack. Tokens and the account email stay out of the download."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_REFRESH_TOKEN
from .coordinator import SoundtrackConfigEntry

_REDACT = {CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_EMAIL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: SoundtrackConfigEntry,
) -> dict[str, Any]:
    """Return zone state without the saved session."""
    snapshot = entry.runtime_data.data
    zones = []
    if snapshot is not None:
        zones = [
            {
                "id": zone.id,
                "name": zone.name,
                "location": zone.location_name,
                "account": zone.account_name,
                "state": zone.playback_state,
                "volume": zone.volume,
                "source": zone.source_name,
                "track": zone.track.title if zone.track else None,
                "online": zone.online,
                "paired": zone.paired,
            }
            for zone in snapshot.zones.values()
        ]
    return {
        "entry": async_redact_data(entry.as_dict(), _REDACT),
        "zones": zones,
    }
