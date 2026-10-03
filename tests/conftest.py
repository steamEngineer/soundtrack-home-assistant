"""Pytest fixtures for the Soundtrack integration."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_socket
from dev.mock_soundtrack import EXPIRES, USER_ID, SoundtrackMock
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.soundtrack.const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _allow_local_sockets() -> None:
    """Home Assistant's test plugin replaces socket.socket with one that always raises.

    The Soundtrack stand-in binds a real port on 127.0.0.1. Re-enable sockets after
    that plugin's setup hook. DNS stays limited to local addresses by the plugin.
    """
    pytest_socket.enable_socket()


def soundtrack_entry() -> MockConfigEntry:
    """A saved Soundtrack session for the stand-in account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ada",
        unique_id=USER_ID,
        data={
            CONF_EMAIL: "ada@example.com",
            CONF_ACCESS_TOKEN: "access-1",
            CONF_REFRESH_TOKEN: "refresh-1",
            CONF_EXPIRES_AT: EXPIRES,
            CONF_USER_ID: USER_ID,
        },
    )


@pytest.fixture
async def mock_api() -> AsyncGenerator[SoundtrackMock]:
    """A Soundtrack API on a random local port, selected via SOUNDTRACK_API_URL."""
    server = SoundtrackMock()
    previous = os.environ.get("SOUNDTRACK_API_URL")
    await server.start()
    os.environ["SOUNDTRACK_API_URL"] = server.url
    try:
        yield server
    finally:
        if previous is None:
            os.environ.pop("SOUNDTRACK_API_URL", None)
        else:
            os.environ["SOUNDTRACK_API_URL"] = previous
        await server.stop()
