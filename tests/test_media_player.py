"""Media player setup against Home Assistant 2026.9 and the local Soundtrack API."""

from __future__ import annotations

import asyncio

from dev.mock_soundtrack import ZONE_ID
from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.media_player import MediaClass, MediaPlayerState, SearchMediaQuery
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from tests.hass_fixture import soundtrack_entry

from custom_components.soundtrack.const import CONF_ACCESS_TOKEN, CONF_EMAIL, DOMAIN
from custom_components.soundtrack.diagnostics import async_get_config_entry_diagnostics


async def _async_setup(hass, mock_api):
    entry = soundtrack_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    entity_id = er.async_get(hass).async_get_entity_id("media_player", DOMAIN, ZONE_ID)
    assert entity_id is not None
    return entry, entity_id


async def test_zone_player_reports_what_is_playing(hass, mock_api) -> None:
    entry, entity_id = await _async_setup(hass, mock_api)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == MediaPlayerState.PLAYING
    assert state.attributes["media_title"] == "Nightshift"
    assert state.attributes["media_artist"] == "Commodores"
    assert state.attributes["volume_level"] == 0.5
    assert state.attributes["source"] == "Morning"
    assert state.attributes["media_position"] == 42
    assert "Front Bar" in (state.attributes.get("friendly_name") or entity_id)

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, ZONE_ID), entry.entry_id)
    assert device is not None
    assert device.via_device_id is not None
    hub = dr.async_get(hass).async_get(device.via_device_id)
    assert hub is not None
    assert hub.name == entry.title

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_ACCESS_TOKEN] == REDACTED
    assert diag["entry"]["data"][CONF_EMAIL] == REDACTED
    assert diag["zones"][0]["track"] == "Nightshift"
    assert "access-1" not in str(diag)


