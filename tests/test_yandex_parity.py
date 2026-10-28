"""Yandex Music must stay registered in every subsystem that knows about providers.

The fork carries Yandex as a first-class provider: metadata, downloads,
enrichment and new-release discovery. Migrating the fork onto upstream 3.1
re-ported it only partially, and nothing failed — the app booted, the worker ran
and the UI showed Yandex, so the missing registrations went unnoticed until an
audit found them.

This file is the guard: it fails loudly the next time an upstream merge drops one
of these registrations. Each assertion names the consequence of losing it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from database.music_database import MusicDatabase


# ── metadata registry ────────────────────────────────────────────────────────

def test_metadata_registry_knows_yandex():
    from core.metadata.registry import METADATA_SOURCE_LABELS, METADATA_SOURCE_PRIORITY

    assert 'yandex' in METADATA_SOURCE_PRIORITY
    assert METADATA_SOURCE_LABELS.get('yandex') == 'Yandex Music'


def test_metadata_registry_returns_a_yandex_client():
    """Without this branch, choosing Yandex as a metadata source yields no client."""
    from core.metadata.registry import get_client_for_source

    with patch('core.yandex_metadata_client.get_yandex_metadata_client') as factory:
        factory.return_value = MagicMock()
        assert get_client_for_source('yandex') is not None


# ── downloads ────────────────────────────────────────────────────────────────

def test_download_plugin_registry_offers_yandex():
    """Losing this makes Yandex disappear from the hybrid download order."""
    from core.download_plugins.registry import build_default_registry

    assert 'yandex' in build_default_registry().names()


def test_yandex_is_scored_as_a_streaming_source():
    """Losing this sends Yandex candidates to the Soulseek filename matcher,
    which parses username='yandex' as a path and rejects valid downloads."""
    from core.downloads.validation import _STREAMING_SOURCES

    assert 'yandex' in _STREAMING_SOURCES


def test_yandex_is_exempt_from_strict_pre_download_duration_checks():
    """Deliberate asymmetry: Yandex durations drift from the Spotify reference,
    so a strict pre-download check produced false rejections."""
    from core.downloads.validation import _STRICT_DURATION_SOURCES

    assert 'yandex' not in _STRICT_DURATION_SOURCES


# ── settings / connections ───────────────────────────────────────────────────

def test_yandex_verify_is_not_an_unknown_service():
    """Losing the run_service_test branch makes the Connections row answer
    'Unknown service.' when expanded."""
    from core import connection_test

    with patch('yandex_music.Client', side_effect=Exception('boom')):
        _, message = connection_test.run_service_test('yandex', {'token': 'tok'})

    assert 'Unknown service' not in message


# ── enrichment ───────────────────────────────────────────────────────────────

def test_enrichment_supports_all_three_yandex_entities():
    """Losing this breaks the Manage Workers breakdown / unmatched / manual-match
    endpoints for Yandex, which whitelist by service."""
    from core.enrichment.unmatched import SERVICE_ENTITY_SUPPORT

    assert SERVICE_ENTITY_SUPPORT.get('yandex') == ('artist', 'album', 'track')


def test_yandex_participates_in_the_enrichment_yield_policy():
    """Losing this stops the worker from yielding during downloads and discovery,
    so it competes with them for the Yandex API."""
    from core.enrichment.yield_policy import ALL_YIELD_WORKERS, API_CONTENTION_WORKERS

    assert 'yandex' in ALL_YIELD_WORKERS
    assert 'yandex' in API_CONTENTION_WORKERS


# ── watchlist ────────────────────────────────────────────────────────────────

def test_yandex_is_a_registered_watchlist_provider():
    """core/watchlist_sources.py is the single provider->column registry; losing
    the entry means an artist cannot be filed under Yandex at all."""
    from core.watchlist_sources import SOURCE_COLUMNS, artist_id_match_sql, source_column

    assert SOURCE_COLUMNS.get('yandex') == 'yandex_artist_id'
    assert source_column('yandex') == 'yandex_artist_id'
    assert 'yandex_artist_id' in artist_id_match_sql()


def test_scanner_maps_yandex_to_its_id_columns():
    """Losing these makes every scan re-resolve watched artists by name."""
    from core.watchlist_scanner import WatchlistScanner

    assert WatchlistScanner._artist_id_attribute_for_source('yandex') == 'yandex_artist_id'
    assert (
        WatchlistScanner._similar_artist_id_attribute_for_source('yandex')
        == 'similar_artist_yandex_id'
    )


# ── database schema ──────────────────────────────────────────────────────────

def test_schema_owns_every_yandex_column(tmp_path):
    """The schema — not the worker's ALTER TABLE — must create these."""
    db = MusicDatabase(str(tmp_path / "music.db"))

    def columns(table):
        with db._get_connection() as conn:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    for table in ("artists", "albums", "tracks"):
        assert {'yandex_id', 'yandex_match_status', 'yandex_last_attempted'} <= columns(table), table

    assert 'yandex_artist_id' in columns('watchlist_artists')
    assert 'similar_artist_yandex_id' in columns('similar_artists')


def test_watchlist_reads_the_yandex_column_end_to_end(tmp_path):
    """Guards the optional_columns trap: a column present in the schema but
    absent from the explicit SELECT list makes get_watchlist_artists raise
    KeyError and return [], leaving the scan with zero artists."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    assert db.add_artist_to_watchlist("ya-1", "Yandex Artist", profile_id=1, source="yandex")

    artists = db.get_watchlist_artists(profile_id=1)

    assert len(artists) == 1
    assert artists[0].yandex_artist_id == "ya-1"
