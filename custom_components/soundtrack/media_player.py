"""Media player entities, one per Soundtrack sound zone."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    Category,
    PlaylistRef,
    SoundtrackAuthError,
    SoundtrackError,
    level_to_volume,
    playback_to_state,
    playlist_sources,
    track_position,
    volume_to_level,
)
from .const import DOMAIN, VOLUME_MAX
from .coordinator import SoundtrackConfigEntry, SoundtrackCoordinator

_LOGGER = logging.getLogger(__name__)

_TYPE_ROOT = "root"
_TYPE_FAVORITES = "favorites"
_TYPE_DISCOVER = "discover"
_TYPE_CATEGORY = "category"
# Soundtrack's nowPlaying lags the command. One more poll, then the 15s cycle.
# Upgrade path: playbackUpdate over graphql-ws.
_FOLLOW_UP_SECONDS = 4

_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
    | MediaPlayerEntityFeature.SELECT_SOURCE
)

_STATE = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "idle": MediaPlayerState.IDLE,
    "off": MediaPlayerState.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SoundtrackConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a media player for each sound zone."""
    coordinator = entry.runtime_data
    platform = entity_platform.async_get_current_platform()
    if not hass.services.has_service(DOMAIN, "play_playlist"):
        schema = {vol.Required("playlist_id"): cv.string}
        platform.async_register_entity_service("play_playlist", schema, "async_play_playlist")
        platform.async_register_entity_service("favorite_playlist", schema, "async_favorite_playlist")
        platform.async_register_entity_service(
            "unfavorite_playlist", schema, "async_unfavorite_playlist"
        )

    known: set[str] = set()

    def _add_zones() -> None:
        if coordinator.data is None:
            return
        added = [
            SoundtrackZone(coordinator, zone_id)
            for zone_id in coordinator.data.zones
            if zone_id not in known
        ]
        known.update(zone.id for zone in coordinator.data.zones.values())
        if added:
            async_add_entities(added)

    entry.async_on_unload(coordinator.async_add_listener(_add_zones))
    _add_zones()


