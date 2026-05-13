"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest

from vividscripts_mcp.adapters import MockBackend
from vividscripts_mcp.models import ProjectSettings


@pytest.fixture
def backend() -> MockBackend:
    """A fresh MockBackend per test."""
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def user_id() -> str:
    return "user-alpha"


@pytest.fixture
def other_user_id() -> str:
    return "user-beta"


@pytest.fixture
def settings() -> ProjectSettings:
    return ProjectSettings(style="dark_cinematic", voice="female", dimension="landscape")


@pytest.fixture
def sample_story() -> str:
    return (
        "I lived alone for years. Or so I thought.\n\n"
        "One night, the floor in the spare room creaked. I assumed the house was settling. "
        "But then I heard footsteps. Steady. Deliberate. Coming from the second bedroom — "
        "the one I'd nailed shut when I moved in."
    )
