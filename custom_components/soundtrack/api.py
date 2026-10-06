"""Soundtrack GraphQL client.

Auth is not OAuth. ``loginUser`` returns an access token plus a rotating
refresh token. The access token is renewed with ``refreshLogin`` until
Soundtrack rejects the refresh token, which is a real sign-out.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from .const import (
    MAX_LIBRARY_PAGES,
    PAGE_SIZE,
    TOKEN_REFRESH_MARGIN_SECONDS,
    VOLUME_MAX,
    api_url,
)

_LOGGER = logging.getLogger(__name__)

TokenListener = Callable[["Tokens"], Awaitable[None]]


class SoundtrackError(Exception):
    """Base error for Soundtrack API calls."""


class SoundtrackAuthError(SoundtrackError):
    """The session is no longer valid. The user has to sign in again."""


class SoundtrackConnectionError(SoundtrackError):
    """The API could not be reached."""


class SoundtrackApiError(SoundtrackError):
    """The API answered, but rejected the request."""


@dataclass(slots=True)
class Tokens:
    """Access and refresh tokens for one Soundtrack user."""

    access_token: str
    refresh_token: str
    expires_at: str
    user_id: str
    email: str


@dataclass(slots=True)
class Identity:
    """The signed-in Soundtrack user."""

    id: str
    name: str
    email: str


@dataclass(slots=True)
class PlaylistRef:
    """A playlist the user can play or save."""

    id: str
    name: str
    description: str | None = None
    image_url: str | None = None


@dataclass(slots=True)
class Category:
    """A Soundtrack browse category."""

    id: str
    name: str
    image_url: str | None = None


@dataclass(slots=True)
class Track:
    """The track currently playing in a zone, if any."""

    title: str | None
    artists: str | None
    album: str | None
    image_url: str | None
    duration: int | None
    started_at: datetime | None
    # Seconds into the track as Soundtrack last measured it, and when that was.
    progress: float | None = None
    progress_at: datetime | None = None


@dataclass(slots=True)
class Account:
    """One business account the user can control."""

    id: str
    name: str
    playlists: list[PlaylistRef] = field(default_factory=list)
    library_cursor: str | None = None


@dataclass(slots=True)
class Zone:
    """One Soundtrack sound zone. This is the speaker."""

    id: str
    name: str
    location_id: str
    location_name: str
    account_id: str
    account_name: str
    online: bool
    paired: bool
    playback_state: str | None
    volume: int | None
    source_id: str | None
    source_name: str | None
    source_type: str | None
    track: Track | None


@dataclass(slots=True)
class Snapshot:
    """Zones and saved playlists fetched in one pass."""

    user_name: str | None
    zones: dict[str, Zone]
    accounts: dict[str, Account]
    fetched_at: datetime
    truncated: bool = False


def volume_to_level(volume: int | None) -> float | None:
    """Map Soundtrack's 0-16 step to Home Assistant's 0-1 level."""
    if volume is None:
        return None
    return max(0.0, min(1.0, volume / VOLUME_MAX))


def level_to_volume(level: float) -> int:
    """Map a 0-1 level to Soundtrack's 0-16 step."""
    return max(0, min(VOLUME_MAX, round(level * VOLUME_MAX)))


def playback_to_state(playback: str | None, *, paired: bool, online: bool) -> str:
    """Return playing, paused, idle, off, or unavailable."""
    if not paired or playback == "unpaired":
        return "off"
    if not online or playback == "offline":
        return "unavailable"
    if playback == "playing":
        return "playing"
    if playback == "paused":
        return "paused"
    if playback == "not_supported":
        return "off"
    return "idle"


def track_position(
    started_at: datetime | None,
    fetched_at: datetime,
    duration: int | None,
    *,
    playing: bool,
) -> float | None:
    """Seconds into the track at fetch time. Home Assistant advances it while playing."""
    if not playing or started_at is None:
        return None
    elapsed = (fetched_at - started_at).total_seconds()
    if elapsed < 0:
        return 0
    if duration is not None and elapsed > duration:
        return float(duration)
    return elapsed


