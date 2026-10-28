"""Tests for wishlist track abandonment (retry-count cap)."""

from unittest.mock import MagicMock, patch

from core.wishlist import processing


# ---------------------------------------------------------------------------
# _resolve_max_retry_count — config-driven cap
# ---------------------------------------------------------------------------


def test_resolve_max_retry_count_default():
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = processing._DEFAULT_MAX_RETRY_COUNT
        assert processing._resolve_max_retry_count() == 5


def test_resolve_max_retry_count_honors_config():
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = 3
        assert processing._resolve_max_retry_count() == 3
        cm.get.return_value = 10
        assert processing._resolve_max_retry_count() == 10


def test_resolve_max_retry_count_falls_back_on_garbage():
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = 'oops'
        assert processing._resolve_max_retry_count() == 5
        cm.get.return_value = 0
        assert processing._resolve_max_retry_count() == 5
        cm.get.return_value = -1
        assert processing._resolve_max_retry_count() == 5
        cm.get.side_effect = RuntimeError('boom')
        assert processing._resolve_max_retry_count() == 5


# ---------------------------------------------------------------------------
# abandon_exhausted_tracks — the main function
# ---------------------------------------------------------------------------


def _make_db(exhausted_tracks=None):
    db = MagicMock()
    db.get_exhausted_wishlist_tracks.return_value = exhausted_tracks or []
    db.remove_from_wishlist.return_value = True
    db.add_to_wishlist_ignore.return_value = True
    return db


def test_abandon_no_exhausted_tracks():
    db = _make_db([])
    assert processing.abandon_exhausted_tracks(db, profile_id=1) == 0
    db.remove_from_wishlist.assert_not_called()
    db.add_to_wishlist_ignore.assert_not_called()


def test_abandon_removes_and_ignores():
    tracks = [
        {'spotify_track_id': '111', 'track_name': 'Song A',
         'artist_name': 'Artist X', 'retry_count': 5},
        {'spotify_track_id': '222', 'track_name': 'Song B',
         'artist_name': 'Artist Y', 'retry_count': 7},
    ]
    db = _make_db(tracks)
    removed = processing.abandon_exhausted_tracks(db, profile_id=1, max_retries=5)

    assert removed == 2
    assert db.remove_from_wishlist.call_count == 2
    assert db.add_to_wishlist_ignore.call_count == 2

    # Verify ignore entries carry reason='exhausted'
    for call in db.add_to_wishlist_ignore.call_args_list:
        assert call.kwargs.get('reason') == 'exhausted' or call[1].get('reason') == 'exhausted'


def test_abandon_uses_configured_cap():
    db = _make_db([])
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = 3
        processing.abandon_exhausted_tracks(db, profile_id=1)
    db.get_exhausted_wishlist_tracks.assert_called_once_with(3, profile_id=1)


def test_abandon_explicit_max_retries_overrides_config():
    db = _make_db([])
    processing.abandon_exhausted_tracks(db, profile_id=1, max_retries=10)
    db.get_exhausted_wishlist_tracks.assert_called_once_with(10, profile_id=1)


def test_abandon_survives_db_query_error():
    db = MagicMock()
    db.get_exhausted_wishlist_tracks.side_effect = RuntimeError('db down')
    assert processing.abandon_exhausted_tracks(db, profile_id=1) == 0


def test_abandon_survives_per_track_remove_error():
    tracks = [
        {'spotify_track_id': '111', 'track_name': 'A',
         'artist_name': 'X', 'retry_count': 5},
        {'spotify_track_id': '222', 'track_name': 'B',
         'artist_name': 'Y', 'retry_count': 5},
    ]
    db = _make_db(tracks)
    db.remove_from_wishlist.side_effect = [RuntimeError('fail'), True]
    removed = processing.abandon_exhausted_tracks(db, profile_id=1, max_retries=5)
    # First track fails, second succeeds
    assert removed == 1
