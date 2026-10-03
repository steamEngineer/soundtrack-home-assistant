# Soundtrack for Home Assistant

Control [Soundtrack](https://www.soundtrackyourbrand.com/) sound zones from Home Assistant. Each zone becomes a media player: you can see what is playing, pause, skip, set the volume, browse playlists, save the ones you want, and start one on a specific speaker.

This is a Home Assistant integration, not a Music Assistant provider. Soundtrack already plays on its own sound zones. The integration tells those zones what to do. It does not pull a stream into another player.

## Install

Copy `custom_components/soundtrack` into your Home Assistant config directory, so you have:

```text
config/custom_components/soundtrack/manifest.json
```

Restart Home Assistant, then go to **Settings → Devices & services → Add integration → Soundtrack**.

Home Assistant 2026.4 or newer is required. The integration is also laid out for HACS (`hacs.json`).

## Sign in

Soundtrack does not offer an OAuth consent screen for this API. The integration signs in with the email and password for the Soundtrack user that should control the zones.

The password is used once and is not stored. Soundtrack returns an access token and a refresh token. Home Assistant keeps those in the config entry and exchanges the refresh token for a new access token before it expires, and again if a request comes back unauthorized. That session lasts until Soundtrack rejects the refresh token.

When that happens, Home Assistant raises a repair and asks for the password again. It does not keep retrying a stored password. A failed refresh is a sign-out, not a blip to hammer through.

## What you get

One media player per sound zone, named from the location and the zone.

| Control | What it does |
| --- | --- |
| Play / pause | Resume or pause that zone |
| Next track | Skip to the next song |
| Volume | Slider mapped onto Soundtrack's 0–16 steps |
| Source | Saved playlists for that zone's account. Picking one starts it |
| Media browser | **Favorites** are the account music library. **Discover** is Soundtrack's browse categories |

The player shows the track title, artists, album, artwork, and how far into the track you are while it is playing. Changes you make in Home Assistant refresh immediately. Changes made in the Soundtrack app show up on the next poll, about every 15 seconds.

The media browser search box queries Soundtrack's playlist catalog. Favorites are the playlists saved on the account. Discover is the browse categories.

### Play a playlist on one speaker

From the media browser, open the zone and choose a playlist.

Or call the service and target the zone:

```yaml
action: soundtrack.play_playlist
target:
  entity_id: media_player.front_bar
data:
  playlist_id: "UGxheWxpc3Q6..."
```

`media_player.play_media` does the same thing. Use `media_content_type: playlist` and the playlist id as `media_content_id`.

### Favorite a playlist

Favoriting adds the playlist to that zone's Soundtrack account library. It then appears under Favorites and in the source list.

```yaml
action: soundtrack.favorite_playlist
target:
  entity_id: media_player.front_bar
data:
  playlist_id: "UGxheWxpc3Q6..."
```

`soundtrack.unfavorite_playlist` removes it. The playlist id is the id shown when you browse, or the id from the Soundtrack app.

Pause, skip, and volume also work as the normal media player actions (`media_player.media_pause`, `media_player.media_next_track`, `media_player.volume_set`).

## Limits

- Fast-forward is skip. Soundtrack's API has no seek inside a track.
- Volume has 17 steps (0 through 16), not a smooth percentage. The raw step is on the `soundtrack_volume` attribute.
- A zone with no paired device is off. A paired zone that is offline is unavailable.
- Accounts, locations, and zones are loaded 100 at a time. Saved playlists follow further pages, up to 400. The log says when something was cut off.
- Soundtrack's own API terms do not allow a visitor-facing jukebox. This integration is for the people who already control the account.

## Development

Home Assistant 2026.4 and newer needs Python 3.14.2 or newer. Tests run inside a slim Home Assistant 2026.9 runtime built from the published wheel. Core's `tests/common.py` is not in that wheel, and `pytest-homeassistant-custom-component` still pins Home Assistant 2025.1, so the fixture in `tests/hass_fixture.py` follows the 2026.9.4 test helper instead.

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements-dev.txt
# The browser UI needs the frontend build that matches this Home Assistant release.
uv pip install home-assistant-frontend==20260826.7
.venv/bin/pytest
```

`pytest` covers the GraphQL client and, in Home Assistant itself, the config flow, reauth, zone setup, playback, favorites, browse, and search.

To click through the UI, run the local Soundtrack stand-in and a Home Assistant pointed at it. `SOUNDTRACK_API_URL` is optional. Leave it unset and the integration uses `https://api.soundtrackyourbrand.com/v2`.

```bash
mkdir -p config/custom_components config/.storage
ln -sfn "$PWD/custom_components/soundtrack" config/custom_components/soundtrack
cp dev/configuration.yaml config/configuration.yaml
# Home Assistant 2026 treats an http: port in YAML as a five-minute trial,
# then restarts on 8123. Seed the stable port before the first boot.
cp dev/http.storage.json config/.storage/http
SOUNDTRACK_API_URL=http://127.0.0.1:43124/ .venv/bin/python dev/mock_soundtrack.py
```

In a second shell:

```bash
SOUNDTRACK_API_URL=http://127.0.0.1:43124/ .venv/bin/python -m homeassistant --config config
```

Assist is a built-in platform, so this Home Assistant also needs `pymicro-vad` and `pyspeex-noise`. On this image `c++` is clang and it looks for the GCC 14 headers:

```bash
sudo apt-get install -y g++ libstdc++-14-dev
uv pip install pymicro-vad==1.0.1 pyspeex-noise==1.0.2
```

Open http://127.0.0.1:43123. On the stand-in, sign in as `ada@example.com` with password `soundtrack`. That account has one sound zone, Front Bar, playing Nightshift. The password is only valid for the stand-in.