class SoundtrackZone(CoordinatorEntity[SoundtrackCoordinator], MediaPlayerEntity):
    """A Soundtrack sound zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = _FEATURES
    _attr_volume_step = 1 / VOLUME_MAX

    def __init__(self, coordinator: SoundtrackCoordinator, zone_id: str) -> None:
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._attr_unique_id = zone_id
        self._follow_up = None

    @property
    def _zone(self):
        data = self.coordinator.data
        if data is None:
            return None
        return data.zones.get(self._zone_id)

    @property
    def available(self) -> bool:
        zone = self._zone
        if not self.coordinator.last_update_success or zone is None:
            return False
        # Offline is not a media-player state. Home Assistant shows the entity
        # as unavailable when this returns false.
        return playback_to_state(zone.playback_state, paired=zone.paired, online=zone.online) != "unavailable"

    @property
    def device_info(self) -> DeviceInfo:
        zone = self._zone
        location = zone.location_name if zone and zone.location_name else None
        name = zone.name if zone else self._zone_id
        info = DeviceInfo(
            identifiers={(DOMAIN, self._zone_id)},
            name=f"{location} {name}" if location else name,
            manufacturer="Soundtrack",
            model="Sound zone",
            suggested_area=location,
        )
        if self.coordinator.config_entry is not None:
            info["via_device"] = (DOMAIN, self.coordinator.config_entry.entry_id)
        return info

    @property
    def state(self) -> MediaPlayerState | None:
        zone = self._zone
        if zone is None:
            return None
        mapped = playback_to_state(zone.playback_state, paired=zone.paired, online=zone.online)
        if mapped == "unavailable":
            return MediaPlayerState.OFF
        return _STATE[mapped]

    @property
    def volume_level(self) -> float | None:
        zone = self._zone
        if zone is None:
            return None
        return volume_to_level(zone.volume)

    @property
    def source(self) -> str | None:
        zone = self._zone
        return zone.source_name if zone else None

    @property
    def source_list(self) -> list[str] | None:
        labels = [label for label, _playlist_id in self._saved_sources()]
        return labels or None

    @property
    def media_title(self) -> str | None:
        zone = self._zone
        if zone is None:
            return None
        if zone.track and zone.track.title:
            return zone.track.title
        return zone.source_name

    @property
    def media_artist(self) -> str | None:
        zone = self._zone
        if zone is None or zone.track is None:
            return None
        return zone.track.artists

    @property
    def media_album_name(self) -> str | None:
        zone = self._zone
        if zone is None or zone.track is None:
            return None
        return zone.track.album

    @property
    def media_image_url(self) -> str | None:
        zone = self._zone
        if zone is None or zone.track is None:
            return None
        return zone.track.image_url

    @property
    def media_duration(self) -> int | None:
        zone = self._zone
        if zone is None or zone.track is None:
            return None
        return zone.track.duration

    @property
    def media_content_id(self) -> str | None:
        zone = self._zone
        return zone.source_id if zone else None

    @property
    def media_content_type(self) -> MediaType | None:
        zone = self._zone
        if zone and zone.track and zone.track.title:
            return MediaType.MUSIC
        return None

    @property
    def media_position(self) -> float | None:
        zone = self._zone
        data = self.coordinator.data
        if zone is None or zone.track is None:
            return None
        track = zone.track
        if track.progress is not None:
            if track.duration is not None:
                return min(track.progress, float(track.duration))
            return track.progress
        if data is None:
            return None
        return track_position(
            track.started_at,
            data.fetched_at,
            track.duration,
            playing=playback_to_state(zone.playback_state, paired=zone.paired, online=zone.online) == "playing",
        )

    @property
    def media_position_updated_at(self):
        zone = self._zone
        if zone is not None and zone.track is not None and zone.track.progress_at is not None:
            return zone.track.progress_at
        if self.media_position is None or self.coordinator.data is None:
            return None
        return self.coordinator.data.fetched_at

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        zone = self._zone
        if zone is None:
            return {}
        return {
            "account": zone.account_name,
            "location": zone.location_name,
            "paired": zone.paired,
            "online": zone.online,
            "soundtrack_volume": zone.volume,
            "source_id": zone.source_id,
        }

    async def async_media_play(self) -> None:
        """Resume the zone."""
        await self._run(self.coordinator.client.async_play(self._zone_id))

    async def async_media_pause(self) -> None:
        """Pause the zone."""
        await self._run(self.coordinator.client.async_pause(self._zone_id))

    async def async_media_next_track(self) -> None:
        """Skip to the next track. This is Soundtrack's fast-forward."""
        await self._run(self.coordinator.client.async_skip(self._zone_id))

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume. The slider is mapped onto 17 steps (0–16)."""
        await self._run(self.coordinator.client.async_set_volume(self._zone_id, level_to_volume(volume)))

    async def async_select_source(self, source: str) -> None:
        """Play a saved playlist chosen by name."""
        playlist_id = dict(self._saved_sources()).get(source)
        if not playlist_id:
            raise HomeAssistantError(f"No saved playlist named {source}.")
        await self.async_play_playlist(playlist_id)

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs) -> None:
        """Play a playlist chosen in the media browser."""
        if media_type not in {MediaType.PLAYLIST, MediaType.MUSIC, "playlist"}:
            raise HomeAssistantError(f"Soundtrack cannot play {media_type}.")
        playlist_id = media_id.removeprefix("playlist:")
        if not playlist_id:
            raise HomeAssistantError("Choose a playlist to play.")
        await self.async_play_playlist(playlist_id)

    async def async_play_playlist(self, playlist_id: str) -> None:
        """Start a playlist on this sound zone."""
        await self._run(self.coordinator.client.async_play_playlist(self._zone_id, playlist_id))

    async def async_favorite_playlist(self, playlist_id: str) -> None:
        """Save a playlist on this zone's account."""
        zone = self._require_zone()
        await self._run(self.coordinator.client.async_favorite(zone.account_id, playlist_id))

    async def async_unfavorite_playlist(self, playlist_id: str) -> None:
        """Remove a playlist from this zone's account."""
        zone = self._require_zone()
        await self._run(self.coordinator.client.async_unfavorite(zone.account_id, playlist_id))

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse saved playlists and the Soundtrack catalog."""
        try:
            return await self._browse(media_content_type, media_content_id)
        except SoundtrackError as err:
            self._raise_command_error(err)
            raise

    async def _browse(
        self,
        media_content_type: MediaType | str | None,
        media_content_id: str | None,
    ) -> BrowseMedia:
        kind = media_content_type or _TYPE_ROOT
        if kind == _TYPE_ROOT:
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=_TYPE_ROOT,
                media_content_type=_TYPE_ROOT,
                title="Soundtrack",
                can_play=False,
                can_expand=True,
                can_search=True,
                children=[
                    _folder(
                        "Favorites",
                        _TYPE_FAVORITES,
                        _TYPE_FAVORITES,
                        MediaClass.PLAYLIST,
                        can_search=True,
                    ),
                    _folder(
                        "Discover",
                        _TYPE_DISCOVER,
                        _TYPE_DISCOVER,
                        MediaClass.GENRE,
                        can_search=True,
                    ),
                ],
            )
        if kind == _TYPE_FAVORITES:
            zone = self._require_zone()
            playlists = await self.coordinator.client.async_favorites(zone.account_id)
            return _folder(
                "Favorites",
                _TYPE_FAVORITES,
                _TYPE_FAVORITES,
                MediaClass.PLAYLIST,
                children=[_playlist_media(playlist) for playlist in playlists],
                can_search=True,
            )
        if kind == _TYPE_DISCOVER:
            categories = await self.coordinator.client.async_categories()
            return _folder(
                "Discover",
                _TYPE_DISCOVER,
                _TYPE_DISCOVER,
                MediaClass.GENRE,
                children=[_category_media(category) for category in categories],
                can_search=True,
            )
        if kind in {_TYPE_CATEGORY, MediaClass.GENRE} and media_content_id:
            title, playlists, related = await self.coordinator.client.async_category_page(media_content_id)
            return _folder(
                title,
                _TYPE_CATEGORY,
                media_content_id,
                MediaClass.PLAYLIST,
                children=[_playlist_media(playlist) for playlist in playlists]
                + [_category_media(category) for category in related],
            )
        raise HomeAssistantError("That Soundtrack library folder is not available.")

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search Soundtrack playlists from the media browser."""
        try:
            playlists = await self.coordinator.client.async_search_playlists(query.search_query)
        except SoundtrackError as err:
            self._raise_command_error(err)
        allowed = set(query.media_filter_classes or [])
        if allowed and MediaClass.PLAYLIST not in allowed:
            playlists = []
        return SearchMedia(result=[_playlist_media(playlist) for playlist in playlists])

    def _saved_sources(self) -> list[tuple[str, str]]:
        zone = self._zone
        data = self.coordinator.data
        if zone is None or data is None:
            return []
        account = data.accounts.get(zone.account_id)
        if account is None:
            return []
        return playlist_sources(account.playlists)

    def _require_zone(self):
        zone = self._zone
        if zone is None:
            raise HomeAssistantError("This sound zone is no longer in the Soundtrack account.")
        return zone

    async def _run(self, coro) -> None:
        try:
            await coro
        except SoundtrackError as err:
            self._raise_command_error(err)
        await self.coordinator.async_refresh()
        if self._follow_up is not None:
            self._follow_up()
        self._follow_up = async_call_later(self.hass, _FOLLOW_UP_SECONDS, self._refresh_again)

    async def _refresh_again(self, _now) -> None:
        self._follow_up = None
        await self.coordinator.async_refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._follow_up is not None:
            self._follow_up()
            self._follow_up = None
        await super().async_will_remove_from_hass()

    def _raise_command_error(self, err: SoundtrackError) -> None:
        if isinstance(err, SoundtrackAuthError):
            _LOGGER.info("Soundtrack signed this session out")
            assert self.coordinator.config_entry is not None
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "Soundtrack signed this session out. Reconnect the integration."
            ) from err
        raise HomeAssistantError(str(err)) from err


def _folder(
    title: str,
    content_type: str,
    content_id: str,
    child_class: MediaClass,
    children: list[BrowseMedia] | None = None,
    *,
    can_search: bool = False,
) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type=content_type,
        title=title,
        can_play=False,
        can_expand=True,
        can_search=can_search,
        children=children,
        children_media_class=child_class,
    )


def _playlist_media(playlist: PlaylistRef) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.PLAYLIST,
        media_content_id=playlist.id,
        media_content_type=MediaType.PLAYLIST,
        title=playlist.name,
        can_play=True,
        can_expand=False,
        thumbnail=playlist.image_url,
    )


def _category_media(category: Category) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.GENRE,
        media_content_id=category.id,
        media_content_type=_TYPE_CATEGORY,
        title=category.name,
        can_play=False,
        can_expand=True,
        thumbnail=category.image_url,
        children_media_class=MediaClass.PLAYLIST,
    )
