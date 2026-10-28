"""The deleted-track blacklist: "I deleted this, never fetch it again".

Matched by NAME, not by provider id: the trash button in the Navidrome player
knows a track's artist and title, but not its Spotify/Yandex/Deezer id. The
native blocklist cannot serve this — it matches tracks by id only, with a
name fallback for artists alone.
"""

from database.music_database import MusicDatabase


def _db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def test_table_exists_on_a_fresh_database(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(deleted_blacklist)")}
    assert {'profile_id', 'artist_norm', 'title_norm', 'artist', 'title'} <= cols


def test_add_then_match_then_remove(tmp_path):
    db = _db(tmp_path)

    assert db.add_deleted_blacklist('Kino', 'Gruppa Krovi', profile_id=1)
    assert db.is_deleted_blacklisted('Kino', 'Gruppa Krovi', profile_id=1)

    entries = db.get_deleted_blacklist(profile_id=1)
    assert len(entries) == 1
    assert entries[0]['artist'] == 'Kino'
    assert entries[0]['title'] == 'Gruppa Krovi'

    assert db.remove_deleted_blacklist('Kino', 'Gruppa Krovi', profile_id=1)
    assert not db.is_deleted_blacklisted('Kino', 'Gruppa Krovi', profile_id=1)


def test_matching_ignores_case_and_surrounding_space(tmp_path):
    db = _db(tmp_path)
    db.add_deleted_blacklist('Kino', 'Gruppa Krovi', profile_id=1)

    assert db.is_deleted_blacklisted('  KINO ', 'gruppa krovi', profile_id=1)


def test_unrelated_track_is_not_blacklisted(tmp_path):
    db = _db(tmp_path)
    db.add_deleted_blacklist('Kino', 'Gruppa Krovi', profile_id=1)

    assert not db.is_deleted_blacklisted('Kino', 'Zvezda po imeni Solntse', profile_id=1)
    assert not db.is_deleted_blacklisted('Nautilus', 'Gruppa Krovi', profile_id=1)


def test_multi_artist_separators_match_on_the_first_artist(tmp_path):
    """Real rows carry every spelling: Navidrome joins collaborators with a
    bullet, metadata sources use a comma or a semicolon. A blacklist entry
    stored under one spelling must still match the others."""
    db = _db(tmp_path)
    db.add_deleted_blacklist('Fabrizio Paterlini, LoopArc', 'Floating', profile_id=1)

    assert db.is_deleted_blacklisted('Fabrizio Paterlini • LoopArc', 'Floating', profile_id=1)
    assert db.is_deleted_blacklisted('Fabrizio Paterlini; Someone Else', 'Floating', profile_id=1)
    assert db.is_deleted_blacklisted('Fabrizio Paterlini', 'Floating', profile_id=1)


def test_ampersand_and_slash_are_not_split(tmp_path):
    """'Blank & Jones' and 'AC/DC' are single names — splitting them would
    blacklist unrelated artists."""
    db = _db(tmp_path)
    db.add_deleted_blacklist('Blank & Jones', 'Sunny Song', profile_id=1)

    assert not db.is_deleted_blacklisted('Blank', 'Sunny Song', profile_id=1)
    assert db.is_deleted_blacklisted('Blank & Jones', 'Sunny Song', profile_id=1)


def test_entry_without_an_artist_matches_the_title_alone(tmp_path):
    db = _db(tmp_path)
    db.add_deleted_blacklist('', 'Orphan Title', profile_id=1)

    assert db.is_deleted_blacklisted('Anybody At All', 'Orphan Title', profile_id=1)


def test_adding_twice_keeps_one_row(tmp_path):
    db = _db(tmp_path)
    db.add_deleted_blacklist('Kino', 'Gruppa Krovi', profile_id=1)
    db.add_deleted_blacklist('KINO', 'gruppa krovi', profile_id=1)

    assert len(db.get_deleted_blacklist(profile_id=1)) == 1


def test_profiles_are_isolated(tmp_path):
    db = _db(tmp_path)
    db.add_deleted_blacklist('Kino', 'Gruppa Krovi', profile_id=1)

    assert not db.is_deleted_blacklisted('Kino', 'Gruppa Krovi', profile_id=2)
