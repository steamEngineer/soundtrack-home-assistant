"""Home Assistant fixtures for the Soundtrack integration."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncGenerator

import pytest
from dev.mock_soundtrack import SoundtrackMock
from homeassistant.core import HomeAssistant
from tests.hass_fixture import async_test_home_assistant


@pytest.fixture
async def mock_api() -> AsyncGenerator[SoundtrackMock]:
    """A Soundtrack API on a random port, selected via SOUNDTRACK_API_URL."""
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


@pytest.fixture
async def hass() -> AsyncGenerator[HomeAssistant]:
    """Home Assistant 2026.9 with this repo's custom component on the loader path."""
    config_dir = tempfile.mkdtemp()
    try:
        async for hass in async_test_home_assistant(config_dir):
            yield hass
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)
