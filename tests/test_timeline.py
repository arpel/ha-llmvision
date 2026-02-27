"""Unit tests for timeline.py module."""

import datetime
import os
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest

from custom_components.llmvision.timeline import Timeline
from homeassistant.util import dt as dt_util


@dataclass
class MockEvent:
    """Mock event for testing."""
    uid: str
    title: str
    start: datetime.datetime
    end: datetime.datetime
    description: str
    key_frame: str = ""
    camera_name: str = ""
    label: str = ""


class TestTimeline:
    """Test Timeline class."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.data = {}
        hass.config = Mock()
        hass.config.path = Mock(return_value="/mock/path")
        hass.loop = Mock()
        hass.loop.run_in_executor = AsyncMock()
        hass.async_add_executor_job = AsyncMock()
        hass.async_create_task = lambda coro: None
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = Mock()
        entry.entry_id = "test_entry"
        entry.data = {"provider": "Settings", "retention_time": 7}
        entry.options = {}
        return entry

    @pytest.fixture
    def timeline_factory(self, tmp_path, monkeypatch):
        """Factory that builds isolated Timeline instances for retention tests."""

        base_path = tmp_path / "config"
        base_path.mkdir()

        real_makedirs = os.makedirs

        def safe_makedirs(path, exist_ok=False):
            if str(path).startswith("/media/llmvision"):
                return
            return real_makedirs(path, exist_ok=exist_ok)

        monkeypatch.setattr(
            "custom_components.llmvision.timeline.os.makedirs", safe_makedirs
        )

        def _build(retention=2, options=None):
            hass = Mock()
            hass.data = {}
            hass.config = Mock()
            hass.config.path = lambda *parts: str(base_path.joinpath(*parts))
            hass.loop = Mock()
            hass.loop.run_in_executor = AsyncMock()
            hass.async_add_executor_job = AsyncMock()
            hass.async_create_task = lambda coro: None

            entry = Mock()
            entry.entry_id = "timeline-entry"
            entry.data = {"provider": "Settings", "retention_time": retention}
            entry.options = options or {}

            timeline = Timeline(hass, entry)
            timeline._migrating = False
            return timeline

        return _build

    def test_retention_time_from_config(self, mock_hass, mock_config_entry):
        """Test retention time is read from config."""
        assert mock_config_entry.data["retention_time"] == 7

    def test_mock_event_creation(self):
        """Test MockEvent dataclass."""
        event = MockEvent(
            uid="test-uid",
            title="Test",
            start=datetime.datetime.now(),
            end=datetime.datetime.now(),
            description="Test desc"
        )

        assert event.uid == "test-uid"
        assert event.title == "Test"
        assert event.key_frame == ""

    async def _insert_rows(
        self,
        db_path: str,
        rows: list[tuple[str, str, str, str, str, str, str, str, str]],
    ):
        async with aiosqlite.connect(db_path) as db:
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO events (
                        uid, title, start, end, description,
                        key_frame, camera_name, category, label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            await db.commit()

    def _build_row(
        self, title: str, age_days: float
    ) -> tuple[str, str, str, str, str, str, str, str, str]:
        base = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=age_days
        )
        start = dt_util.as_local(base).isoformat()
        end = dt_util.as_local(base + datetime.timedelta(minutes=1)).isoformat()
        return (
            str(uuid.uuid4()),
            title,
            start,
            end,
            "",
            "",
            "",
            "",
            "",
        )

    async def _fetch_titles(self, db_path: str) -> list[str]:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT title FROM events ORDER BY title") as cursor:
                rows = await cursor.fetchall()
        return [row[0] for row in rows]

    @pytest.mark.asyncio
    async def test_retention_purges_old_events(self, timeline_factory):
        """Events older than retention_time days are purged when loading."""

        timeline = timeline_factory(retention=2)
        await timeline._initialize_db()

        rows = [
            self._build_row("expired", age_days=5),
            self._build_row("recent", age_days=0.1),
        ]
        await self._insert_rows(timeline._db_path, rows)

        await timeline.load_events()

        titles = await self._fetch_titles(timeline._db_path)
        assert titles == ["recent"]

    @pytest.mark.asyncio
    async def test_zero_retention_disables_purge(self, timeline_factory):
        """Setting retention_time to 0 leaves old events untouched."""

        timeline = timeline_factory(retention=0)
        await timeline._initialize_db()

        rows = [
            self._build_row("expired", age_days=10),
            self._build_row("recent", age_days=0.5),
        ]
        await self._insert_rows(timeline._db_path, rows)

        await timeline.load_events()

        titles = await self._fetch_titles(timeline._db_path)
        assert titles == ["expired", "recent"]
