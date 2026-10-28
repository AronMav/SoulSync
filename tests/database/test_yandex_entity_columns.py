"""Yandex enrichment columns belong to the canonical schema.

They used to exist only because core/yandex_worker.py self-healed them with
ALTER TABLE at startup, which meant a fresh database had no Yandex columns until
the worker happened to run, and the schema had two owners.
"""

from database.music_database import MusicDatabase


def _columns(db, table):
    with db._get_connection() as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_entity_tables_have_yandex_columns_on_a_fresh_database(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    for table in ("artists", "albums", "tracks"):
        cols = _columns(db, table)
        assert 'yandex_id' in cols, table
        assert 'yandex_match_status' in cols, table
        assert 'yandex_last_attempted' in cols, table


def test_similar_artists_has_a_yandex_id_column(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert 'similar_artist_yandex_id' in _columns(db, 'similar_artists')


def test_yandex_columns_do_not_pay_for_an_index_up_front(tmp_path):
    """3.6.0 builds enrichment indexes lazily, on a database that has data.

    A fresh database must not carry per-source index conversions before there
    is anything to index - the same rule tests/test_enrichment_queue_indexes.py
    enforces for the deezer columns. So the yandex columns ship unindexed and
    the queue's index pass adds them when it first needs them.
    """
    db = MusicDatabase(str(tmp_path / "music.db"))

    with db._get_connection() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}

    assert not [name for name in names if 'yandex' in name], sorted(names)


def test_worker_no_longer_owns_the_schema():
    """The worker must not carry a second, competing schema definition."""
    import inspect

    from core import yandex_worker

    assert not hasattr(yandex_worker.YandexWorker, '_ensure_schema')
    assert 'ALTER TABLE' not in inspect.getsource(yandex_worker)
