"""A slim Home Assistant 2026.9 fixture.

Core's tests/common.py is not shipped in the wheel, and
pytest-homeassistant-custom-component still pins Home Assistant 2025.1.
This follows async_test_home_assistant from the 2026.9.4 tag: auth, config
entries, registries that do not touch disk, and the loader path that
discovers custom_components.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import suppress
from typing import Any
import os
from unittest.mock import patch

import aiohttp
from aiohttp.test_utils import unused_port

from homeassistant import auth, config_entries, loader
from homeassistant.helpers import frame as frame_helper
from homeassistant.auth import auth_store
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.setup import async_setup_component
from homeassistant.helpers import (
    area_registry as ar,
    category_registry as cr,
    condition,
    device_registry as dr,
    entity,
    entity_registry as er,
    floor_registry as fr,
    issue_registry as ir,
    label_registry as lr,
    restore_state as rs,
    storage,
    translation,
    trigger,
)
from homeassistant.util import dt as dt_util, ulid as ulid_util
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.soundtrack.const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from dev.mock_soundtrack import EXPIRES, USER_ID


class _TestResolver(aiohttp.ThreadedResolver):
    """DNS resolver that does not start Zeroconf. The lab instance still does."""

    async def real_close(self) -> None:
        await self.close()


class _StoreWithoutWriteLoad(storage.Store):
    """Registry store that drops writes. Tests do not need a .storage directory."""

    async def async_save(self, *args: Any, **kwargs: Any) -> None:
        return None

    @callback
    def async_save_delay(self, *args: Any, **kwargs: Any) -> None:
        return None


class MockConfigEntry(config_entries.ConfigEntry):
    """Config entry with the defaults Home Assistant 2026.9 requires."""

    def __init__(
        self,
        *,
        domain: str = DOMAIN,
        data: Mapping[str, Any] | None = None,
        title: str = "Ada",
        unique_id: str | None = USER_ID,
        source: str = config_entries.SOURCE_USER,
        version: int = 1,
        minor_version: int = 1,
    ) -> None:
        super().__init__(
            data=data or {},
            discovery_keys={},
            domain=domain,
            entry_id=ulid_util.ulid_now(),
            minor_version=minor_version,
            options={},
            source=source,
            subentries_data=(),
            title=title,
            unique_id=unique_id,
            version=version,
        )

    def add_to_hass(self, hass: HomeAssistant) -> None:
        hass.config_entries._entries[self.entry_id] = self


def soundtrack_entry() -> MockConfigEntry:
    """A saved Soundtrack session for the stand-in account."""
    return MockConfigEntry(
        data={
            CONF_EMAIL: "ada@example.com",
            CONF_ACCESS_TOKEN: "access-1",
            CONF_REFRESH_TOKEN: "refresh-1",
            CONF_EXPIRES_AT: EXPIRES,
            CONF_USER_ID: USER_ID,
        }
    )


async def async_test_home_assistant(config_dir: str) -> AsyncGenerator[HomeAssistant]:
    """Yield a running-enough Home Assistant for config entries and entities."""
    hass = HomeAssistant(config_dir)
    frame_helper.async_setup(hass)
    store = auth_store.AuthStore(hass)
    hass.auth = auth.AuthManager(hass, store, {}, {})
    if store._users is None:
        store._set_defaults()

    orig_tz = dt_util.get_default_time_zone()
    hass.config.location_name = "test home"
    hass.config.latitude = 32.87336
    hass.config.longitude = -117.22743
    hass.config.elevation = 0
    await hass.config.async_set_time_zone("UTC")
    hass.config.units = METRIC_SYSTEM
    hass.config.skip_pip = True
    hass.config.skip_pip_packages = []
    media = os.path.join(config_dir, "media")
    os.makedirs(media, exist_ok=True)
    hass.config.media_dirs = {"local": media}

    hass_config = {"http": {"server_port": unused_port()}}
    hass.config_entries = config_entries.ConfigEntries(hass, hass_config)
    hass.config_entries._initialized.set()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hass.config_entries._async_shutdown)

    entity.async_setup(hass)
    loader.async_setup(hass)
    await condition.async_setup(hass)
    await trigger.async_setup(hass)
    hass.data[translation.TRANSLATION_FLATTEN_CACHE] = translation._TranslationCache(hass)
    dr.async_setup(hass)

    with (
        patch.object(_StoreWithoutWriteLoad, "async_load", return_value=None),
        patch("homeassistant.helpers.area_registry.AreaRegistryStore", _StoreWithoutWriteLoad),
        patch("homeassistant.helpers.device_registry.DeviceRegistryStore", _StoreWithoutWriteLoad),
        patch("homeassistant.helpers.entity_registry.EntityRegistryStore", _StoreWithoutWriteLoad),
        patch("homeassistant.helpers.storage.Store", _StoreWithoutWriteLoad),
        patch("homeassistant.helpers.issue_registry.IssueRegistryStore", _StoreWithoutWriteLoad),
        patch("homeassistant.helpers.restore_state.RestoreStateData.async_setup_dump", return_value=None),
        patch("homeassistant.helpers.restore_state.start.async_at_start"),
        patch(
            "homeassistant.helpers.aiohttp_client._async_make_resolver",
            lambda hass: _TestResolver(),
        ),
    ):
        await ar.async_load(hass)
        await cr.async_load(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        await fr.async_load(hass)
        await ir.async_load(hass)
        await lr.async_load(hass)
        await rs.async_load(hass)
        hass.set_state(CoreState.running)
        # aiohttp's HA resolver asks zeroconf for adapters, which needs network.
        assert await async_setup_component(hass, "network", {})
        try:
            yield hass
        finally:
            dt_util.set_default_time_zone(orig_tz)
            with suppress(Exception):
                await hass.async_stop(force=True)
