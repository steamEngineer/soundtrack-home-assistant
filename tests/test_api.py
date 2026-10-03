"""Checks for the Soundtrack client. Home Assistant itself is not required."""

from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
from aiohttp import web
import pytest

from custom_components.soundtrack.api import (
    PlaylistRef,
    SoundtrackApiError,
    SoundtrackAuthError,
    SoundtrackClient,
    SoundtrackConnectionError,
    Tokens,
    _SNAPSHOT_QUERY,
    async_login,
    level_to_volume,
    parse_snapshot,
    playback_to_state,
    playlist_sources,
    track_position,
    unwrap_response,
    volume_to_level,
)


def test_queries_are_balanced() -> None:
    assert _SNAPSHOT_QUERY.count("{") == _SNAPSHOT_QUERY.count("}")
    assert "nowPlaying" in _SNAPSHOT_QUERY
    assert "password" not in _SNAPSHOT_QUERY


def test_volume_mapping() -> None:
    assert level_to_volume(0) == 0
    assert level_to_volume(0.5) == 8
    assert level_to_volume(1) == 16
    assert level_to_volume(-1) == 0
    assert level_to_volume(2) == 16
    assert volume_to_level(None) is None
    assert volume_to_level(0) == 0
    assert volume_to_level(16) == 1
    assert volume_to_level(8) == 0.5


def test_playback_state() -> None:
    assert playback_to_state("playing", paired=True, online=True) == "playing"
    assert playback_to_state("paused", paired=True, online=True) == "paused"
    assert playback_to_state("playing", paired=False, online=True) == "off"
    assert playback_to_state("playing", paired=True, online=False) == "unavailable"
    assert playback_to_state("offline", paired=True, online=True) == "unavailable"
    assert playback_to_state(None, paired=True, online=True) == "idle"


def test_track_position() -> None:
    start = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
    fetched = datetime(2026, 10, 3, 3, 0, 30, tzinfo=timezone.utc)
    assert track_position(start, fetched, 180, playing=True) == 30
    assert track_position(start, fetched, 180, playing=False) is None
    assert track_position(None, fetched, 180, playing=True) is None
    assert track_position(fetched, start, 180, playing=True) == 0
    assert track_position(start, datetime(2026, 10, 3, 3, 5, tzinfo=timezone.utc), 180, playing=True) == 180


def test_placeholder_art_is_resized() -> None:
    from custom_components.soundtrack.api import _image_from_display

    assert _image_from_display({"image": {"placeholder": "https://cdn.example/%w/%h"}}) == (
        "https://cdn.example/300/300"
    )
    assert _image_from_display(
        {"image": {"sizes": {"thumbnail": "https://cdn.example/%w/%h/jazz"}}}
    ) == "https://cdn.example/300/300/jazz"


def test_playlist_sources_disambiguate_names() -> None:
    rows = playlist_sources(
        [
            PlaylistRef("a", "Morning"),
            PlaylistRef("b", "Evening"),
            PlaylistRef("c", "Morning"),
        ]
    )
    assert rows == [("Morning (1)", "a"), ("Evening", "b"), ("Morning (2)", "c")]


