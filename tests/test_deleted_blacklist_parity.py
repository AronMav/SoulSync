"""Guard: the delete → blacklist chain must survive the next upstream merge.

This feature was already lost once. The migration onto upstream 3.1 kept the
trash button working — the file was still deleted — but silently dropped every
piece that remembered the deletion, leaving 131 rows in the live database that
no code referenced any more. Nothing failed loudly, so it went unnoticed.

Each assertion below names the link it protects. The chain is only as good as
its weakest one: a working button that forgets, or a remembering database that
nothing consults, both put the deleted track back.
"""

from __future__ import annotations

from pathlib import Path

from database.music_database import MusicDatabase


def test_database_exposes_the_blacklist_api():
    """Losing these makes the delete route silently stop remembering."""
    for method in ('add_deleted_blacklist', 'is_deleted_blacklisted',
                   'get_deleted_blacklist', 'remove_deleted_blacklist'):
        assert callable(getattr(MusicDatabase, method, None)), method


def test_schema_owns_the_blacklist_table(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(deleted_blacklist)")}
    assert {'profile_id', 'artist_norm', 'title_norm', 'artist', 'title'} <= cols


def test_the_wishlist_gate_is_actually_wired(tmp_path):
    """The link that was missing before: a remembered track must be refused by
    the shared wishlist entry point, not merely stored somewhere."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_deleted_blacklist("Kino", "Gruppa Krovi")

    outcome = db.add_to_wishlist_detailed(
        {"id": "t1", "name": "Gruppa Krovi", "artists": [{"name": "Kino"}],
         "album": {"id": "a1", "name": "Album", "images": []}},
        source_type="playlist",
    )

    assert outcome["created"] is False
    assert outcome.get("reason") == "deleted-blacklist"


def _nd_delete_script() -> str:
    script = Path(__file__).resolve().parents[1] / "webui" / "static" / "nd-delete.js"
    return script.read_text(encoding="utf-8")


def test_the_browser_script_sends_the_track_identity():
    """The button knows artist and title; if it stops sending them the backend
    has nothing to record and the blacklist quietly stays empty."""
    text = _nd_delete_script()

    assert "artist: song.artist" in text
    assert "title: song.title" in text


def test_the_player_resolves_the_track_at_click_time():
    """Regression guard for a delete-the-wrong-file bug.

    The control row is built once and never rebuilt when the player advances,
    so an id captured while creating the button goes stale immediately: the
    button then deletes whatever had been playing when it first appeared. The
    click handler must ask what is playing now.
    """
    text = _nd_delete_script()

    assert "function currentPlayingId()" in text
    click_handler = text[text.index("btn.onclick"):]
    assert "currentPlayingId()" in click_handler[:400], (
        "the click handler must resolve the playing track itself, "
        "not reuse an id captured when the button was created"
    )


def test_the_delete_and_blacklist_routes_are_registered():
    """Losing a route turns the button into a 404 — or, worse, leaves deletion
    working while review and un-blocking disappear."""
    import web_server

    rules = {r.rule for r in web_server.app.url_map.iter_rules()}
    for route in ('/api/nd/delete', '/api/nd/delete-album',
                  '/api/nd/blacklist', '/api/nd/blacklist/remove'):
        assert route in rules, route
