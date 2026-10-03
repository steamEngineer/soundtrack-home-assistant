"""Constants for the Soundtrack integration."""

DOMAIN = "soundtrack"

API_URL = "https://api.soundtrackyourbrand.com/v2"

CONF_EMAIL = "email"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"
CONF_USER_ID = "user_id"

# Soundtrack volume is an integer step, not a percentage.
VOLUME_MAX = 16

# ponytail: poll instead of the graphql-ws nowPlaying subscription.
# 15s is enough for multi-minute background tracks and stays far under the
# API budget (50 tokens/sec refill). Upgrade path: subscribe to
# nowPlayingUpdate / playbackUpdate and refresh the card on each push.
SCAN_INTERVAL_SECONDS = 15

# One page covers a home and a small site. The snapshot logs when a
# connection is truncated. Library favorites page further, up to this cap.
PAGE_SIZE = 100
MAX_LIBRARY_PAGES = 4

TOKEN_REFRESH_MARGIN_SECONDS = 120
