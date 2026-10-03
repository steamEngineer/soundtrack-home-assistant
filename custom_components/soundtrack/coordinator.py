"""Data update coordinator for Soundtrack zones."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Snapshot, SoundtrackAuthError, SoundtrackClient, SoundtrackError
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class SoundtrackCoordinator(DataUpdateCoordinator[Snapshot]):
    """Poll zones and what's playing.

    A rejected refresh token raises ConfigEntryAuthFailed so Home Assistant
    opens the reauth flow instead of retrying the password on its own.
    """

    def __init__(self, hass: HomeAssistant, client: SoundtrackClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.client = client

    async def _async_update_data(self) -> Snapshot:
        try:
            return await self.client.async_snapshot()
        except SoundtrackAuthError as err:
            raise ConfigEntryAuthFailed(
                "Soundtrack ended this session. Sign in again to keep controlling sound zones."
            ) from err
        except SoundtrackError as err:
            raise UpdateFailed(str(err)) from err


type SoundtrackConfigEntry = ConfigEntry[SoundtrackCoordinator]