def playlist_sources(playlists: list[PlaylistRef]) -> list[tuple[str, str]]:
    """Return ``(label, id)`` with duplicate names disambiguated."""
    counts = Counter(playlist.name or "Playlist" for playlist in playlists)
    seen: Counter[str] = Counter()
    rows: list[tuple[str, str]] = []
    for playlist in playlists:
        name = playlist.name or "Playlist"
        seen[name] += 1
        label = name if counts[name] == 1 else f"{name} ({seen[name]})"
        rows.append((label, playlist.id))
    return rows


def parse_instant(value: str | None) -> datetime | None:
    """Parse a Soundtrack Instant/Date scalar."""
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


# Square art for the player and the browser. The thumbnail preset is 150px,
# and hero is a 1200x400 banner, so neither is the album picture.
_ART_EDGE = 960


def _url(value: Any) -> str | None:
    found: str | None = None
    if isinstance(value, str) and value:
        found = value
    elif isinstance(value, dict):
        inner = value.get("url")
        if isinstance(inner, str) and inner:
            found = inner
    if found and "%w" in found:
        # Soundtrack leaves the pixel size in the URL. A literal %w is a 400.
        edge = str(_ART_EDGE)
        return found.replace("%w", edge).replace("%h", edge)
    return found


def _image_from_display(display: dict[str, Any] | None) -> str | None:
    if not display:
        return None
    image = display.get("image") or {}
    sized = _url(image.get("size"))
    if sized:
        return sized
    sizes = image.get("sizes") or {}
    teaser = _url(sizes.get("teaser"))
    if teaser:
        return teaser
    placeholder = _url(image.get("placeholder"))
    if placeholder:
        return placeholder
    return _url(sizes.get("thumbnail")) or _url(sizes.get("hero"))


