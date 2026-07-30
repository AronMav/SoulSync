"""Settings → Connections must be able to verify the Yandex Music token.

The row is registered in SERVICE_CONFIG_REGISTRY with a required ``token``, so
expanding it in the UI fires a verify. Without a branch in run_service_test the
verify answered "Unknown service." and the row looked broken.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core import connection_test


def _account(login):
    client = MagicMock()
    client.init.return_value = None
    client.me.account.login = login
    return client


def test_valid_token_reports_the_account_login():
    client = _account('c0dymaverick')

    with patch('yandex_music.Client', return_value=client) as ctor:
        ok, message = connection_test.run_service_test('yandex', {'token': 'tok-123'})

    assert ok is True
    assert 'c0dymaverick' in message
    ctor.assert_called_once_with('tok-123')
    client.init.assert_called_once()


def test_invalid_token_is_reported_as_a_failure():
    client = MagicMock()
    client.init.side_effect = Exception('Unauthorized')

    with patch('yandex_music.Client', return_value=client):
        ok, message = connection_test.run_service_test('yandex', {'token': 'bad'})

    assert ok is False
    assert 'Unauthorized' in message


def test_missing_token_is_reported_without_calling_the_api():
    with patch('yandex_music.Client') as ctor:
        ok, message = connection_test.run_service_test('yandex', {'token': ''})

    assert ok is False
    assert 'token' in message.lower()
    ctor.assert_not_called()


def test_yandex_is_not_an_unknown_service():
    """The regression this branch fixes: the service fell through the elif chain."""
    with patch('yandex_music.Client', side_effect=Exception('boom')):
        _, message = connection_test.run_service_test('yandex', {'token': 'tok'})

    assert 'Unknown service' not in message