async def test_pause_volume_skip_and_playlist(hass, mock_api) -> None:
    _entry, entity_id = await _async_setup(hass, mock_api)

    await hass.services.async_call(
        "media_player",
        "media_pause",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED

    await hass.services.async_call(
        "media_player",
        "volume_set",
        {ATTR_ENTITY_ID: entity_id, "volume_level": 1},
        blocking=True,
    )
    assert hass.states.get(entity_id).attributes["volume_level"] == 1
    assert mock_api.volume == 16

    await hass.services.async_call(
        "media_player",
        "media_next_track",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert hass.states.get(entity_id).attributes["media_title"] == "Easy"

    await hass.services.async_call(
        DOMAIN,
        "play_playlist",
        {ATTR_ENTITY_ID: entity_id, "playlist_id": "playlist-jazz"},
        blocking=True,
    )
    assert hass.states.get(entity_id).attributes["source"] == "Jazz After Dark"
    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING


async def test_favorite_shows_up_as_a_source(hass, mock_api) -> None:
    _entry, entity_id = await _async_setup(hass, mock_api)
    sources = hass.states.get(entity_id).attributes["source_list"]
    assert "Jazz After Dark" not in sources

    await hass.services.async_call(
        DOMAIN,
        "favorite_playlist",
        {ATTR_ENTITY_ID: entity_id, "playlist_id": "playlist-jazz"},
        blocking=True,
    )
    sources = hass.states.get(entity_id).attributes["source_list"]
    assert "Jazz After Dark" in sources

    await hass.services.async_call(
        DOMAIN,
        "unfavorite_playlist",
        {ATTR_ENTITY_ID: entity_id, "playlist_id": "playlist-jazz"},
        blocking=True,
    )
    sources = hass.states.get(entity_id).attributes["source_list"]
    assert "Jazz After Dark" not in sources


async def test_browse_and_search(hass, mock_api) -> None:
    _entry, entity_id = await _async_setup(hass, mock_api)
    player = hass.data["media_player"].get_entity(entity_id)
    root = await player.async_browse_media()
    assert root.title == "Soundtrack"
    assert root.can_search is True
    assert [child.title for child in root.children] == ["Favorites", "Discover"]

    favorites = await player.async_browse_media("favorites", "favorites")
    assert [child.title for child in favorites.children] == ["Morning", "Evening"]

    discover = await player.async_browse_media("discover", "discover")
    assert discover.children[0].title == "Jazz"
    assert discover.children[0].thumbnail == "https://cdn.example/960/960/jazz"

    genre = await player.async_browse_media("genre", discover.children[0].media_content_id)
    assert [child.title for child in genre.children] == ["Jazz After Dark", "Lounge"]
    assert genre.children[0].can_play is True
    assert genre.children[1].can_expand is True

    found = await player.async_search_media(SearchMediaQuery(search_query="jazz"))
    assert [item.title for item in found.result] == ["Jazz After Dark"]

    empty = await player.async_search_media(
        SearchMediaQuery(search_query="jazz", media_filter_classes=[MediaClass.ALBUM])
    )
    assert list(empty.result) == []


async def test_command_refreshes_again_after_the_zone_catches_up(
    hass, mock_api, monkeypatch
) -> None:
    from custom_components.soundtrack import media_player as player_module

    monkeypatch.setattr(player_module, "_FOLLOW_UP_SECONDS", 0)
    entry, entity_id = await _async_setup(hass, mock_api)
    refreshes = 0
    refresh = entry.runtime_data.async_refresh

    async def _count():
        nonlocal refreshes
        refreshes += 1
        await refresh()

    entry.runtime_data.async_refresh = _count
    await hass.services.async_call(
        "media_player",
        "media_pause",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert refreshes >= 2


async def test_rejected_refresh_starts_reauth(hass, mock_api) -> None:
    entry, entity_id = await _async_setup(hass, mock_api)
    mock_api.reject_refresh = True
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


def _soundtrack_devices(hass, entry_id: str):
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)


async def test_unload_reload_and_upgrade_keep_the_same_entity(hass, mock_api) -> None:
    entry, entity_id = await _async_setup(hass, mock_api)
    assert entry.minor_version == 2
    assert entry.data[CONF_ACCESS_TOKEN] == "access-1"

    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, new_entity_id="media_player.front_bar")
    await hass.async_block_till_done()
    entity_id = "media_player.front_bar"
    device_id = registry.async_get(entity_id).device_id
    assert len(_soundtrack_devices(hass, entry.entry_id)) == 2

    await hass.services.async_call(
        DOMAIN,
        "play_playlist",
        {ATTR_ENTITY_ID: entity_id, "playlist_id": "playlist-jazz"},
        blocking=True,
    )
    assert hass.states.get(entity_id).attributes["source"] == "Jazz After Dark"

    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.data["media_player"].get_entity(entity_id) is None
    # The registry row stays, so a reload can claim the same entity id.
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert registry.async_get(entity_id).unique_id == ZONE_ID
    assert coordinator._shutdown_requested is True
    assert len(_soundtrack_devices(hass, entry.entry_id)) == 2

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING
    assert hass.states.get(entity_id).attributes["source"] == "Jazz After Dark"
    assert registry.async_get(entity_id).device_id == device_id
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    assert len(_soundtrack_devices(hass, entry.entry_id)) == 2

    await hass.services.async_call(
        "media_player",
        "media_pause",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED

    # An entry saved by version 1.1 is migrated on the next setup.
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, minor_version=1)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert entry.data[CONF_EMAIL] == "ada@example.com"
    assert entry.data[CONF_ACCESS_TOKEN] == "access-1"
    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED
    assert registry.async_get(entity_id).unique_id == ZONE_ID
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1

    mock_api.hide_zone = True
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert registry.async_get(entity_id).unique_id == ZONE_ID
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1

    mock_api.hide_zone = False
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED
    assert hass.states.get(entity_id).attributes["source"] == "Jazz After Dark"
    assert registry.async_get(entity_id).device_id == device_id
    assert len(_soundtrack_devices(hass, entry.entry_id)) == 2
