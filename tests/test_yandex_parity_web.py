"""Yandex parity checks that need the web_server module.

Split out of tests/test_yandex_parity.py because importing web_server builds the
whole Flask application, which is far heavier than the rest of the guard. The
checks belong to the same guard: both registries are whitelists, and a provider
missing from them is silently unsupported rather than broken-looking.
"""

from __future__ import annotations


def test_manual_match_knows_the_yandex_id_columns():
    """Losing this makes manual matching reject every Yandex entity as an
    invalid service/entity_type combination."""
    import web_server

    assert web_server._SERVICE_ID_COLUMNS.get('yandex') == {
        'artist': 'yandex_id', 'album': 'yandex_id', 'track': 'yandex_id',
    }


def test_yandex_service_config_requires_a_token():
    """Losing this makes the Connections row skip verification entirely."""
    import web_server

    assert web_server.SERVICE_CONFIG_REGISTRY.get('yandex') == {'required': ['token']}
