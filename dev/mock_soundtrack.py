"""Local Soundtrack GraphQL stand-in for the Home Assistant lab.

The real API stays at api.soundtrackyourbrand.com. Point a development
Home Assistant at this process with SOUNDTRACK_API_URL.

Sign in with ada@example.com / soundtrack. The password wrong-password is
rejected. other@example.com signs in as a different user.
"""

from __future__ import annotations

import argparse
from typing import Any

from aiohttp import web

USER_ID = "user-1"
OTHER_USER_ID = "user-other"
ACCOUNT_ID = "account-1"
ZONE_ID = "zone-bar"
EXPIRES = "2099-01-01T00:00:00Z"

_CATALOG = {
    "playlist-morning": "Morning",
    "playlist-evening": "Evening",
    "playlist-jazz": "Jazz After Dark",
}


class SoundtrackMock:
    """One in-memory Soundtrack account."""

    def __init__(self) -> None:
        self.volume = 8
        self.state = "playing"
        self.source_id = "playlist-morning"
        self.track_title = "Nightshift"
        self.track_artist = "Commodores"
        self.library = ["playlist-morning", "playlist-evening"]
        self.reject_refresh = False
        self.hide_zone = False
        self.signed_in = (USER_ID, "Ada", "ada@example.com")
        self.url = ""
        self._runner: web.AppRunner | None = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        app = web.Application()
        app.router.add_get("/", self._health)
        app.router.add_post("/", self._graphql)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        sockets = site._server.sockets if site._server else None
        bound = sockets[0].getsockname()[1] if sockets else port
        self.url = f"http://{host}:{bound}/"
        return self.url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "zone": ZONE_ID, "state": self.state})

    async def _graphql(self, request: web.Request) -> web.Response:
        body = await request.json()
        query = body.get("query") or ""
        variables = body.get("variables") or {}
        if "loginUser" in query:
            return self._login(variables)
        if self.reject_refresh or not (request.headers.get("Authorization") or "").startswith(
            "Bearer "
        ):
            return _auth_error()
        if "refreshLogin" in query:
            return web.json_response(
                {
                    "data": {
                        "refreshLogin": {
                            "token": "access-refreshed",
                            "refreshToken": "refresh-refreshed",
                            "expiresAt": EXPIRES,
                        }
                    }
                }
            )
        if "addToMusicLibrary" in query:
            playlist_id = variables.get("source")
            if playlist_id and playlist_id not in self.library:
                self.library.append(playlist_id)
            return web.json_response(
                {"data": {"addToMusicLibrary": {"musicLibrary": {"id": ACCOUNT_ID}}}}
            )
        if "removeFromMusicLibrary" in query:
            playlist_id = variables.get("source")
            self.library = [item for item in self.library if item != playlist_id]
            return web.json_response(
                {"data": {"removeFromMusicLibrary": {"musicLibrary": {"id": ACCOUNT_ID}}}}
            )
        if "soundZoneAssignSource" in query:
            self.source_id = variables.get("source") or self.source_id
            return web.json_response({"data": {"soundZoneAssignSource": {"soundZones": [ZONE_ID]}}})
        if "setVolume" in query:
            self.volume = int(variables.get("volume") or 0)
            return web.json_response({"data": {"setVolume": {"volume": self.volume}}})
        if "skipTrack" in query:
            self.track_title = "Easy"
            self.track_artist = "Commodores"
            self.state = "playing"
            return web.json_response({"data": {"skipTrack": {"status": "playing"}}})
        if "pause(" in query:
            self.state = "paused"
            return web.json_response({"data": {"pause": {"status": "paused"}}})
        if "play(" in query:
            self.state = "playing"
            return web.json_response({"data": {"play": {"status": "playing"}}})
        if "editorialBrowse" in query:
            playlist = _playlist("playlist-jazz")
            playlist["__typename"] = "Playlist"
            return web.json_response(
                {
                    "data": {
                        "editorialBrowse": {
                            "title": "Jazz",
                            "sections": {
                                "edges": [
                                    {
                                        "node": {
                                            "title": "Popular",
                                            "items": {
                                                "edges": [
                                                    {"node": playlist},
                                                    {
                                                        "node": {
                                                            "__typename": "BrowseCategory",
                                                            "id": "lounge",
                                                            "name": "Lounge",
                                                        }
                                                    },
                                                ]
                                            },
                                        }
                                    }
                                ]
                            },
                        }
                    }
                }
            )
        if "browseCategories" in query:
            return web.json_response({"data": {"browseCategories": _edges([_category()])}})
        if "browseCategory" in query:
            return web.json_response(
                {
                    "data": {
                        "browseCategory": {
                            "id": "category-jazz",
                            "name": "Jazz",
                            "playlists": _edges([_playlist("playlist-jazz")]),
                        }
                    }
                }
            )
        if "search(" in query:
            needle = str(variables.get("query") or "").casefold()
            found = [
                _playlist(playlist_id)
                for playlist_id, name in _CATALOG.items()
                if needle in name.casefold()
            ]
            return web.json_response({"data": {"search": _edges(found)}})
        if "account(id:" in query:
            return web.json_response(
                {
                    "data": {
                        "account": {
                            "musicLibrary": {
                                "playlists": _edges(
                                    [_playlist(playlist_id) for playlist_id in self.library]
                                )
                            }
                        }
                    }
                }
            )
        if "accounts(" in query:
            return web.json_response({"data": {"me": self._user(with_accounts=True)}})
        return web.json_response({"data": {"me": self._user(with_accounts=False)}})

    def _login(self, variables: dict[str, Any]) -> web.Response:
        email = str(variables.get("email") or "")
        password = str(variables.get("password") or "")
        if password == "wrong-password":
            return web.json_response({"errors": [{"message": "Invalid credentials"}]})
        other = password == "other-account" or email.casefold() == "other@example.com"
        user_id = OTHER_USER_ID if other else USER_ID
        name = "Someone Else" if other else "Ada"
        self.signed_in = (user_id, name, email)
        return web.json_response(
            {
                "data": {
                    "loginUser": {
                        "token": "access-1",
                        "refreshToken": "refresh-1",
                        "expiresAt": EXPIRES,
                        "userId": user_id,
                    },
                    "me": {"__typename": "User", "id": user_id, "name": name, "email": email},
                }
            }
        )

    def _user(self, *, with_accounts: bool) -> dict[str, Any]:
        user_id, name, email = self.signed_in
        user: dict[str, Any] = {
            "__typename": "User",
            "id": user_id,
            "name": name,
            "email": email,
        }
        if not with_accounts:
            return user
        user["accounts"] = _edges(
            [
                {
                    "id": ACCOUNT_ID,
                    "businessName": "Ada's Cafe",
                    "musicLibrary": {
                        "playlists": _edges(
                            [
                                {"id": playlist_id, "name": _CATALOG[playlist_id]}
                                for playlist_id in self.library
                            ],
                            more=False,
                        )
                    },
                    "locations": _edges(
                        [
                            {
                                "id": "location-front",
                                "name": "Front",
                                "soundZones": _edges([] if self.hide_zone else [self._zone()]),
                            }
                        ]
                    ),
                }
            ]
        )
        return user

    def _zone(self) -> dict[str, Any]:
        return {
            "id": ZONE_ID,
            "name": "Bar",
            "online": True,
            "isPaired": True,
            "playback": {
                "state": self.state,
                "volume": self.volume,
                "progress": {"progressMs": 42000, "updatedAt": "2026-10-03T03:00:42Z"},
                "current": {
                    "start": "2026-10-03T03:00:00Z",
                    "playable": {
                        "__typename": "Track",
                        "title": self.track_title,
                        "durationMs": 180000,
                        "artists": [{"name": self.track_artist}],
                    },
                },
                "playFrom": {
                    "__typename": "Playlist",
                    "id": self.source_id,
                    "name": _CATALOG.get(self.source_id, "Playlist"),
                },
            },
            "nowPlaying": {
                "startedAt": "2026-10-03T03:00:00Z",
                "track": {
                    "title": self.track_title,
                    "name": self.track_title,
                    "durationMs": 180000,
                    "artists": [{"name": self.track_artist}],
                    "album": {"title": "Nightshift", "name": "Nightshift"},
                    "display": {"image": {"placeholder": "https://cdn.example/%w/%h", "sizes": {}}},
                },
            },
        }


