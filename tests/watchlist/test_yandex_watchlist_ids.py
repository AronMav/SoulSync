"""Yandex is a first-class Watchlist provider: its artist ids get their own column.

core/watchlist_sources.py is documented as "the one place that maps a Watchlist
provider name to its id column". Yandex was missing from it, so an artist could
not be filed under Yandex and a scan could not resolve one.
"""

from core.watchlist_sources import artist_id_match_sql, normalize_source, source_column
from database.music_database import MusicDatabase


def test_yandex_is_a_known_watchlist_source():
    assert normalize_source('yandex') == 'yandex'
    assert normalize_source('Yandex') == 'yandex'
    assert normalize_source('yandex_music') == 'yandex'
    assert source_column('yandex') == 'yandex_artist_id'


def test_yandex_column_participates_in_id_lookup():
    """add / update / remove all match a bare id through this SQL, so a provider
    missing here is a provider that can never be found again."""
    assert 'yandex_artist_id' in artist_id_match_sql()


def test_watchlist_artist_can_store_a_yandex_match(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_artist_to_watchlist(
        "9489746",
        "100 Atmosfer",
        profile_id=1,
        source="yandex",
    )

    artists = db.get_watchlist_artists(profile_id=1)

    assert len(artists) == 1
    assert artists[0].artist_name == "100 Atmosfer"
    assert artists[0].yandex_artist_id == "9489746"
    assert artists[0].spotify_artist_id is None


def test_yandex_match_can_be_added_to_an_existing_artist(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_artist_to_watchlist("sp-1", "Linked Artist", profile_id=1, source="spotify")
    assert db.add_artist_to_watchlist("ya-1", "Linked Artist", profile_id=1, source="yandex")

    artists = db.get_watchlist_artists(profile_id=1)

    assert len(artists) == 1
    assert artists[0].spotify_artist_id == "sp-1"
    assert artists[0].yandex_artist_id == "ya-1"


def test_yandex_match_supports_update_presence_and_removal(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-1", "Removable Artist", profile_id=1, source="spotify")
    artist = db.get_watchlist_artists(profile_id=1)[0]

    assert db.update_watchlist_yandex_id(artist.id, "ya-1")
    assert db.is_artist_in_watchlist("ya-1", profile_id=1)
    assert db.remove_artist_from_watchlist("ya-1", profile_id=1)
    assert db.get_watchlist_artists(profile_id=1) == []


def test_yandex_ids_backfill_from_the_library_by_name(tmp_path):
    """The enrichment worker writes artists.yandex_id; this bridge lets those
    matches surface on the watchlist without waiting for a scan."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-1", "Library Matched Artist", profile_id=1, source="spotify")
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, yandex_id) VALUES (?, ?, ?)",
            ("library-artist-1", "Library Matched Artist", "ya-library-1"),
        )
        conn.commit()

    assert db.backfill_watchlist_yandex_ids_from_library(profile_id=1) == 1

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.yandex_artist_id == "ya-library-1"


def test_yandex_ids_backfill_from_the_library_by_linked_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-1", "Different Watchlist Name", profile_id=1, source="spotify")
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, spotify_artist_id, yandex_id) VALUES (?, ?, ?, ?)",
            ("library-artist-1", "Canonical Library Name", "sp-1", "ya-library-1"),
        )
        conn.commit()

    assert db.backfill_watchlist_yandex_ids_from_library(profile_id=1) == 1

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.yandex_artist_id == "ya-library-1"
