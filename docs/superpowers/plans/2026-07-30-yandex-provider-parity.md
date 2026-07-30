# Yandex Provider Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Восстановить паритет Yandex Music с остальными провайдерами SoulSync после миграции форка на upstream 3.1.9 и закрепить его тестом, который упадёт при следующей потере.

**Architecture:** Четыре независимые точки интеграции + один сквозной тест-страж. Правки аддитивные: регистрация yandex в существующих реестрах и картах, каноническая схема БД вместо самолечения в воркере. Ни одна правка не меняет поведение других провайдеров.

**Tech Stack:** Python 3.11 (в образе) / 3.13 (хост), Flask, SQLite, pytest 9.0.3, Docker Compose. Репозиторий `/home/aronmav/SoulSync` на сервере 188.246.224.118, ветка `main`, форк `AronMav/SoulSync` поверх upstream `Nezreka/SoulSync` 3.1.9.

**Спека:** `docs/superpowers/specs/2026-07-30-yandex-provider-parity-design.md`

## Global Constraints

- Работа только в `/home/aronmav/SoulSync` на сервере 188.246.224.118, ветка `main`. Локальный `D:\GIT\SoulSync` — разошедшаяся старая ветка `update-upstream-3.1`, её не трогать.
- Каждая задача заканчивается коммитом. Автор коммита: `AronMav <aronmav13@gmail.com>`. В сообщение добавлять `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Не мёржить upstream, не менять `metadata.fallback_source`, не поднимать yandex в `METADATA_SOURCE_PRIORITY`.
- Не трогать кнопку удаления в Navidrome и `deleted_blacklist` — отдельная спека.
- `yandex` НЕ добавляется в `_strict_duration_sources` — это осознанное решение, длительности Яндекса расходятся со Spotify-эталоном.
- Правило колонки в `watchlist_artists`: CREATE + миграция + `optional_columns` + dataclass. Пропуск `optional_columns` даёт `KeyError` → `get_watchlist_artists` возвращает `[]` → скан находит 0 артистов.
- Прогон тестов — в одноразовом контейнере из образа `soulsync-custom:yandex` (в нём runtime-зависимости в `/opt/venv`); на хосте pytest не установлен.

## Команда прогона тестов

Во всех задачах ниже под «Run:» подразумевается эта обвязка (репозиторий монтируется в `/repo`, pytest ставится в одноразовый контейнер):

```bash
docker run --rm -v /home/aronmav/SoulSync:/repo -w /repo --entrypoint sh \
  soulsync-custom:yandex -c \
  '/opt/venv/bin/pip install -q pytest==9.0.3 >/dev/null 2>&1; PYTHONPATH=/repo /opt/venv/bin/python -m pytest <АРГУМЕНТЫ>'
```

Первый прогон качает pytest (нужен интернет из контейнера — образ ходит через xray-прокси, заданный в compose; при `docker run` прокси не наследуется, поэтому передавать `-e HTTP_PROXY=http://10.10.1.37:1081 -e HTTPS_PROXY=http://10.10.1.37:1081` при необходимости). Если установка pytest не проходит — резервный путь из спеки: venv на хосте из `requirements-dev.txt`.

## Отклонения от спеки (механизм, не объём)

Разведка кодовой базы 3.1 нашла три места, где реализация должна отличаться от описанной в спеке. Цель и объём те же, меняется механизм — в сторону конвенций 3.1.

1. **Реестр источников watchlist.** Спека предлагала добавить yandex в карту `_artist_id_attribute_for_source` внутри сканера. В 3.1 появился `core/watchlist_sources.py` — «the one place that maps a Watchlist provider name to its id column» (`SOURCE_COLUMNS`), питающий add / update / remove / lookup через `artist_id_match_sql`. Yandex регистрируется там, а дублирующая карта в сканере делегирует в `source_column`. Это устраняет сам класс расхождения, из-за которого дырка и возникла.
2. **Мост «библиотека → watchlist».** Вместо бespoke-метода `_library_yandex_id` в сканере — `backfill_watchlist_yandex_ids_from_library` в БД, зеркало существующего `backfill_watchlist_musicbrainz_ids_from_library`. Тот же эффект (ID из `artists.yandex_id`, заполненные enrichment-воркером, попадают в watchlist), но по готовой конвенции и с готовым тестовым шаблоном.
3. **Порядок задач.** Спека перечисляла дырки 1→2→3→4; здесь схема БД идёт до сканера, потому что сканер потребляет `update_watchlist_yandex_id` и колонку `yandex_artist_id`. Шесть кодовых коммитов вместо пяти: работа с БД разделена на «колонки сущностей» и «плумбинг watchlist» — это разные единицы с разными тестами.

Дополнительно: `_STREAMING_SOURCES` и `_STRICT_DURATION_SOURCES` поднимаются на уровень модуля. Сейчас это локальные переменные внутри `get_valid_candidates`, и тест-страж физически не может проверить их состав.

## File Structure

**Изменяемые файлы:**

| Файл | Ответственность | Что меняется |
| --- | --- | --- |
| `core/downloads/validation.py` | отбор кандидатов загрузки | два кортежа-константы на уровень модуля, `yandex` в стриминговый |
| `core/connection_test.py` | проверка подключений из Settings | ветка `yandex` в `run_service_test` |
| `core/watchlist_sources.py` | единственная карта провайдер → колонка watchlist | `yandex` в `SOURCE_COLUMNS`, алиас `yandex_music` |
| `database/music_database.py` | схема и доступ к данным | `_add_yandex_columns`, колонка `yandex_artist_id`, `similar_artist_yandex_id`, dataclass-поля, `update_watchlist_yandex_id`, `backfill_watchlist_yandex_ids_from_library` |
| `core/yandex_worker.py` | enrichment-воркер Yandex | удаление `_ensure_schema` (схема канонизирована) |
| `core/watchlist_scanner.py` | скан новинок | делегирование в `source_column`, `yandex` в карту похожих артистов и в кэш-диспетчеризацию |