def _edges(nodes: list[dict[str, Any]], *, more: bool = False) -> dict[str, Any]:
    return {
        "pageInfo": {"hasNextPage": more, "endCursor": None},
        "edges": [{"node": node} for node in nodes],
    }


def _playlist(playlist_id: str) -> dict[str, Any]:
    return {
        "id": playlist_id,
        "name": _CATALOG.get(playlist_id, "Playlist"),
        "description": None,
        "display": {
            "image": {"sizes": {"thumbnail": {"url": f"https://cdn.example/{playlist_id}.jpg"}}}
        },
    }


def _category() -> dict[str, Any]:
    return {
        "id": "category-jazz",
        "name": "Jazz",
        "image": {"large": {"url": "https://cdn.example/%w/%h/jazz"}},
    }


def _auth_error() -> web.Response:
    return web.json_response(
        {"errors": [{"message": "invalid token", "extensions": {"code": "UNAUTHENTICATED"}}]},
        status=401,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Soundtrack GraphQL stand-in")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=43124)
    args = parser.parse_args()
    app_mock = SoundtrackMock()

    async def _app() -> web.Application:
        application = web.Application()
        application["mock"] = app_mock

        async def health(_request: web.Request) -> web.Response:
            return await app_mock._health(_request)

        async def graphql(request: web.Request) -> web.Response:
            return await app_mock._graphql(request)

        application.router.add_get("/", health)
        application.router.add_post("/", graphql)
        return application

    print(f"Soundtrack stand-in on http://{args.host}:{args.port}/")
    print("Sign in as ada@example.com / soundtrack")
    web.run_app(_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
