"""The watchlist scanner must resolve and cache Yandex artist ids.

Without these maps the scanner had no storage column for yandex, so every scan
re-resolved all watched artists by name over the network instead of reusing the
id the enrichment worker had already stored.
"""

from unittest.mock import MagicMock

from core.watchlist_scanner import WatchlistScanner
from core.watchlist_sources import SOURCE_COLUMNS


def test_artist_id_attribute_covers_yandex():
    assert WatchlistScanner._artist_id_attribute_for_source('yandex') == 'yandex_artist_id'


def test_artist_id_attribute_delegates_to_the_single_registry():
    """The scanner must not keep a second copy of the provider->column map:
    that duplication is why yandex went missing on the 3.1 port."""
    for source, column in SOURCE_COLUMNS.items():
        assert WatchlistScanner._artist_id_attribute_for_source(source) == column, source


def test_unknown_source_still_has_no_attribute():
    assert WatchlistScanner._artist_id_attribute_for_source('beatport') is None


def test_similar_artist_id_attribute_covers_yandex():
    assert (
        WatchlistScanner._similar_artist_id_attribute_for_source('yandex')
        == 'similar_artist_yandex_id'
    )


def test_caching_a_yandex_id_writes_the_column_and_the_dataclass():
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    # ``database`` is a lazy read-only property backed by ``_database``.
    scanner._database = MagicMock()
    artist = MagicMock()
    artist.id = 42

    scanner._cache_watchlist_artist_source_id(artist, 'yandex', 'ya-1')

    scanner.database.update_watchlist_yandex_id.assert_called_once_with(42, 'ya-1')
    assert artist.yandex_artist_id == 'ya-1'
