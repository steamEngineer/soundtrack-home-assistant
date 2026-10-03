# Soundtrack for Home Assistant

Play and control [Soundtrack](https://www.soundtrackyourbrand.com/) sound zones from Home Assistant. Each zone becomes its own media player, so you can see what is on, pause it, skip, change the volume, and start a playlist on that speaker.

The music stays on the Soundtrack zone. Home Assistant is the remote.

Home Assistant 2026.4 or newer is required.

## Install

In HACS, add this repository as a custom integration, install Soundtrack, and restart Home Assistant.

To install by hand, copy `custom_components/soundtrack` into your config directory:

```text
config/custom_components/soundtrack/manifest.json
```

Restart, then open **Settings → Devices & services → Add integration → Soundtrack**.

## Sign in

Use the email and password for the Soundtrack account that should control the zones. The password is only used to sign in. Home Assistant keeps the session and renews it in the background.

When Soundtrack ends that session, Home Assistant asks you to enter the password again. The sign-in screen says which email it is reconnecting.

## What you can do

You get one media player per sound zone, named from the location and the zone.

| Control | What it does |
| --- | --- |
| Play / pause | Resume or pause that zone |
| Next track | Skip to the next song |
| Volume | A slider over Soundtrack's volume steps |
| Source | Playlists saved on the account. Choosing one starts it on that zone |
| Media browser | **Favorites** are the saved library. **Discover** is Soundtrack's browse categories |

The player shows the title, artists, album, artwork, and how far through the song you are, including while it is paused. The progress keeps moving while the zone is playing.

A change you make in Home Assistant shows up immediately. A change made in the Soundtrack app shows up within about 15 seconds.

Search in the media browser looks through Soundtrack's playlist catalog. Open a category under Discover to see the playlists in it.

### Start a playlist

In the media browser, open the zone and pick a playlist. You can also pick one from the source list on the player.

In an automation or script, target the zone:

```yaml
action: soundtrack.play_playlist
target:
  entity_id: media_player.front_bar
data:
  playlist_id: "UGxheWxpc3Q6..."
```

`media_player.play_media` does the same thing. Set the content type to `playlist` and use the playlist id as the content id.

The playlist id is the one shown when you browse, or the id from the Soundtrack app.

### Save a playlist

Saving a playlist puts it in that zone's Soundtrack library. It then shows up under Favorites and in the source list.

```yaml
action: soundtrack.favorite_playlist
target:
  entity_id: media_player.front_bar
data:
  playlist_id: "UGxheWxpc3Q6..."
```

`soundtrack.unfavorite_playlist` takes it back out.

Pause, skip, and volume also work as the usual media player actions.

## Limits

- Next track skips the song. Soundtrack cannot jump to a time inside a track.
- Volume moves in 17 steps, from 0 through 16. The current step is the `soundtrack_volume` attribute.
- A zone with nothing paired to it is off. A paired zone that cannot be reached is unavailable.
- Saved playlists load up to 400. If the library is larger than that, the rest is left out and Home Assistant notes it in the log.
- Soundtrack's terms cover the people who already run the account. This integration follows that: it controls your zones, and it is not a public jukebox.