def test_parse_snapshot_reads_zone_and_flags_truncation() -> None:
    snapshot = parse_snapshot(
        {
            "me": {
                "__typename": "User",
                "name": "Ada",
                "accounts": {
                    "pageInfo": {"hasNextPage": False},
                    "edges": [
                        {
                            "node": {
                                "id": "acc",
                                "businessName": "Ada's Cafe",
                                "musicLibrary": {
                                    "playlists": {
                                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                        "edges": [{"node": {"id": "pl1", "name": "Morning"}}],
                                    }
                                },
                                "locations": {
                                    "pageInfo": {"hasNextPage": False},
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "loc",
                                                "name": "Front",
                                                "soundZones": {
                                                    "pageInfo": {"hasNextPage": True},
                                                    "edges": [
                                                        {
                                                            "node": {
                                                                "id": "zone",
                                                                "name": "Bar",
                                                                "online": True,
                                                                "isPaired": True,
                                                                "playback": {
                                                                    "state": "playing",
                                                                    "volume": 8,
                                                                    "playFrom": {
                                                                        "__typename": "Playlist",
                                                                        "id": "pl1",
                                                                        "name": "Morning",
                                                                    },
                                                                },
                                                                "nowPlaying": {
                                                                    "startedAt": "2026-10-03T03:00:00Z",
                                                                    "track": {
                                                                        "title": "Branches",
                                                                        "durationMs": 180000,
                                                                        "artists": [{"name": "Fluida"}],
                                                                        "album": {"title": "Horizon"},
                                                                        "display": {
                                                                            "image": {
                                                                                "sizes": {
                                                                                    "thumbnail": "https://cdn.example/art.jpg"
                                                                                }
                                                                            }
                                                                        },
                                                                    },
                                                                },
                                                            }
                                                        }
                                                    ],
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                        }
                    ],
                },
            }
        },
        now=datetime(2026, 10, 3, 3, 0, 10, tzinfo=timezone.utc),
    )
    assert snapshot.truncated is True
    assert snapshot.user_name == "Ada"
    zone = snapshot.zones["zone"]
    assert zone.name == "Bar"
    assert zone.location_name == "Front"
    assert zone.account_name == "Ada's Cafe"
    assert zone.volume == 8
    assert zone.source_id == "pl1"
    assert zone.track is not None
    assert zone.track.title == "Branches"
    assert zone.track.artists == "Fluida"
    assert zone.track.album == "Horizon"
    assert zone.track.duration == 180
    assert zone.track.image_url == "https://cdn.example/art.jpg"
    assert snapshot.accounts["acc"].library_cursor == "cursor-1"
    assert track_position(zone.track.started_at, snapshot.fetched_at, zone.track.duration, playing=True) == 10


def test_parse_snapshot_prefers_measured_progress_and_the_newer_track() -> None:
    snapshot = parse_snapshot(
        {
            "me": {
                "__typename": "User",
                "accounts": {
                    "edges": [
                        {
                            "node": {
                                "id": "acc",
                                "businessName": "Ada's Cafe",
                                "locations": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "loc",
                                                "name": "Front",
                                                "soundZones": {
                                                    "edges": [
                                                        {
                                                            "node": {
                                                                "id": "zone",
                                                                "name": "Bar",
                                                                "online": True,
                                                                "isPaired": True,
                                                                "playback": {
                                                                    "state": "paused",
                                                                    "progress": {
                                                                        "progressMs": 12500,
                                                                        "updatedAt": "2026-10-03T03:02:12Z",
                                                                    },
                                                                    "current": {
                                                                        "start": "2026-10-03T03:02:00Z",
                                                                        "playable": {
                                                                            "__typename": "Track",
                                                                            "title": "Easy",
                                                                            "artists": [{"name": "Commodores"}],
                                                                            "durationMs": 180000,
                                                                        },
                                                                    },
                                                                },
                                                                "nowPlaying": {
                                                                    "startedAt": "2026-10-03T03:00:00Z",
                                                                    "track": {"title": "Nightshift"},
                                                                },
                                                            }
                                                        }
                                                    ]
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                },
            }
        }
    )
    track = snapshot.zones["zone"].track
    assert track is not None
    assert track.title == "Easy"
    assert track.artists == "Commodores"
    assert track.progress == 12.5
    assert track.progress_at == datetime(2026, 10, 3, 3, 2, 12, tzinfo=timezone.utc)


def test_parse_snapshot_rejects_api_client_session() -> None:
    with pytest.raises(SoundtrackApiError):
        parse_snapshot({"me": {"__typename": "PublicAPIClient"}})


def test_unwrap_distinguishes_sign_out_from_forbidden() -> None:
    with pytest.raises(SoundtrackAuthError):
        unwrap_response(401, {"errors": [{"message": "nope"}]})
    with pytest.raises(SoundtrackAuthError):
        unwrap_response(
            200,
            {"errors": [{"message": "Unauthenticated", "extensions": {"code": "UNAUTHENTICATED"}}]},
        )
    with pytest.raises(SoundtrackApiError):
        unwrap_response(200, {"errors": [{"message": "Forbidden"}]})
    with pytest.raises(SoundtrackConnectionError):
        unwrap_response(503, {})
    with pytest.raises(SoundtrackApiError):
        unwrap_response(429, {})
    assert unwrap_response(200, {"data": {"ok": True}, "errors": [{"message": "partial"}]}) == {"ok": True}


@pytest.fixture
async def graphql():
    state: dict = {"requests": []}

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        state["requests"].append(
            {
                "query": body["query"],
                "variables": body.get("variables"),
                "authorization": request.headers.get("Authorization"),
            }
        )
        return await state["respond"](body, request)

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    state["url"] = f"http://127.0.0.1:{port}/"
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client:
        yield client


def _client(session, graphql, tokens: Tokens | None = None, on_tokens=None) -> SoundtrackClient:
    return SoundtrackClient(
        session,
        tokens
        or Tokens(
            access_token="expired",
            refresh_token="old-refresh",
            expires_at="2099-01-01T00:00:00Z",
            user_id="user-1",
            email="ada@example.com",
        ),
        on_tokens=on_tokens,
        base_url=graphql["url"],
    )


async def test_login_sends_credentials_in_the_body_only(graphql, session) -> None:
    async def respond(body, request):
        return web.json_response(
            {
                "data": {
                    "loginUser": {
                        "token": "access",
                        "refreshToken": "refresh",
                        "expiresAt": "2099-01-01T00:00:00Z",
                        "userId": "user-1",
                    }
                }
            }
        )

    graphql["respond"] = respond
    tokens = await async_login(session, "ada@example.com", "secret", base_url=graphql["url"])
    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.user_id == "user-1"
    sent = graphql["requests"][0]
    assert sent["authorization"] is None
    assert sent["variables"] == {"email": "ada@example.com", "password": "secret"}


async def test_rejected_login_is_auth_error(graphql, session) -> None:
    async def respond(body, request):
        return web.json_response({"errors": [{"message": "Invalid credentials"}]})

    graphql["respond"] = respond
    with pytest.raises(SoundtrackAuthError):
        await async_login(session, "ada@example.com", "wrong", base_url=graphql["url"])


async def test_expired_access_token_refreshes_once(graphql, session) -> None:
    stored: list[Tokens] = []

    async def on_tokens(tokens: Tokens) -> None:
        stored.append(tokens)

    async def respond(body, request):
        if "refreshLogin" in body["query"]:
            assert body["variables"]["refreshToken"] == "old-refresh"
            return web.json_response(
                {
                    "data": {
                        "refreshLogin": {
                            "token": "new-access",
                            "refreshToken": "new-refresh",
                            "expiresAt": "2099-06-01T00:00:00Z",
                        }
                    }
                }
            )
        if request.headers.get("Authorization") != "Bearer new-access":
            return web.json_response(
                {"errors": [{"message": "Unauthenticated", "extensions": {"code": "UNAUTHENTICATED"}}]},
                status=401,
            )
        return web.json_response({"data": {"ok": True}})

    graphql["respond"] = respond
    client = _client(session, graphql, on_tokens=on_tokens)
    assert await client.execute("query { ok }") == {"ok": True}
    assert len(graphql["requests"]) == 3
    refresh_calls = [item for item in graphql["requests"] if "refreshLogin" in item["query"]]
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["authorization"] is None
    assert stored[0].access_token == "new-access"
    assert stored[0].refresh_token == "new-refresh"
    assert client.tokens.email == "ada@example.com"


async def test_rejected_refresh_does_not_loop_or_retry_the_password(graphql, session) -> None:
    async def respond(body, request):
        return web.json_response({"errors": [{"message": "invalid token"}]}, status=401)

    graphql["respond"] = respond
    client = _client(session, graphql)
    with pytest.raises(SoundtrackAuthError):
        await client.execute("query { ok }")
    kinds = ["refresh" if "refreshLogin" in item["query"] else "call" for item in graphql["requests"]]
    assert kinds == ["call", "refresh"]


async def test_forbidden_does_not_refresh(graphql, session) -> None:
    async def respond(body, request):
        return web.json_response({"errors": [{"message": "Forbidden"}]})

    graphql["respond"] = respond
    client = _client(session, graphql)
    with pytest.raises(SoundtrackApiError):
        await client.execute("mutation Pause { pause }")
    assert all("refreshLogin" not in item["query"] for item in graphql["requests"])


async def test_proactive_refresh_uses_the_new_token(graphql, session) -> None:
    async def respond(body, request):
        if "refreshLogin" in body["query"]:
            return web.json_response(
                {
                    "data": {
                        "refreshLogin": {
                            "token": "fresh",
                            "refreshToken": "fresher",
                            "expiresAt": "2099-01-01T00:00:00Z",
                        }
                    }
                }
            )
        assert request.headers.get("Authorization") == "Bearer fresh"
        return web.json_response({"data": {"ok": True}})

    graphql["respond"] = respond
    client = _client(
        session,
        graphql,
        Tokens("stale", "old-refresh", "2000-01-01T00:00:00Z", "user-1", "ada@example.com"),
    )
    assert await client.execute("query { ok }") == {"ok": True}
    assert "refreshLogin" in graphql["requests"][0]["query"]
    assert len(graphql["requests"]) == 2


async def test_category_page_reads_editorial_playlists(graphql, session) -> None:
    async def respond(body, request):
        if "browseCategories" in body["query"]:
            return web.json_response(
                {
                    "data": {
                        "browseCategories": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "electronic",
                                        "name": "Electronic",
                                        "image": {"large": {"url": "https://cdn.example/%w/%h/electronic"}},
                                    }
                                }
                            ]
                        }
                    }
                }
            )
        assert body["variables"] == {"id": "soundtrack:browse:electronic"}
        assert "editorialBrowse" in body["query"]
        return web.json_response(
            {
                "data": {
                    "editorialBrowse": {
                        "title": "Electronic",
                        "sections": {
                            "edges": [
                                {
                                    "node": {
                                        "items": {
                                            "edges": [
                                                {"node": {"__typename": "Playlist", "id": "pl1", "name": "House"}},
                                                {"node": {"__typename": "Playlist", "id": "pl1", "name": "House"}},
                                                {
                                                    "node": {
                                                        "__typename": "BrowseCategory",
                                                        "id": "lounge",
                                                        "name": "Lounge",
                                                    }
                                                },
                                            ]
                                        }
                                    }
                                }
                            ]
                        },
                    }
                }
            }
        )

    graphql["respond"] = respond
    client = _client(session, graphql)
    categories = await client.async_categories()
    assert categories[0].image_url == "https://cdn.example/300/300/electronic"
    title, playlists, related = await client.async_category_page("electronic")
    assert title == "Electronic"
    assert [(playlist.id, playlist.name) for playlist in playlists] == [("pl1", "House")]
    assert [(category.id, category.name) for category in related] == [("lounge", "Lounge")]


async def test_play_playlist_retries_a_rejected_start(graphql, session) -> None:
    plays = 0

    async def respond(body, request):
        nonlocal plays
        if "play(" in body["query"]:
            plays += 1
            if plays == 1:
                return web.json_response({"errors": [{"message": "Sound zone is not ready"}]})
        return web.json_response({"data": {"ok": True}})

    graphql["respond"] = respond
    client = _client(session, graphql)
    await client.async_play_playlist("zone-1", "playlist-9")
    assert plays == 2
    assert "soundZoneAssignSource" in graphql["requests"][0]["query"]


async def test_play_playlist_assigns_then_starts(graphql, session) -> None:
    async def respond(body, request):
        return web.json_response({"data": {"ok": True}})

    graphql["respond"] = respond
    client = _client(session, graphql)
    await client.async_play_playlist("zone-1", "playlist-9")
    assert "soundZoneAssignSource" in graphql["requests"][0]["query"]
    assert graphql["requests"][0]["variables"] == {"soundZone": "zone-1", "source": "playlist-9"}
    assert "play(" in graphql["requests"][1]["query"]
    assert graphql["requests"][1]["variables"] == {"soundZone": "zone-1"}


async def test_search_playlists_uses_the_catalog(graphql, session) -> None:
    async def respond(body, request):
        assert body["variables"] == {"query": "jazz", "first": 25}
        assert "type: playlist" in body["query"]
        return web.json_response(
            {
                "data": {
                    "search": {
                        "edges": [
                            {"node": {"id": "pl1", "name": "Jazz Bar"}},
                            {"node": {}},
                        ]
                    }
                }
            }
        )

    graphql["respond"] = respond
    client = _client(session, graphql)
    found = await client.async_search_playlists("jazz")
    assert [(playlist.id, playlist.name) for playlist in found] == [("pl1", "Jazz Bar")]


async def test_favorite_targets_the_account(graphql, session) -> None:
    async def respond(body, request):
        return web.json_response({"data": {"addToMusicLibrary": {"musicLibrary": {"id": "lib"}}}})

    graphql["respond"] = respond
    client = _client(session, graphql)
    await client.async_favorite("acc-1", "playlist-9")
    await client.async_unfavorite("acc-1", "playlist-9")
    assert graphql["requests"][0]["variables"] == {"parent": "acc-1", "source": "playlist-9"}
    assert "addToMusicLibrary" in graphql["requests"][0]["query"]
    assert "removeFromMusicLibrary" in graphql["requests"][1]["query"]


async def test_snapshot_follows_library_pages(graphql, session) -> None:
    async def respond(body, request):
        if "LibraryPage" in body["query"]:
            assert body["variables"]["after"] == "cursor-1"
            return web.json_response(
                {
                    "data": {
                        "account": {
                            "musicLibrary": {
                                "playlists": {
                                    "pageInfo": {"hasNextPage": False},
                                    "edges": [{"node": {"id": "pl2", "name": "Evening"}}],
                                }
                            }
                        }
                    }
                }
            )
        return web.json_response(
            {
                "data": {
                    "me": {
                        "__typename": "User",
                        "name": "Ada",
                        "accounts": {
                            "pageInfo": {"hasNextPage": False},
                            "edges": [
                                {
                                    "node": {
                                        "id": "acc",
                                        "businessName": "Ada's Cafe",
                                        "musicLibrary": {
                                            "playlists": {
                                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                                "edges": [{"node": {"id": "pl1", "name": "Morning"}}],
                                            }
                                        },
                                        "locations": {"pageInfo": {"hasNextPage": False}, "edges": []},
                                    }
                                }
                            ],
                        },
                    }
                }
            }
        )

    graphql["respond"] = respond
    client = _client(session, graphql)
    snapshot = await client.async_snapshot()
    assert [playlist.name for playlist in snapshot.accounts["acc"].playlists] == ["Morning", "Evening"]
    assert snapshot.accounts["acc"].library_cursor is None


async def test_token_store_failure_keeps_the_session(graphql, session) -> None:
    async def on_tokens(tokens: Tokens) -> None:
        raise RuntimeError("disk full")

    async def respond(body, request):
        if "refreshLogin" in body["query"]:
            return web.json_response(
                {
                    "data": {
                        "refreshLogin": {
                            "token": "fresh",
                            "refreshToken": "fresher",
                            "expiresAt": "2099-01-01T00:00:00Z",
                        }
                    }
                }
            )
        return web.json_response({"data": {"ok": True}})

    graphql["respond"] = respond
    client = _client(
        session,
        graphql,
        Tokens("stale", "old-refresh", "2000-01-01T00:00:00Z", "user-1", "ada@example.com"),
        on_tokens=on_tokens,
    )
    assert await client.execute("query { ok }") == {"ok": True}
    assert client.tokens.access_token == "fresh"
