"""The deleted-track blacklist gates every AUTOMATIC wishlist re-add.

Placed on ``add_to_wishlist_detailed`` rather than on the watchlist scanner:
in 3.1 nine call sites reach the wishlist (the scanner, wishlist/service,
repair_worker seven times, discography_backfill, scan_watchlist_labels), so a
check in the scanner alone leaves the repair and backfill paths open — which is
exactly how a deleted track came back before.

Manual intent wins, mirroring the ignore-list convention already established
here: ``source_type='manual'`` or ``user_initiated=True`` bypasses the gate AND
clears the entry, because a user who explicitly asks for the track has changed
their mind and the blacklist should not argue.
"""

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _track(track_id="t1", name="Some Song", artist="Some Artist", album_id="alb1"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"id": album_id, "name": "Some Album", "images": []},
    }


def test_blacklisted_track_is_not_auto_readded(db):
    track = _track("t1", name="Gruppa Krovi", artist="Kino")
    assert db.add_to_wishlist(track, source_type="playlist") is True

    db.remove_from_wishlist("t1")
    db.add_deleted_blacklist("Kino", "Gruppa Krovi")

    assert db.add_to_wishlist(track, source_type="playlist") is False


def test_the_gate_reports_why_it_skipped(db):
    db.add_deleted_blacklist("Kino", "Gruppa Krovi")
    track = _track("t1", name="Gruppa Krovi", artist="Kino")

    outcome = db.add_to_wishlist_detailed(track, source_type="playlist")

    assert outcome["created"] is False
    assert outcome.get("reason") == "deleted-blacklist"


def test_an_unrelated_track_still_gets_added(db):
    """The gate must stay a gate, not a wall."""
    db.add_deleted_blacklist("Kino", "Gruppa Krovi")

    other = _track("t2", name="Zvezda po imeni Solntse", artist="Kino")
    assert db.add_to_wishlist(other, source_type="playlist") is True


def test_manual_add_bypasses_the_gate_and_clears_the_entry(db):
    db.add_deleted_blacklist("Kino", "Gruppa Krovi")
    track = _track("t1", name="Gruppa Krovi", artist="Kino")

    assert db.add_to_wishlist(track, source_type="manual") is True
    assert db.is_deleted_blacklisted("Kino", "Gruppa Krovi") is False


def test_user_initiated_add_bypasses_the_gate_and_clears_the_entry(db):
    db.add_deleted_blacklist("Kino", "Gruppa Krovi")
    track = _track("t1", name="Gruppa Krovi", artist="Kino")

    # An automatic 'album' add (e.g. repair_worker) is still blocked...
    assert db.add_to_wishlist(track, source_type="album") is False
    # ...but the explicit user click goes through and lifts the ban.
    assert db.add_to_wishlist(track, source_type="album", user_initiated=True) is True
    assert db.is_deleted_blacklisted("Kino", "Gruppa Krovi") is False


def test_multi_artist_spelling_still_blocks(db):
    """Navidrome joins collaborators with a bullet; the wishlist payload may
    carry only the first artist. The gate has to see through that."""
    db.add_deleted_blacklist("Fabrizio Paterlini • LoopArc", "Floating")
    track = _track("t3", name="Floating", artist="Fabrizio Paterlini")

    assert db.add_to_wishlist(track, source_type="playlist") is False


def test_gate_fails_open_when_the_lookup_errors(db, monkeypatch):
    """A broken blacklist must never wedge every download."""
    def boom(*args, **kwargs):
        raise RuntimeError("blacklist table on fire")

    monkeypatch.setattr(MusicDatabase, "is_deleted_blacklisted", boom)
    track = _track("t4", name="Some Song", artist="Some Artist")

    assert db.add_to_wishlist(track, source_type="playlist") is True