def _nodes(connection: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not connection:
        return [], {}
    nodes: list[dict[str, Any]] = []
    for edge in connection.get("edges") or []:
        if edge and edge.get("node"):
            nodes.append(edge["node"])
    return nodes, connection.get("pageInfo") or {}


def _category(node: dict[str, Any]) -> Category:
    image = _image_from_display(node.get("display")) or _url((node.get("image") or {}).get("large"))
    return Category(id=node["id"], name=node.get("name") or "Category", image_url=image)


def _playlist(node: dict[str, Any]) -> PlaylistRef:
    return PlaylistRef(
        id=node["id"],
        name=node.get("name") or "Playlist",
        description=node.get("description") or None,
        image_url=_image_from_display(node.get("display")),
    )


def _progress(playback: dict[str, Any]) -> tuple[float | None, datetime | None]:
    """Soundtrack's measured position. Wall clock from startedAt includes time spent paused."""
    raw = playback.get("progress") or {}
    milliseconds = raw.get("progressMs")
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
        return None, None
    return max(0.0, milliseconds / 1000), parse_instant(raw.get("updatedAt"))


def _newer_track(current: Track | None, candidate: Track | None) -> Track | None:
    """Prefer playback.current when it starts a later song.

    nowPlaying.startedAt is whole seconds. playback.current.start has
    milliseconds, so play-now leaves current a fraction of a second ahead
    while the speaker is still on the queued track.
    """
    if current is None:
        return candidate
    if candidate is None or candidate.started_at is None:
        return current
    if current.started_at is None:
        return candidate
    if candidate.started_at - current.started_at < timedelta(seconds=1):
        return current
    return candidate


def _track(now_playing: dict[str, Any] | None) -> Track | None:
    if not now_playing:
        return None
    raw = now_playing.get("track")
    started_at = parse_instant(now_playing.get("startedAt"))
    if not raw:
        if started_at is None:
            return None
        return Track(None, None, None, None, None, started_at)
    artists = ", ".join(
        artist.get("name", "")
        for artist in raw.get("artists") or []
        if artist and artist.get("name")
    )
    album = raw.get("album") or {}
    duration_ms = raw.get("durationMs")
    duration = int(duration_ms / 1000) if isinstance(duration_ms, int) else None
    return Track(
        title=raw.get("title") or raw.get("name"),
        artists=artists or None,
        album=album.get("title") or album.get("name"),
        image_url=_image_from_display(raw.get("display")),
        duration=duration,
        started_at=started_at,
    )


def _volume(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except TypeError, ValueError:
        return None
    return max(0, min(VOLUME_MAX, number))


def _source(play_from: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    if not play_from:
        return None, None, None
    return play_from.get("id"), play_from.get("name"), play_from.get("__typename")


def parse_snapshot(data: dict[str, Any], *, now: datetime | None = None) -> Snapshot:
    """Build a snapshot from a ``me { ... on User }`` payload."""
    me = data.get("me") or {}
    typename = me.get("__typename")
    if typename and typename != "User":
        raise SoundtrackApiError(
            "Soundtrack session is not a user. Sign in with the account email and password."
        )
    zones: dict[str, Zone] = {}
    accounts: dict[str, Account] = {}
    truncated = False
    account_nodes, account_page = _nodes(me.get("accounts"))
    truncated = truncated or bool(account_page.get("hasNextPage"))
    for account_node in account_nodes:
        library = (account_node.get("musicLibrary") or {}).get("playlists")
        playlist_nodes, library_page = _nodes(library)
        account = Account(
            id=account_node["id"],
            name=account_node.get("businessName") or "Soundtrack",
            playlists=[_playlist(node) for node in playlist_nodes],
            library_cursor=library_page.get("endCursor")
            if library_page.get("hasNextPage")
            else None,
        )
        accounts[account.id] = account
        location_nodes, location_page = _nodes(account_node.get("locations"))
        truncated = truncated or bool(location_page.get("hasNextPage"))
        for location in location_nodes:
            zone_nodes, zone_page = _nodes(location.get("soundZones"))
            truncated = truncated or bool(zone_page.get("hasNextPage"))
            for zone in zone_nodes:
                playback = zone.get("playback") or {}
                source_id, source_name, source_type = _source(playback.get("playFrom"))
                current = playback.get("current") or {}
                playable = (
                    current.get("playable") if isinstance(current.get("playable"), dict) else None
                )
                current_track = None
                if (
                    playable
                    and playable.get("__typename") in {None, "Track"}
                    and (
                        playable.get("title")
                        or playable.get("name")
                        or playable.get("__typename") == "Track"
                    )
                ):
                    current_track = _track({"startedAt": current.get("start"), "track": playable})
                track = _newer_track(_track(zone.get("nowPlaying")), current_track)
                progress, progress_at = _progress(playback)
                if track is not None and progress is not None:
                    track.progress = progress
                    track.progress_at = progress_at
                zones[zone["id"]] = Zone(
                    id=zone["id"],
                    name=zone.get("name") or "Sound zone",
                    location_id=location.get("id") or "",
                    location_name=location.get("name") or "",
                    account_id=account.id,
                    account_name=account.name,
                    online=bool(zone.get("online")),
                    paired=bool(zone.get("isPaired")),
                    playback_state=playback.get("state"),
                    volume=_volume(playback.get("volume")),
                    source_id=source_id,
                    source_name=source_name,
                    source_type=source_type,
                    track=track,
                )
    return Snapshot(
        user_name=me.get("name"),
        zones=zones,
        accounts=accounts,
        fetched_at=now or datetime.now(UTC),
        truncated=truncated,
    )


def _messages(errors: list[dict[str, Any]]) -> str:
    parts = [str(error.get("message")).strip() for error in errors if error.get("message")]
    return "; ".join(parts)


def _is_auth(status: int, errors: list[dict[str, Any]]) -> bool:
    if status == 401:
        return True
    for error in errors:
        code = str((error.get("extensions") or {}).get("code") or "").upper()
        if code in {"UNAUTHENTICATED", "UNAUTHORIZED"}:
            return True
        message = str(error.get("message") or "").strip().casefold()
        if message in {"unauthenticated", "unauthorized", "invalid token"}:
            return True
        if "token" in message and any(
            word in message for word in ("expired", "invalid", "revoked")
        ):
            return True
    return False


def unwrap_response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    """Return the GraphQL ``data`` object or raise a typed error."""
    errors = [error for error in body.get("errors") or [] if isinstance(error, dict)]
    if _is_auth(status, errors):
        raise SoundtrackAuthError(_messages(errors) or "Soundtrack rejected the session.")
    data = body.get("data")
    if status == 429:
        raise SoundtrackApiError("Soundtrack rate limit reached. Try again shortly.")
    if status >= 500:
        raise SoundtrackConnectionError(f"Soundtrack returned HTTP {status}.")
    if errors and not data:
        raise SoundtrackApiError(_messages(errors) or "Soundtrack request failed.")
    if status >= 400:
        raise SoundtrackApiError(_messages(errors) or f"Soundtrack returned HTTP {status}.")
    if not isinstance(data, dict):
        raise SoundtrackApiError("Soundtrack returned an empty response.")
    if errors:
        _LOGGER.warning("Soundtrack returned partial errors: %s", _messages(errors))
    return data


_DISPLAY_IMAGE = f"""
display {{
  image {{
    placeholder
    size(width: {_ART_EDGE}, height: {_ART_EDGE})
    sizes {{ teaser thumbnail }}
  }}
}}
"""

_TRACK_FIELDS = f"""
title
name
durationMs
artists {{ name }}
album {{ title name }}
{_DISPLAY_IMAGE}
"""

_PLAYLIST_FIELDS = f"""
id
name
description
{_DISPLAY_IMAGE}
"""

_SNAPSHOT_QUERY = f"""
query Snapshot($first: Int!) {{
  me {{
    __typename
    ... on User {{
      id
      name
      email
      accounts(first: $first) {{
        pageInfo {{ hasNextPage endCursor }}
        edges {{
          node {{
            id
            businessName
            musicLibrary {{
              playlists(first: $first) {{
                pageInfo {{ hasNextPage endCursor }}
                edges {{ node {{ id name }} }}
              }}
            }}
            locations(first: $first) {{
              pageInfo {{ hasNextPage endCursor }}
              edges {{
                node {{
                  id
                  name
                  soundZones(first: $first) {{
                    pageInfo {{ hasNextPage endCursor }}
                    edges {{
                      node {{
                        id
                        name
                        online
                        isPaired
                        playback {{
                          state
                          volume
                          progress {{ progressMs updatedAt }}
                          current {{
                            start
                            playable {{
                              __typename
                              ... on Track {{ {_TRACK_FIELDS} }}
                            }}
                          }}
                          playFrom {{
                            __typename
                            ... on Playlist {{ id name }}
                            ... on Schedule {{ id name }}
                            ... on Soundtrack {{ id name }}
                          }}
                        }}
                        nowPlaying {{
                          startedAt
                          track {{ {_TRACK_FIELDS} }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

_LIBRARY_PAGE_QUERY = """
query LibraryPage($id: ID!, $first: Int!, $after: String) {
  account(id: $id) {
    musicLibrary {
      playlists(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        edges { node { id name } }
      }
    }
  }
}
"""

_FAVORITES_QUERY = f"""
query Favorites($id: ID!, $first: Int!, $after: String) {{
  account(id: $id) {{
    musicLibrary {{
      playlists(first: $first, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        edges {{ node {{ {_PLAYLIST_FIELDS} }} }}
      }}
    }}
  }}
}}
"""

_CATEGORIES_QUERY = f"""
query Categories($first: Int!) {{
  browseCategories(first: $first) {{
    edges {{
      node {{
        id
        name
        {_DISPLAY_IMAGE}
        image {{ large {{ url }} }}
      }}
    }}
  }}
}}
"""

_SEARCH_QUERY = f"""
query SearchPlaylists($query: String!, $first: Int!) {{
  search(query: $query, type: playlist, first: $first) {{
    edges {{
      node {{
        ... on Playlist {{ {_PLAYLIST_FIELDS} }}
      }}
    }}
  }}
}}
"""

_CATEGORY_PAGE_QUERY = f"""
query CategoryPage($id: String!) {{
  editorialBrowse(id: $id) {{
    title
    sections(first: 8) {{
      edges {{
        node {{
          title
          items(first: 20) {{
            edges {{
              node {{
                __typename
                ... on Playlist {{ {_PLAYLIST_FIELDS} }}
                ... on BrowseCategory {{
                  id
                  name
                  {_DISPLAY_IMAGE}
                  image {{ large {{ url }} }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

_WHOAMI_QUERY = """
query WhoAmI {
  me {
    __typename
    ... on User { id name email }
  }
}
"""

_LOGIN_MUTATION = """
mutation Login($email: String!, $password: String!) {
  loginUser(input: {email: $email, password: $password}) {
    token
    refreshToken
    expiresAt
    userId
  }
}
"""

_REFRESH_MUTATION = """
mutation Refresh($refreshToken: String!) {
  refreshLogin(input: {refreshToken: $refreshToken}) {
    token
    refreshToken
    expiresAt
  }
}
"""

_PLAY_MUTATION = """
mutation Play($soundZone: ID!) {
  play(input: {soundZone: $soundZone}) { status }
}
"""

_PAUSE_MUTATION = """
mutation Pause($soundZone: ID!) {
  pause(input: {soundZone: $soundZone}) { status }
}
"""

_SKIP_MUTATION = """
mutation Skip($soundZone: ID!) {
  skipTrack(input: {soundZone: $soundZone}) { status }
}
"""

_VOLUME_MUTATION = """
mutation SetVolume($soundZone: ID!, $volume: Volume!) {
  setVolume(input: {soundZone: $soundZone, volume: $volume}) { volume }
}
"""

_ASSIGN_MUTATION = """
mutation Assign($soundZone: ID!, $source: ID!) {
  soundZoneAssignSource(input: {soundZones: [$soundZone], source: $source, immediate: true}) {
    soundZones
  }
}
"""

_QUEUE_TRACK_MUTATION = """
mutation QueueTrack($soundZone: ID!, $track: ID!) {
  soundZoneQueueTracks(input: { soundZone: $soundZone, tracks: [$track], immediate: true }) {
    status
  }
}
"""

_FAVORITE_MUTATION = """
mutation Favorite($parent: ID!, $source: ID!) {
  addToMusicLibrary(input: {parent: $parent, source: $source}) {
    musicLibrary { id }
  }
}
"""

_UNFAVORITE_MUTATION = """
mutation Unfavorite($parent: ID!, $source: ID!) {
  removeFromMusicLibrary(input: {parent: $parent, source: $source}) {
    musicLibrary { id }
  }
}
"""


async def async_graphql(
    session: aiohttp.ClientSession,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    token: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """POST one GraphQL operation and return its data."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with session.post(
            base_url or api_url(),
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            cost = response.headers.get("x-ratelimiting-cost")
            if cost is not None:
                _LOGGER.debug(
                    "Soundtrack rate limit cost=%s remaining=%s",
                    cost,
                    response.headers.get("x-ratelimiting-tokens-available"),
                )
            try:
                body = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as err:
                if response.status == 401:
                    raise SoundtrackAuthError("Soundtrack rejected the session.") from err
                if response.status >= 500:
                    raise SoundtrackConnectionError(
                        f"Soundtrack returned HTTP {response.status}."
                    ) from err
                raise SoundtrackApiError(
                    "Soundtrack returned a response that was not JSON."
                ) from err
            if not isinstance(body, dict):
                raise SoundtrackApiError("Soundtrack returned a response that was not JSON.")
            return unwrap_response(response.status, body)
    except SoundtrackError:
        raise
    except (aiohttp.ClientError, TimeoutError) as err:
        raise SoundtrackConnectionError("Could not reach Soundtrack.") from err


async def async_login(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
    *,
    base_url: str | None = None,
) -> Tokens:
    """Sign in. Raises SoundtrackAuthError when the credentials are rejected."""
    try:
        data = await async_graphql(
            session,
            _LOGIN_MUTATION,
            {"email": email, "password": password},
            base_url=base_url,
        )
    except SoundtrackAuthError:
        raise
    except SoundtrackApiError as err:
        raise SoundtrackAuthError("Soundtrack rejected those credentials.") from err
    payload = data.get("loginUser") or {}
    token = payload.get("token")
    refresh = payload.get("refreshToken")
    if not token or not refresh:
        raise SoundtrackAuthError("Soundtrack rejected those credentials.")
    return Tokens(
        access_token=token,
        refresh_token=refresh,
        expires_at=payload.get("expiresAt") or "",
        user_id=payload.get("userId") or "",
        email=email,
    )


class SoundtrackClient:
    """Authenticated client. One lock covers refresh so two callers cannot rotate the refresh token twice."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: Tokens,
        *,
        on_tokens: TokenListener | None = None,
        base_url: str | None = None,
    ) -> None:
        self._session = session
        self.tokens = tokens
        self._on_tokens = on_tokens
        self._base_url = base_url or api_url()
        # ponytail: one lock serializes every call so a refresh cannot race.
        # Upgrade path: lock only the refresh if command latency starts to matter.
        self._lock = asyncio.Lock()

    async def async_whoami(self) -> Identity:
        """Return the signed-in user."""
        data = await self.execute(_WHOAMI_QUERY)
        me = data.get("me") or {}
        if me.get("__typename") != "User":
            raise SoundtrackApiError("Soundtrack session is not a user.")
        return Identity(
            id=me.get("id") or self.tokens.user_id,
            name=me.get("name") or "",
            email=me.get("email") or self.tokens.email,
        )

    async def async_snapshot(self) -> Snapshot:
        """Fetch zones, playback, and the first pages of saved playlists."""
        data = await self.execute(_SNAPSHOT_QUERY, {"first": PAGE_SIZE})
        snapshot = parse_snapshot(data)
        if snapshot.truncated:
            _LOGGER.warning(
                "Soundtrack returned more than %s accounts, locations, or zones; extras were not loaded",
                PAGE_SIZE,
            )
        for account in snapshot.accounts.values():
            pages = 1
            while account.library_cursor and pages < MAX_LIBRARY_PAGES:
                extra = await self.execute(
                    _LIBRARY_PAGE_QUERY,
                    {"id": account.id, "first": PAGE_SIZE, "after": account.library_cursor},
                )
                library = ((extra.get("account") or {}).get("musicLibrary") or {}).get("playlists")
                nodes, page = _nodes(library)
                account.playlists.extend(_playlist(node) for node in nodes)
                account.library_cursor = page.get("endCursor") if page.get("hasNextPage") else None
                pages += 1
            if account.library_cursor:
                _LOGGER.warning(
                    "Saved playlists for %s were truncated at %s",
                    account.name,
                    PAGE_SIZE * MAX_LIBRARY_PAGES,
                )
                account.library_cursor = None
        return snapshot

    async def async_favorites(self, account_id: str) -> list[PlaylistRef]:
        """List playlists saved on an account, including artwork."""
        playlists: list[PlaylistRef] = []
        cursor: str | None = None
        for _ in range(MAX_LIBRARY_PAGES):
            data = await self.execute(
                _FAVORITES_QUERY,
                {"id": account_id, "first": PAGE_SIZE, "after": cursor},
            )
            library = ((data.get("account") or {}).get("musicLibrary") or {}).get("playlists")
            nodes, page = _nodes(library)
            playlists.extend(_playlist(node) for node in nodes)
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                break
        return playlists

    async def async_search_playlists(self, query: str) -> list[PlaylistRef]:
        """Search the Soundtrack catalog for playlists."""
        data = await self.execute(_SEARCH_QUERY, {"query": query, "first": 25})
        nodes, _page = _nodes(data.get("search"))
        return [_playlist(node) for node in nodes if node.get("id")]

    async def async_categories(self) -> list[Category]:
        """List Soundtrack browse categories."""
        data = await self.execute(_CATEGORIES_QUERY, {"first": PAGE_SIZE})
        nodes, _page = _nodes(data.get("browseCategories"))
        return [_category(node) for node in nodes if node.get("id")]

    async def async_category_page(
        self, category_id: str
    ) -> tuple[str, list[PlaylistRef], list[Category]]:
        """Playlists and related categories for one Discover entry.

        ``browseCategory.playlists`` is empty on the current API. The editorial
        page ``soundtrack:browse:<id>`` is what the Soundtrack app renders.
        """
        page_id = (
            category_id
            if category_id.startswith("soundtrack:browse:")
            else f"soundtrack:browse:{category_id}"
        )
        data = await self.execute(_CATEGORY_PAGE_QUERY, {"id": page_id})
        page = data.get("editorialBrowse") or {}
        playlists: list[PlaylistRef] = []
        seen: set[str] = set()
        related: list[Category] = []
        seen_related: set[str] = set()
        for section in _nodes(page.get("sections"))[0]:
            for node in _nodes(section.get("items"))[0]:
                kind = node.get("__typename")
                if kind == "Playlist" and node.get("id") and node["id"] not in seen:
                    seen.add(node["id"])
                    playlists.append(_playlist(node))
                elif kind == "BrowseCategory" and node.get("id") and node["id"] not in seen_related:
                    if node["id"] == category_id:
                        continue
                    seen_related.add(node["id"])
                    related.append(_category(node))
        # ponytail: first 8 sections, 20 items each. Upgrade path: follow section cursors.
        return page.get("title") or "Playlists", playlists, related

    async def async_play(self, zone_id: str) -> None:
        """Resume playback."""
        await self.execute(_PLAY_MUTATION, {"soundZone": zone_id})

    async def async_pause(self, zone_id: str) -> None:
        """Pause playback."""
        await self.execute(_PAUSE_MUTATION, {"soundZone": zone_id})

    async def async_skip(self, zone_id: str) -> None:
        """Skip to the next track. Soundtrack has no in-track seek."""
        await self.execute(_SKIP_MUTATION, {"soundZone": zone_id})

    async def async_set_volume(self, zone_id: str, volume: int) -> None:
        """Set the zone volume to a step from 0 to 16."""
        await self.execute(
            _VOLUME_MUTATION,
            {"soundZone": zone_id, "volume": max(0, min(VOLUME_MAX, volume))},
        )

    async def async_play_playlist(self, zone_id: str, playlist_id: str) -> None:
        """Assign a playlist and start it. Each step is retried once.

        Assign with ``immediate: true`` and the following ``play`` race on a
        zone that is still switching source. The second call is the one that
        lands.
        """
        await self._once_more(
            lambda: self.execute(_ASSIGN_MUTATION, {"soundZone": zone_id, "source": playlist_id})
        )
        await self._once_more(lambda: self.execute(_PLAY_MUTATION, {"soundZone": zone_id}))

    async def async_play_track(self, zone_id: str, track_id: str) -> None:
        """Start one track. The queue call starts playback, so this does not send play."""
        await self._once_more(
            lambda: self.execute(
                _QUEUE_TRACK_MUTATION,
                {"soundZone": zone_id, "track": track_id},
            )
        )

    async def _once_more(self, call) -> dict[str, Any]:
        try:
            return await call()
        except SoundtrackApiError:
            await asyncio.sleep(0.75)
            return await call()

    async def async_favorite(self, account_id: str, playlist_id: str) -> None:
        """Save a playlist on the account."""
        await self.execute(_FAVORITE_MUTATION, {"parent": account_id, "source": playlist_id})

    async def async_unfavorite(self, account_id: str, playlist_id: str) -> None:
        """Remove a playlist from the account."""
        await self.execute(_UNFAVORITE_MUTATION, {"parent": account_id, "source": playlist_id})

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an authenticated operation, refreshing the session first when it is due or rejected."""
        async with self._lock:
            await self._refresh_if_due()
            try:
                return await async_graphql(
                    self._session,
                    query,
                    variables,
                    token=self.tokens.access_token,
                    base_url=self._base_url,
                )
            except SoundtrackAuthError:
                _LOGGER.debug("Soundtrack access token was rejected; refreshing the session")
                await self._refresh()
                return await async_graphql(
                    self._session,
                    query,
                    variables,
                    token=self.tokens.access_token,
                    base_url=self._base_url,
                )

    def _expires_soon(self) -> bool:
        expires = parse_instant(self.tokens.expires_at)
        if expires is None:
            return False
        return expires <= datetime.now(UTC) + timedelta(seconds=TOKEN_REFRESH_MARGIN_SECONDS)

    async def _refresh_if_due(self) -> None:
        if self._expires_soon():
            await self._refresh()

    async def _refresh(self) -> None:
        """Exchange the refresh token. A failure here is a sign-out, not a prompt to retry login."""
        if not self.tokens.refresh_token:
            raise SoundtrackAuthError("Soundtrack session has no refresh token.")
        try:
            data = await async_graphql(
                self._session,
                _REFRESH_MUTATION,
                {"refreshToken": self.tokens.refresh_token},
                base_url=self._base_url,
            )
        except SoundtrackAuthError:
            raise
        except SoundtrackApiError as err:
            raise SoundtrackAuthError(str(err)) from err
        payload = data.get("refreshLogin") or {}
        token = payload.get("token")
        refresh = payload.get("refreshToken")
        if not token or not refresh:
            raise SoundtrackAuthError("Soundtrack did not return a new session.")
        self.tokens = Tokens(
            access_token=token,
            refresh_token=refresh,
            expires_at=payload.get("expiresAt") or "",
            user_id=self.tokens.user_id,
            email=self.tokens.email,
        )
        if self._on_tokens is None:
            return
        try:
            await self._on_tokens(self.tokens)
        except Exception:
            _LOGGER.exception("Could not store the refreshed Soundtrack session")