**Создаваемые файлы:**

| Файл | Ответственность |
| --- | --- |
| `tests/test_yandex_parity.py` | сквозной страж: yandex зарегистрирован во всех точках интеграции |
| `tests/watchlist/test_yandex_watchlist_ids.py` | колонка Yandex-ID в watchlist: хранение, обновление, backfill |

**Дополняемые тесты:** `tests/downloads/test_downloads_validation.py` (маршрутизация кандидата Yandex), `tests/test_yandex_connection_test.py` (новый файл рядом с `test_usenet_connection_test.py`).

---

### Task 1: Кандидаты Yandex идут в матчер стриминга

**Files:**
- Modify: `core/downloads/validation.py` (константы на уровень модуля; локальные определения на строках 171 и 185, использования на 172 и 367)
- Test: `tests/downloads/test_downloads_validation.py` (дополнить)

**Interfaces:**
- Produces: `core.downloads.validation._STREAMING_SOURCES: tuple[str, ...]` и `core.downloads.validation._STRICT_DURATION_SOURCES: set[str]` — модульные константы, которые Task 6 импортирует для проверки состава.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/downloads/test_downloads_validation.py` (файл уже содержит нужные `_Track`, `_Candidate`, `_MatchingEngine` и импортирует `validation` и `get_valid_candidates`):

```python
def test_yandex_candidate_is_scored_by_the_streaming_matcher(monkeypatch):
    """A Yandex candidate carries structured artist/title fields, so it must be
    scored by the streaming path. Falling through to the Soulseek matcher makes
    it parse ``username='yandex'`` as a filename and extract garbage segments,
    which is what rejected every Yandex download before the fix."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=180_000, name='Gruppa Krovi', artists=('Kino',))
    cand = _Candidate(
        username='yandex',
        duration=181_000,
        title='Gruppa Krovi',
        artist='Kino',
    )

    assert get_valid_candidates([cand], expected, 'Kino Gruppa Krovi') == [cand]


def test_yandex_is_exempt_from_strict_duration_filtering_unlike_tidal(monkeypatch):
    """Yandex catalogue durations legitimately drift from the Spotify reference,
    so yandex must NOT join _STRICT_DURATION_SOURCES: the same drift that
    correctly rejects a Tidal candidate pre-download has to be tolerated for
    Yandex, where post-download integrity + AcoustID do the real checking."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=338_000)

    tidal = _Candidate(username='tidal', duration=30_000)
    assert get_valid_candidates([tidal], expected, 'Artist Song') == []

    yandex = _Candidate(username='yandex', duration=30_000)
    assert get_valid_candidates([yandex], expected, 'Artist Song') == [yandex]
```

- [ ] **Step 2: Прогнать тесты, убедиться что падают**

Run: `<обвязка> tests/downloads/test_downloads_validation.py -k yandex -v`
Expected: FAIL. Оба теста возвращают `[]` вместо кандидата — yandex не распознан как стриминговый источник и уходит в Soulseek-ветку.

- [ ] **Step 3: Поднять константы на уровень модуля**

В `core/downloads/validation.py` найти существующее модульное определение `_VERSION_KEYWORDS` и добавить рядом с ним (до `def get_valid_candidates`):

```python
# Streaming sources return structured API results with proper artist/title metadata —
# score them with the same matching engine as Soulseek instead of the filename matcher.
# Torrent / usenet belong here too: their filename field is a download URL, not a
# slskd-style ``Artist/Album/Track.flac`` path, so the Soulseek matcher would extract
# garbage segments from it. Yandex belongs here for exactly that reason: its candidates
# carry ``username='yandex'`` plus pre-filled ``title`` / ``artist`` fields.
# Module-level so tests and the Yandex parity guard can assert membership.
_STREAMING_SOURCES = (
    "youtube", "tidal", "qobuz", "hifi", "deezer_dl", "soundcloud",
    "amazon", "torrent", "usenet", "yandex",
)

# Sources whose API durations are trustworthy enough to reject a candidate BEFORE
# downloading it. Yandex is deliberately absent: its catalogue durations drift from
# the Spotify reference often enough that a strict pre-download check produced false
# rejections. Post-download integrity checks and AcoustID cover the real mismatches.
_STRICT_DURATION_SOURCES = {"tidal", "qobuz", "hifi", "deezer_dl", "amazon"}
```

- [ ] **Step 4: Убрать локальные определения и переключить использования**

В `get_valid_candidates`:
- удалить строку с локальным `_streaming_sources = (...)` вместе с висящим над ней комментарием (он перенесён к константе);
- заменить `if results[0].username in _streaming_sources:` на `if results[0].username in _STREAMING_SOURCES:`;
- удалить строку с локальным `_strict_duration_sources = {...}`;
- заменить `if (\n                r.username in _strict_duration_sources` на `_STRICT_DURATION_SOURCES`;
- заменить `is_streaming_source = initial_candidates[0].username in _streaming_sources if initial_candidates else False` на `_STREAMING_SOURCES`.

Проверить, что старых имён в файле не осталось:

```bash
cd /home/aronmav/SoulSync && grep -n "_streaming_sources\|_strict_duration_sources" core/downloads/validation.py
```

Expected: пусто (только версии в верхнем регистре, которые этот grep не ловит).

- [ ] **Step 5: Прогнать тесты, убедиться что проходят**

Run: `<обвязка> tests/downloads/test_downloads_validation.py -v`
Expected: PASS, включая существующие тесты SoundCloud / Tidal / torrent (регресс по остальным источникам).

- [ ] **Step 6: Коммит**

```bash
cd /home/aronmav/SoulSync
git add core/downloads/validation.py tests/downloads/test_downloads_validation.py
git -c user.name="AronMav" -c user.email="aronmav13@gmail.com" commit -m "fix: route Yandex candidates through the streaming matcher

Yandex candidates carry username='yandex' with structured title/artist fields,
but the source was missing from the streaming-source tuple, so they fell through
to the Soulseek filename matcher — which parsed the username as a path and
extracted garbage segments, rejecting valid downloads.

Hoists the streaming and strict-duration tuples to module level so they have a
single definition and the parity guard can assert their contents. Yandex stays
out of the strict-duration set on purpose: its catalogue durations drift from
the Spotify reference, and post-download integrity checks cover real mismatches.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Проверка подключения Yandex в Settings → Connections

**Files:**
- Modify: `core/connection_test.py`, функция `run_service_test` (строка 54; ветки провайдеров идут цепочкой `elif`, deezer — на 425, qobuz — на 468)
- Create: `tests/test_yandex_connection_test.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `run_service_test("yandex", {...}) -> tuple[bool, str]` — как все прочие ветки: `(True, "Connected as <login>")` либо `(False, "<причина>")`. Task 6 проверяет, что результат не равен `"Unknown service."`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_yandex_connection_test.py`:

```python
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
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `<обвязка> tests/test_yandex_connection_test.py -v`
Expected: FAIL — все четыре теста получают `(False, "Unknown service.")`, потому что ветки нет.

- [ ] **Step 3: Добавить ветку yandex**

В `core/connection_test.py`, в цепочку `elif` внутри `run_service_test`, сразу после ветки `elif service == "deezer":` (строки 425–437) вставить:

```python
        elif service == "yandex":
            # One token powers metadata, new-release discovery, enrichment and
            # downloads, so verifying it verifies the whole provider. Yandex has
            # no anonymous tier worth probing — an absent token is a config
            # error, not a connectivity one.
            token = (test_config or {}).get('token') or ''
            if not token.strip():
                return False, "Yandex Music token is not set. Paste a user token and click Connect."
            try:
                import yandex_music
                client = yandex_music.Client(token.strip())
                client.init()
                login = None
                try:
                    login = client.me.account.login
                except Exception:
                    login = None
                if login:
                    return True, f"Connected as {login}"
                return True, "Yandex Music token accepted"
            except Exception as e:
                return False, f"Yandex Music connection error: {str(e)}"
```

- [ ] **Step 4: Прогнать тест, убедиться что проходит**

Run: `<обвязка> tests/test_yandex_connection_test.py -v`
Expected: PASS, 4 теста.

- [ ] **Step 5: Прогнать соседний тест на регресс**

Run: `<обвязка> tests/test_usenet_connection_test.py tests/test_spotify_free_status.py -v`
Expected: PASS — цепочка `elif` не сломана вставкой.

- [ ] **Step 6: Коммит**

```bash
cd /home/aronmav/SoulSync
git add core/connection_test.py tests/test_yandex_connection_test.py
git -c user.name="AronMav" -c user.email="aronmav13@gmail.com" commit -m "fix: add Yandex branch to run_service_test

SERVICE_CONFIG_REGISTRY declares a required token for yandex, so expanding the
Connections row fires a verify — which fell through the elif chain and answered
'Unknown service.' The branch validates the token via yandex_music.Client.init()
and reports the account login, matching how every other provider row behaves.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Канонические Yandex-колонки сущностей

**Files:**
- Modify: `database/music_database.py` — новый метод `_add_yandex_columns` рядом с `_add_deezer_columns` (строка 3368); миграция `similar_artists` (строки 2150–2167); CREATE `similar_artists_new` (строка 2276); dataclass `SimilarArtist` (поля `similar_artist_*_id`, строки 120–131)
- Modify: `core/yandex_worker.py` — удалить `_ensure_schema` (строки 47–65) и его вызов (строка 43)
- Test: `tests/database/test_yandex_entity_columns.py` (создать)

**Interfaces:**
- Produces: после `MusicDatabase(path)` таблицы `artists` / `albums` / `tracks` содержат `yandex_id TEXT`, `yandex_match_status TEXT`, `yandex_last_attempted TIMESTAMP`; `similar_artists` содержит `similar_artist_yandex_id TEXT`; dataclass `SimilarArtist` имеет поле `similar_artist_yandex_id: Optional[str] = None`. Task 4 и Task 5 полагаются на наличие `artists.yandex_id`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/database/test_yandex_entity_columns.py`:

```python
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


def test_yandex_columns_are_indexed_like_deezer(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    with db._get_connection() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}

    for expected in (
        'idx_artists_yandex_id', 'idx_artists_yandex_status',
        'idx_albums_yandex_id', 'idx_albums_yandex_status',
        'idx_tracks_yandex_id', 'idx_tracks_yandex_status',
    ):
        assert expected in names, expected


def test_worker_no_longer_owns_the_schema():
    """The worker must not carry a second, competing schema definition."""
    import inspect

    from core import yandex_worker

    assert not hasattr(yandex_worker.YandexWorker, '_ensure_schema')
    assert 'ALTER TABLE' not in inspect.getsource(yandex_worker)
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `<обвязка> tests/database/test_yandex_entity_columns.py -v`
Expected: FAIL — колонок и индексов на свежей БД нет, `_ensure_schema` у воркера есть.

- [ ] **Step 3: Добавить метод `_add_yandex_columns`**

В `database/music_database.py` сразу после метода `_add_deezer_columns` (он заканчивается блоком с `idx_tracks_deezer_status`, строка 3418) добавить его зеркало:

```python
    def _add_yandex_columns(self, cursor):
        """Add Yandex Music tracking columns for enrichment (artists, albums, tracks).

        Mirrors _add_deezer_columns. This is the canonical owner of these columns:
        core/yandex_worker.py used to create them itself with ALTER TABLE, which
        left a fresh database without them until the worker first ran.
        """
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'yandex_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN yandex_id TEXT")
            if 'yandex_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN yandex_match_status TEXT")
            if 'yandex_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN yandex_last_attempted TIMESTAMP")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_yandex_id ON artists (yandex_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_yandex_status ON artists (yandex_match_status)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'yandex_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN yandex_id TEXT")
            if 'yandex_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN yandex_match_status TEXT")
            if 'yandex_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN yandex_last_attempted TIMESTAMP")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_yandex_id ON albums (yandex_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_yandex_status ON albums (yandex_match_status)")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'yandex_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN yandex_id TEXT")
            if 'yandex_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN yandex_match_status TEXT")
            if 'yandex_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN yandex_last_attempted TIMESTAMP")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_yandex_id ON tracks (yandex_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_yandex_status ON tracks (yandex_match_status)")

        except Exception as e:
            logger.error(f"Error adding Yandex columns: {e}")
```

Замечание про живую БД: воркер уже создал эти колонки как `TEXT`, включая `yandex_last_attempted`. Guard увидит существующую колонку и пропустит ALTER, а SQLite благодаря динамической типизации хранит там timestamp-строки без проблем. Расхождение объявленного типа с `TIMESTAMP` на живой БД ожидаемо и безвредно.

- [ ] **Step 4: Вызвать метод в инициализации схемы**

Найти вызов `_add_deezer_columns` и добавить вызов нового метода сразу после него:

```bash
cd /home/aronmav/SoulSync && grep -n "_add_deezer_columns(cursor)" database/music_database.py
```

По найденной строке добавить следующей:

```python
            self._add_yandex_columns(cursor)
```

- [ ] **Step 5: Добавить колонку в `similar_artists`**

В блоке миграций `similar_artists` (после блока `similar_artist_musicbrainz_id`, строка 2167) добавить:

```python
            if 'similar_artist_yandex_id' not in similar_artists_columns:
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN similar_artist_yandex_id TEXT")
                logger.info("Added similar_artist_yandex_id column to similar_artists table")
```

В CREATE `similar_artists_new` (строка 2276) после `similar_artist_musicbrainz_id TEXT,` добавить:

```python
                                similar_artist_yandex_id TEXT,
```

В dataclass, где объявлены `similar_artist_deezer_id` и `similar_artist_musicbrainz_id` (строки 129–130), добавить рядом:

```python
    similar_artist_yandex_id: Optional[str] = None  # Yandex Music artist ID
```

- [ ] **Step 6: Убрать самолечение схемы из воркера**

В `core/yandex_worker.py`:
- удалить строку `self._ensure_schema()` (строка 43);
- удалить метод `_ensure_schema` целиком вместе с комментарием-заголовком `# --- schema (self-healing fallback; canonical schema lives in music_database) ---` (строки 47–65).

`_load_stats_from_db()` остаётся и вызывается сразу после инициализации: он читает те же колонки, которые к этому моменту гарантированно созданы канонической миграцией при построении `MusicDatabase`.

- [ ] **Step 7: Прогнать тесты, убедиться что проходят**

Run: `<обвязка> tests/database/test_yandex_entity_columns.py -v`
Expected: PASS, 4 теста.

- [ ] **Step 8: Прогнать набор БД на регресс**

Run: `<обвязка> tests/database -v`
Expected: PASS — новые ALTER'ы и индексы не ломают существующие тесты схемы.

- [ ] **Step 9: Коммит**

```bash
cd /home/aronmav/SoulSync
git add database/music_database.py core/yandex_worker.py tests/database/test_yandex_entity_columns.py
git -c user.name="AronMav" -c user.email="aronmav13@gmail.com" commit -m "fix: make the database schema own the Yandex enrichment columns

yandex_id / yandex_match_status / yandex_last_attempted existed only because the
enrichment worker created them itself with ALTER TABLE at startup, so a fresh
database had no Yandex columns until the worker first ran and the schema had two
owners. Adds _add_yandex_columns mirroring _add_deezer_columns (including the
matching indexes), adds similar_artist_yandex_id, and drops the worker's
self-healing schema block.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Yandex как источник watchlist (реестр + колонка + мост)

**Files:**
- Modify: `core/watchlist_sources.py` — `SOURCE_COLUMNS`, `SOURCE_ALIASES`
- Modify: `database/music_database.py` — CREATE `watchlist_artists` (строка 492); `_add_watchlist_itunes_id_column` (строка 2730); `optional_columns` (строка 11756); чтение строки (~11784); конструктор `WatchlistArtist` (~11805); dataclass `WatchlistArtist` (строки 91–94); новые `update_watchlist_yandex_id` и `backfill_watchlist_yandex_ids_from_library`
- Test: `tests/watchlist/test_yandex_watchlist_ids.py` (создать)

**Interfaces:**
- Consumes: `artists.yandex_id` из Task 3.
- Produces: `core.watchlist_sources.SOURCE_COLUMNS['yandex'] == 'yandex_artist_id'`; `WatchlistArtist.yandex_artist_id: Optional[str]`; `MusicDatabase.update_watchlist_yandex_id(watchlist_id: int, yandex_id: str) -> bool`; `MusicDatabase.backfill_watchlist_yandex_ids_from_library(profile_id: int = 1) -> int`. Task 5 вызывает `update_watchlist_yandex_id` и читает атрибут `yandex_artist_id`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/watchlist/test_yandex_watchlist_ids.py` (зеркало `test_musicbrainz_watchlist_ids.py`):

```python
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
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `<обвязка> tests/watchlist/test_yandex_watchlist_ids.py -v`
Expected: FAIL — `normalize_source('yandex')` возвращает `None`, колонки и методов нет.

- [ ] **Step 3: Зарегистрировать yandex в реестре источников**

В `core/watchlist_sources.py` в `SOURCE_COLUMNS` добавить запись после `"amazon"`:

```python
    "yandex": "yandex_artist_id",
```

В `SOURCE_ALIASES` добавить написания, которые присылают UI и метадата-реестр:

```python
    "yandex_music": "yandex",
    "yandexmusic": "yandex",
    "ya": "yandex",
```

- [ ] **Step 4: Добавить колонку в схему**

В CREATE `watchlist_artists` (строка 492) после `amazon_artist_id TEXT,` добавить:

```python
                    yandex_artist_id TEXT,
```

В `_add_watchlist_itunes_id_column` (строка 2730 — метод исторически назван по первой колонке, но добавляет их все) после блока `musicbrainz_artist_id` добавить:

```python
            if 'yandex_artist_id' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN yandex_artist_id TEXT")
                logger.info("Added yandex_artist_id column to watchlist_artists table for Yandex Music support")
```

- [ ] **Step 5: Провести колонку через чтение (правило четырёх правок)**

В `database/music_database.py`:

1. dataclass `WatchlistArtist`, после `musicbrainz_artist_id` (строка 94):

```python
    yandex_artist_id: Optional[str] = None  # Cross-provider support
```

2. `optional_columns` (строка 11756) — добавить `'yandex_artist_id'` в список, рядом с `'musicbrainz_artist_id'`:

```python
                optional_columns = ['image_url', 'itunes_artist_id', 'deezer_artist_id', 'discogs_artist_id', 'musicbrainz_artist_id', 'yandex_artist_id', 'include_albums', 'include_eps', 'include_singles',
```

3. чтение строки, рядом с `musicbrainz_artist_id` (~11784):

```python
                    yandex_artist_id = row['yandex_artist_id'] if 'yandex_artist_id' in existing_columns else None
```

4. конструктор `WatchlistArtist(...)`, после `musicbrainz_artist_id=musicbrainz_artist_id,`:

```python
                        yandex_artist_id=yandex_artist_id,
```

- [ ] **Step 6: Добавить метод обновления**

После `update_watchlist_musicbrainz_id` (заканчивается около строки 12123) добавить:

```python
    def update_watchlist_yandex_id(self, watchlist_id: int, yandex_id: str) -> bool:
        """Update the Yandex Music artist ID for a watchlist artist (cross-provider support)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET yandex_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (yandex_id, watchlist_id))

                conn.commit()
                logger.info(f"Updated Yandex ID for watchlist artist {watchlist_id}: {yandex_id}")
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating watchlist Yandex ID: {e}")
            return False
```

- [ ] **Step 7: Добавить мост из библиотеки**

После `backfill_watchlist_musicbrainz_ids_from_library` добавить его зеркало. Сопоставление идёт по имени либо по любому уже известному ID провайдера — так же, как в оригинале:

```python
    def backfill_watchlist_yandex_ids_from_library(self, profile_id: int = 1) -> int:
        """Copy existing library Yandex artist IDs onto matching watchlist rows.

        The Yandex enrichment worker writes IDs to ``artists.yandex_id``. The
        watchlist scan reads ``watchlist_artists.yandex_artist_id``, so this
        bridge lets already-enriched library matches serve the scan instead of
        making it re-resolve every artist by name over the network.
        """
        _match = """
            LOWER(a.name) = LOWER(watchlist_artists.artist_name)
            OR (
                watchlist_artists.spotify_artist_id IS NOT NULL
                AND watchlist_artists.spotify_artist_id != ''
                AND a.spotify_artist_id = watchlist_artists.spotify_artist_id
            )
            OR (
                watchlist_artists.itunes_artist_id IS NOT NULL
                AND watchlist_artists.itunes_artist_id != ''
                AND a.itunes_artist_id = watchlist_artists.itunes_artist_id
            )
            OR (
                watchlist_artists.deezer_artist_id IS NOT NULL
                AND watchlist_artists.deezer_artist_id != ''
                AND a.deezer_id = watchlist_artists.deezer_artist_id
            )
            OR (
                watchlist_artists.musicbrainz_artist_id IS NOT NULL
                AND watchlist_artists.musicbrainz_artist_id != ''
                AND a.musicbrainz_id = watchlist_artists.musicbrainz_artist_id
            )
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    UPDATE watchlist_artists
                    SET yandex_artist_id = (
                            SELECT a.yandex_id
                            FROM artists a
                            WHERE a.yandex_id IS NOT NULL
                              AND a.yandex_id != ''
                              AND ({_match})
                            LIMIT 1
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE profile_id = ?
                      AND (yandex_artist_id IS NULL OR yandex_artist_id = '')
                      AND EXISTS (
                          SELECT 1
                          FROM artists a
                          WHERE a.yandex_id IS NOT NULL
                            AND a.yandex_id != ''
                            AND ({_match})
                      )
                """, (profile_id,))
                updated = cursor.rowcount
                conn.commit()
                if updated:
                    logger.info(f"Backfilled Yandex IDs onto {updated} watchlist artists from the library")
                return updated

        except Exception as e:
            logger.error(f"Error backfilling watchlist Yandex IDs: {e}")
            return 0
```

- [ ] **Step 8: Прогнать тест, убедиться что проходит**

Run: `<обвязка> tests/watchlist/test_yandex_watchlist_ids.py -v`
Expected: PASS, 7 тестов.

- [ ] **Step 9: Прогнать watchlist-набор на регресс**

Run: `<обвязка> tests/watchlist tests/test_watchlist_itunes_id_repair.py tests/metadata/test_artist_source_lookup.py -v`
Expected: PASS. Особое внимание `test_provider_neutral_contract.py` — он параметризован по списку провайдеров и проверяет, что неизвестный источник отвергается; добавление yandex в реестр не должно его сломать.

- [ ] **Step 10: Коммит**

```bash
cd /home/aronmav/SoulSync
git add core/watchlist_sources.py database/music_database.py tests/watchlist/test_yandex_watchlist_ids.py
git -c user.name="AronMav" -c user.email="aronmav13@gmail.com" commit -m "feat: register Yandex Music as a Watchlist provider

core/watchlist_sources.py is the one place mapping a Watchlist provider to its id
column, and yandex was absent — so an artist could not be filed under Yandex and
a scan could not resolve one. Registers the provider plus its aliases, adds the
watchlist_artists.yandex_artist_id column through all four required places
(CREATE, migration, optional_columns, dataclass), and adds update +
library-backfill methods mirroring the MusicBrainz pair.

Omitting optional_columns is the documented trap here: the column would pass the
PRAGMA guard, never get selected, and KeyError out of get_watchlist_artists —
leaving the scan with zero artists.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Сканер использует кэш Yandex-ID

**Files:**
- Modify: `core/watchlist_scanner.py` — `_artist_id_attribute_for_source` (строки 574–583), `_similar_artist_id_attribute_for_source` (строки 585–593), `_cache_watchlist_artist_source_id` (строки 605–625), две площадки диспетчеризации `cache_callback` (строки ~3220–3226 и ~3462–3467)
- Test: `tests/watchlist/test_yandex_scanner_ids.py` (создать)

**Interfaces:**
- Consumes: `core.watchlist_sources.source_column` и `MusicDatabase.update_watchlist_yandex_id` из Task 4.
- Produces: `WatchlistScanner._artist_id_attribute_for_source('yandex') == 'yandex_artist_id'`, `WatchlistScanner._similar_artist_id_attribute_for_source('yandex') == 'similar_artist_yandex_id'`. Task 6 проверяет обе карты.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/watchlist/test_yandex_scanner_ids.py`:

```python
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
    """The scanner must not keep a second copy of the provider→column map:
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
    scanner.database = MagicMock()
    artist = MagicMock()
    artist.id = 42

    scanner._cache_watchlist_artist_source_id(artist, 'yandex', 'ya-1')

    scanner.database.update_watchlist_yandex_id.assert_called_once_with(42, 'ya-1')
    assert artist.yandex_artist_id == 'ya-1'
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `<обвязка> tests/watchlist/test_yandex_scanner_ids.py -v`
Expected: FAIL — карты возвращают `None` для yandex, кэш ничего не пишет.

- [ ] **Step 3: Делегировать карту артистов в реестр**

Заменить тело `_artist_id_attribute_for_source` (строки 574–583) на делегирование — так карта перестаёт быть второй копией:

```python
    @staticmethod
    def _artist_id_attribute_for_source(source: str) -> Optional[str]:
        """Return the watchlist artist attribute that stores the given source ID.

        Delegates to core.watchlist_sources, which is the single registry mapping
        a provider to its ``watchlist_artists`` column (the dataclass attribute
        carries the same name). Keeping a second copy here is what silently
        dropped Yandex when the fork moved to upstream 3.1.
        """
        from core.watchlist_sources import source_column

        return source_column(source)
```

- [ ] **Step 4: Добавить yandex в карту похожих артистов**

В `_similar_artist_id_attribute_for_source` (строка 585) добавить запись — у таблицы `similar_artists` своя схема колонок, отдельного реестра для неё нет:

```python
            'yandex': 'similar_artist_yandex_id',
```

- [ ] **Step 5: Добавить ветку кэширования**

В `_cache_watchlist_artist_source_id` после ветки `musicbrainz` (строка 623–625) добавить:

```python
        elif source == 'yandex':
            self.database.update_watchlist_yandex_id(watchlist_artist.id, source_id)
            watchlist_artist.yandex_artist_id = source_id
```

- [ ] **Step 6: Добавить yandex в две площадки диспетчеризации**

В обоих местах, где строится `cache_callback` (около строк 3220–3226 и 3462–3467), после ветки `deezer` добавить ветку с той же формой, что у соседей:

```python
                        elif source == 'yandex':
                            cache_callback = lambda found_id, watchlist_id=artist.id, artist=artist: self._cache_watchlist_artist_source_id(artist, 'yandex', found_id)
```

Отступ во второй площадке (строка ~3462) на четыре пробела меньше — скопировать отступ соседней ветки `deezer` в том же блоке.

Проверить, что обе площадки покрыты:

```bash
cd /home/aronmav/SoulSync && grep -c "_cache_watchlist_artist_source_id(artist, 'yandex'" core/watchlist_scanner.py
```

Expected: `2`

- [ ] **Step 7: Прогнать тест, убедиться что проходит**

Run: `<обвязка> tests/watchlist/test_yandex_scanner_ids.py -v`
Expected: PASS, 5 тестов.

- [ ] **Step 8: Прогнать наборы сканера и watchlist на регресс**

Run: `<обвязка> tests/watchlist tests/test_watchlist_scanner_scan.py -v`
Expected: PASS — делегирование карты не меняет результат ни для одного существующего провайдера (это закрепляет `test_artist_id_attribute_delegates_to_the_single_registry`).

- [ ] **Step 9: Коммит**

```bash
cd /home/aronmav/SoulSync
git add core/watchlist_scanner.py tests/watchlist/test_yandex_scanner_ids.py
git -c user.name="AronMav" -c user.email="aronmav13@gmail.com" commit -m "fix: teach the watchlist scanner to cache Yandex artist ids

The scanner had no storage column for yandex, so every scan re-resolved all
watched artists by name over the network instead of reusing the id already
stored by the enrichment worker.

_artist_id_attribute_for_source now delegates to core.watchlist_sources instead
of duplicating the provider→column map — that duplicate is precisely what lost
Yandex during the 3.1 port. The similar-artist map and the two cache_callback
dispatch sites get explicit yandex entries.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Тест-страж паритета

**Files:**
- Create: `tests/test_yandex_parity.py`

**Interfaces:**
- Consumes: всё, что произвели задачи 1–5.
- Produces: ничего для последующих задач — это конечная защита.

- [ ] **Step 1: Написать тест-страж**

Создать `tests/test_yandex_parity.py`:

```python
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
    from core.download_plugins.registry import get_registry

    names = {spec.name for spec in get_registry().specs()}
    assert 'yandex' in names


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
    """core/watchlist_sources.py is the single provider→column registry; losing
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


def test_manual_match_knows_the_yandex_id_columns():
    """Losing this makes manual matching reject every Yandex entity as an
    invalid service/entity_type combination."""
    import web_server

    assert web_server._SERVICE_ID_COLUMNS.get('yandex') == {
        'artist': 'yandex_id', 'album': 'yandex_id', 'track': 'yandex_id',
    }


def test_yandex_service_config_requires_a_token():
    import web_server

    assert web_server.SERVICE_CONFIG_REGISTRY.get('yandex') == {'required': ['token']}
```

- [ ] **Step 2: Прогнать страж**

Run: `<обвязка> tests/test_yandex_parity.py -v`
Expected: PASS по всем проверкам. Задачи 1–5 закрыли все точки.

Если падает `test_download_plugin_registry_offers_yandex` — проверить фактический API реестра: `grep -n "def specs\|def get_registry\|def all\b" core/download_plugins/registry.py` и привести обращение к существующему методу (реестр строится вызовом `registry.register(PluginSpec(name='yandex', ...))`, строка 197).

Если падают два последних теста при импорте `web_server` (модуль тяжёлый, создаёт Flask-приложение) — вынести их в отдельный файл `tests/test_yandex_parity_web.py` с тем же содержанием, чтобы тяжёлый импорт не тормозил основной страж. Проверки при этом сохранить: они закрывают реальные точки потери.

- [ ] **Step 3: Проверить, что страж действительно ловит потерю**

Мутационная проверка: временно удалить `'yandex'` из `_STREAMING_SOURCES` в `core/downloads/validation.py`, прогнать `<обвязка> tests/test_yandex_parity.py -k streaming -v`, убедиться в FAIL, вернуть значение обратно.

```bash
cd /home/aronmav/SoulSync && git diff --stat core/downloads/validation.py
```

Expected: пусто — правка откачена.

- [ ] **Step 4: Прогнать весь затронутый объём**

Run: `<обвязка> tests/test_yandex_parity.py tests/downloads tests/database tests/watchlist tests/metadata tests/enrichment tests/test_yandex_connection_test.py -q`
Expected: PASS, без новых падений относительно базовой линии.

Если какие-то тесты падали ещё ДО наших правок (унаследованные от upstream), зафиксировать их список и убедиться, что он не вырос: `git stash && <обвязка> ... -q ; git stash pop`.

- [ ] **Step 5: Коммит**

```bash
cd /home/aronmav/SoulSync
git add tests/test_yandex_parity.py
git -c user.name="AronMav" -c user.email="aronmav13@gmail.com" commit -m "test: guard Yandex provider parity across all integration points

Migrating the fork onto upstream 3.1 re-ported Yandex only partially and nothing
failed — the app booted, the worker ran, the UI showed Yandex — so four missing
registrations went unnoticed until an audit found them.

This guard asserts every registration a first-class provider needs: metadata
registry and client factory, download plugin, streaming-matcher membership (and
deliberate strict-duration exemption), Connections verify, enrichment entity
support and yield policy, the watchlist provider registry and scanner maps, and
every schema column including the optional_columns read path. Each assertion
documents what breaks when it is lost.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Деплой и приёмка на живой системе

**Files:** изменений в коде нет — сборка, проверка, публикация.

**Interfaces:**
- Consumes: коммиты задач 1–6.

- [ ] **Step 1: Бэкап живой БД**

```bash
ssh aronmav@188.246.224.118 'docker exec soulsync sh -c "cp /app/data/music_library.db /app/data/music_library.db.bak-yandex-parity-20260730" && docker exec soulsync ls -la /app/data/ | grep bak-yandex-parity'
```

Expected: файл бэкапа есть и по размеру совпадает с исходным.

- [ ] **Step 2: Собрать образ**

```bash
ssh aronmav@188.246.224.118 'cd /home/aronmav/SoulSync && docker build -t soulsync-custom:3.1-yandex-parity . 2>&1 | tail -20'
```

Expected: `Successfully tagged`. Образ `soulsync-custom:yandex` остаётся нетронутым как откат.

- [ ] **Step 3: Переключить compose на новый тег и поднять**

```bash
ssh aronmav@188.246.224.118 'cd /home/aronmav/docker/music/soulsync && cp docker-compose.yml docker-compose.yml.bak-yandex-parity && sed -i "s|image: soulsync-custom:yandex|image: soulsync-custom:3.1-yandex-parity|" docker-compose.yml && grep -n "image:" docker-compose.yml && docker compose up -d && sleep 45 && docker ps --filter name=soulsync --format "{{.Status}}"'
```

Expected: `image: soulsync-custom:3.1-yandex-parity`, контейнер `Up ... (healthy)`.

- [ ] **Step 4: Проверить миграцию схемы на живой БД**

```bash
ssh aronmav@188.246.224.118 'docker exec soulsync python3 -c "
import sqlite3
con = sqlite3.connect(\"/app/data/music_library.db\")
def cols(t):
    return {r[1] for r in con.execute(f\"PRAGMA table_info({t})\")}
for t in (\"artists\",\"albums\",\"tracks\"):
    print(t, sorted(c for c in cols(t) if \"yandex\" in c))
print(\"watchlist_artists\", sorted(c for c in cols(\"watchlist_artists\") if \"yandex\" in c))
print(\"similar_artists\", sorted(c for c in cols(\"similar_artists\") if \"yandex\" in c))
"'
```

Expected: у `artists`/`albums`/`tracks` — три yandex-колонки; `watchlist_artists` содержит `yandex_artist_id`; `similar_artists` содержит `similar_artist_yandex_id`.

- [ ] **Step 5: Проверить, что watchlist читается (ловушка `optional_columns`)**

```bash
ssh aronmav@188.246.224.118 'docker exec soulsync python3 -c "
from database.music_database import MusicDatabase
rows = MusicDatabase().get_watchlist_artists(profile_id=1)
print(\"watchlist artists:\", len(rows))
print(\"with yandex id:\", sum(1 for r in rows if r.yandex_artist_id))
"'
```

Expected: количество артистов НЕ ноль (ожидается порядка 171). Ноль означает срабатывание ловушки `optional_columns` — тогда откат по шагу 9.

- [ ] **Step 6: Проверить verify Yandex**

```bash
ssh aronmav@188.246.224.118 'curl -s -X POST http://10.10.1.40:8008/api/settings/verify -H "Content-Type: application/json" -d "{\"services\":[\"yandex\"]}"'
```

Expected: `success: true` и сообщение с логином аккаунта. Больше не `"Unknown service."`

- [ ] **Step 7: Заполнить Yandex-ID в watchlist из библиотеки**

```bash
ssh aronmav@188.246.224.118 'docker exec soulsync python3 -c "
from database.music_database import MusicDatabase
db = MusicDatabase()
print(\"backfilled:\", db.backfill_watchlist_yandex_ids_from_library(profile_id=1))
rows = db.get_watchlist_artists(profile_id=1)
print(\"with yandex id:\", sum(1 for r in rows if r.yandex_artist_id), \"/\", len(rows))
"'
```

Expected: `backfilled` больше нуля (enrichment-воркер уже сматчил порядка 352 артистов), и доля артистов с проставленным ID заметно выросла.

- [ ] **Step 8: Проверить, что воркер жив без `_ensure_schema`, и загрузку через Yandex**

```bash
ssh aronmav@188.246.224.118 'docker logs soulsync --since 5m 2>&1 | grep -iE "yandex|schema|error|traceback" | tail -25'
```

Expected: `Yandex background worker started`, статистика восстановлена, ни `Traceback`, ни ошибок схемы.

Затем — реальная загрузка через Yandex (проверяет дырку № 1 вживую). Выбрать трек, которого нет в библиотеке, запустить загрузку через UI `soulsync.aronmav.ru` с источником yandex либо через API, и проследить по логам, что кандидат прошёл валидацию (`validation conf`), а не был отклонён матчером имён:

```bash
ssh aronmav@188.246.224.118 'docker logs soulsync --since 10m 2>&1 | grep -iE "yandex|validation|candidate|reject" | tail -30'
```

Expected: кандидат принят, файл дошёл до библиотеки. Если видно отклонение с разбором пути на сегменты — дырка № 1 закрыта не полностью.

- [ ] **Step 9: Условие откката**

Если любой из шагов 4–8 провалился:

```bash
ssh aronmav@188.246.224.118 'cd /home/aronmav/docker/music/soulsync && cp docker-compose.yml.bak-yandex-parity docker-compose.yml && docker compose up -d && sleep 30 && docker ps --filter name=soulsync --format "{{.Status}}"'
```

БД при этом остаётся с добавленными колонками — они аддитивны и старому коду не мешают (воркер вернёт своё `_ensure_schema` и увидит колонки готовыми). Если нужно откатить и данные — восстановить `music_library.db.bak-yandex-parity-20260730`.

- [ ] **Step 10: Публикация**

```bash
ssh aronmav@188.246.224.118 'cd /home/aronmav/SoulSync && git log --oneline -7 && git push origin main'
```

Expected: шесть кодовых коммитов + коммит спеки уехали в `AronMav/SoulSync`.

- [ ] **Step 11: Обновить память проекта**

Дописать в память (`project_music_stack_188.md`) факты: миграция форка на 3.1 роняла Yandex в четырёх точках; ключевой файл 3.1 — `core/watchlist_sources.py` как единственный реестр провайдеров watchlist; тест `tests/test_yandex_parity.py` — страж, прогонять после каждого мёржа upstream; правило четырёх правок для колонки `watchlist_artists`; актуальный тег образа.

---

## Self-Review

**Покрытие спеки.** Дырка № 1 → Task 1. Дырка № 2 → Task 2. Дырка № 3 → Task 4 (реестр + колонка + мост) и Task 5 (карты сканера и кэш). Дырка № 4 → Task 3 (колонки сущностей, similar_artists, удаление `_ensure_schema`) и Task 4 (`watchlist_artists`, включая `optional_columns`). Тест-страж → Task 6, все перечисленные в спеке проверки на месте плюс проверки `_SERVICE_ID_COLUMNS` и `SERVICE_CONFIG_REGISTRY`. Верификация и деплой → Task 7, включая бэкап БД, rollback-образ, все пять пунктов приёмки и push. Обязательный поведенческий тест из спеки → Task 1, Step 1, второй тест. Три отклонения по механизму выписаны явным разделом.

**Плейсхолдеры.** Не осталось: каждый шаг содержит либо готовый код, либо конкретную команду с ожидаемым результатом. Два условных ветвления в Task 6, Step 2 сформулированы как правила принятия решения с конкретной проверяющей командой, а не как «доработать по ситуации».

**Согласованность имён.** `_STREAMING_SOURCES` и `_STRICT_DURATION_SOURCES` вводятся в Task 1 и используются под теми же именами в Task 6. `update_watchlist_yandex_id` и `backfill_watchlist_yandex_ids_from_library` объявлены в Task 4 и вызываются в Task 5 и Task 7 с теми же сигнатурами. `yandex_artist_id` (watchlist), `yandex_id` (сущности), `similar_artist_yandex_id` (похожие) — три разных имени для трёх разных таблиц, различие сохранено во всех задачах. `source_column` из `core/watchlist_sources` используется в Task 4 и Task 5 согласованно.
