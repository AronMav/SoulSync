# Yandex Music provider — восстановление паритета после миграции на upstream 3.1

**Дата:** 2026-07-30
**Репозиторий:** `AronMav/SoulSync`, ветка `main` (форк Nezreka/SoulSync, поверх upstream 3.1.9)
**Рабочая копия:** `/home/aronmav/SoulSync` на сервере 188.246.224.118
**Статус:** одобрено к реализации

## Контекст и проблема

Форк SoulSync содержит Yandex Music как полноценный провайдер: метаданные, источник
загрузок, enrichment-воркер, источник обнаружения новинок для watchlist-сканера.
До миграции на upstream 3.1 интеграция была доведена до полного паритета с остальными
провайдерами.

При переводе форка на upstream 3.1.9 Yandex был портирован заново, но порт оказался
неполным: часть точек интеграции не восстановлена. Потери молчаливые — приложение
запускается, воркер работает, UI отображает Yandex, поэтому регрессия не заметна
без построчного аудита.

Аудит текущего состояния форка выявил четыре незакрытых точки. Остальное
(`core/metadata/registry.py`, `core/download_plugins/registry.py`,
`core/enrichment/unmatched.py`, `core/enrichment/yield_policy.py`,
`_SERVICE_ID_COLUMNS`, `SERVICE_CONFIG_REGISTRY`, файлы webui) — на месте.

## Цель

1. Закрыть четыре выявленные дырки, вернув Yandex к паритету с прочими провайдерами.
2. Зафиксировать паритет автоматическим тестом, чтобы следующий мёрж upstream ронял
   сборку явной ошибкой вместо молчаливой потери функциональности.

Вне области: кнопка удаления треков в Navidrome и чёрный список удалённых
(отдельная спека), смена primary-источника метаданных, расширение возможностей
Yandex за пределы существующих, мёрж upstream.

## Дырка № 1 — валидация кандидатов загрузки

**Файл:** `core/downloads/validation.py`, функция `get_valid_candidates` (строка 149)

`_streaming_sources` (строка 171) не содержит `"yandex"`. Кандидаты Yandex несут
`username='yandex'` (`core/yandex_download_client.py:138`), поэтому попадают не в
метадата-матчер, а в Soulseek-матчер имён файлов, который из такого значения
извлекает мусорные сегменты и даёт ложные отказы. Это тот же root cause, который
уже был найден и исправлен до миграции.

**Изменение:**

- Поднять `_streaming_sources` из локальной переменной в модульную константу
  `_STREAMING_SOURCES`. Оба использования (строки 171 и 367) лежат внутри одной
  функции, так что текущий код корректен, но локальный кортеж невозможно
  импортировать в тест. Модульная константа даёт одно определение и тестируемость.
- Добавить `"yandex"` в эту константу.
- **Не** добавлять `yandex` в `_strict_duration_sources` (строка 185). Длительности
  треков в каталоге Яндекса систематически расходятся со Spotify-эталоном, поэтому
  строгая отбраковка по длительности до скачивания дала бы ложные отказы. Решение
  осознанное и переносится из pre-3.1 версии; тест паритета фиксирует именно
  отсутствие yandex в этом множестве.

## Дырка № 2 — проверка подключения в Settings → Connections

**Файл:** `core/connection_test.py`, функция `run_service_test` (строка 54)

Ветки `yandex` нет. `SERVICE_CONFIG_REGISTRY` при этом требует для yandex поле
`token`, поэтому раскрытие строки Yandex в Connections шлёт verify и получает
`"Unknown service."` Проверено живьём:
`POST /api/settings/verify {"services":["yandex"]}` →
`{"success": false, "error": "Unknown service."}`

**Изменение:** добавить ветку `elif service == "yandex":` по образцу существующих
веток deezer и qobuz — прочитать токен из `test_config`, валидировать через
`yandex_music.Client(token).init()`, вернуть успех с логином аккаунта
(`"Connected as <login>"`) либо внятную ошибку при невалидном токене.

## Дырка № 3 — watchlist-сканер не использует кэш Yandex-ID

**Файл:** `core/watchlist_scanner.py`

Ни одного упоминания yandex. Следствия: сканер не умеет ни читать, ни записывать
Yandex-ID артистов, поэтому резолвит каждого артиста по имени при каждом прогоне.
При 171 отслеживаемом артисте и рейт-лимите порядка 30 секунд на артиста это
единственная причина, по которой суточный скан длится больше часа.

**Изменения:**

- `_artist_id_attribute_for_source` (строка 574) → добавить
  `'yandex': 'yandex_artist_id'`.
- `_similar_artist_id_attribute_for_source` (строка 585) → добавить
  `'yandex': 'similar_artist_yandex_id'`.
- Новый метод `_library_yandex_id(name)` — читает `artists.yandex_id`, уже
  заполненный enrichment-воркером, и отдаёт его до попытки поиска по имени.
  Мост между enrichment и discovery: один резолв на двоих.
- Ветки `cache_callback` для yandex там, где сейчас перечислены spotify / itunes /
  deezer (около строк 3222–3226 и 3463–3467), чтобы найденный ID кэшировался в
  колонку, как у остальных источников.

Родовой путь кэширования `_cache_watchlist_artist_source_id` (строка 605) уже
опирается на карту `_artist_id_attribute_for_source`, поэтому добавления в карту
достаточно для записи; отдельные ветки нужны только в местах явной диспетчеризации.

## Дырка № 4 — каноническая схема базы данных

**Файл:** `database/music_database.py`

Ни одного упоминания yandex. Колонки `yandex_id` / `yandex_match_status` /
`yandex_last_attempted` в `artists` / `albums` / `tracks` сейчас существуют только
потому, что `core/yandex_worker.py` создаёт их сам методом `_ensure_schema()` через
`ALTER TABLE` при инициализации. Колонок `watchlist_artists.yandex_artist_id` и
`similar_artists.similar_artist_yandex_id` нет вовсе.

**Изменения:**

- `artists` / `albums` / `tracks`: `yandex_id`, `yandex_match_status`,
  `yandex_last_attempted` — в CREATE и в блоки миграций, по образцу deezer-пары
  (строка 1114), с индексами по аналогии.
- `watchlist_artists.yandex_artist_id`:
  - в CREATE (блок строки 492);
  - миграция `ALTER TABLE` для существующих БД;
  - **в явный список `optional_columns` (строка 11756)**;
  - поле в dataclass `WatchlistArtist`;
  - чтение в `get_watchlist_artists`;
  - метод `update_watchlist_yandex_id` по образцу `update_watchlist_deezer_id`
    (строка 12070).
- `similar_artists.similar_artist_yandex_id`: поле dataclass + миграция
  `ALTER TABLE` (зеркало блока строки 2159).
- Удалить `_ensure_schema()` из `core/yandex_worker.py` — после канонизации схемы
  это дубль, а схема должна иметь один источник истины. Каноническая миграция
  выполняется при инициализации БД, то есть до старта воркера.

**Критично.** `get_watchlist_artists` строит SELECT из явного списка
`optional_columns`, а guard проверяет наличие колонки через PRAGMA. Если колонка
добавлена в схему, но не внесена в `optional_columns`, guard проходит, колонка не
селектится, обращение к `row['yandex_artist_id']` даёт `KeyError`, метод возвращает
пустой список — и **watchlist-скан находит 0 артистов**. Эта ошибка уже
происходила ранее. Правило: колонка в `watchlist_artists` требует четырёх правок —
CREATE, миграция, `optional_columns`, dataclass.

## Тест-страж паритета

**Файл:** `tests/test_yandex_parity.py` (верхний уровень `tests/`, как прочие
сквозные тесты вида `test_acoustid_*.py`; тест затрагивает много подсистем и не
принадлежит ни одному доменному подкаталогу)

Один файл, проверяющий регистрацию Yandex во всех точках интеграции:

- `METADATA_SOURCE_PRIORITY` содержит `yandex`; `METADATA_SOURCE_LABELS['yandex']`
  задан;
- `get_client_for_source('yandex')` возвращает клиент;
- реестр download-плагинов содержит `yandex`;
- `_STREAMING_SOURCES` содержит `yandex`, а `_strict_duration_sources` — **не**
  содержит (фиксирует осознанное решение из дырки № 1);
- `SERVICE_CONFIG_REGISTRY['yandex']` требует `token`;
- `run_service_test('yandex', ...)` не возвращает `"Unknown service"`;
- `SERVICE_ENTITY_SUPPORT['yandex'] == ('artist', 'album', 'track')`;
- `_SERVICE_ID_COLUMNS['yandex']` покрывает artist / album / track;
- `yandex` присутствует в `ALL_YIELD_WORKERS` и в `API_CONTENTION_WORKERS`;
- карты `_artist_id_attribute_for_source` и
  `_similar_artist_id_attribute_for_source` отдают колонки для `yandex`;
- после инициализации временной БД все четыре таблицы содержат соответствующие
  yandex-колонки.

Тест работает без сети: `yandex_music` мокается, БД создаётся во временном файле.

Помимо проверок членства — обязательная поведенческая проверка: кандидат с
`username='yandex'` проходит через стриминговый путь `get_valid_candidates`, а не
через матчер имён файлов. Она ценнее проверки членства в списке, потому что
проверяет эффект, а не декларацию. Обвязка минимальна: в
`tests/downloads/test_downloads_validation.py` уже есть dataclass `_Candidate`,
которым строятся кандидаты вида `_Candidate(username='youtube', duration=...)` —
поведенческий кейс для yandex пишется по этому образцу.

## Порядок реализации

Пять коммитов, каждый самодостаточен:

1. `fix: route Yandex candidates through the streaming matcher` — дырка № 1
2. `fix: add Yandex branch to run_service_test` — дырка № 2
3. `fix: teach watchlist scanner to cache Yandex artist IDs` — дырка № 3
4. `fix: canonicalize Yandex columns in the database schema` — дырка № 4
5. `test: guard Yandex provider parity across all integration points` — тест

## Верификация

**Прогон тестов.** На хосте pytest сейчас не установлен (в `tests/__pycache__`
остались артефакты прошлых прогонов под pytest 9.1.1, но интерпретатор его больше
не видит). Поэтому не создавать venv с нуля, а запустить pytest в одноразовом
контейнере из существующего образа, где все runtime-зависимости уже стоят в
`/opt/venv`, смонтировав рабочую копию репозитория. Если этот путь упрётся —
резервный вариант — venv на хосте из `requirements-dev.txt` (тянет и runtime-,
и dev-зависимости). Прогнать новый тест и смежные наборы на регресс:
`tests/downloads`, `tests/database`, `tests/metadata`, `tests/enrichment`.

**Деплой.** Перед деплоем — бэкап живой `music_library.db` (миграция правит схему
рабочей базы). Сборка нового тега `soulsync-custom:3.1-yandex-parity` из
`Dockerfile`; текущий `soulsync-custom:yandex` остаётся образом отката. Обновление
контейнера через `docker compose up -d`.

**Приёмка на живой системе.**

- `POST /api/settings/verify {"services":["yandex"]}` возвращает успех с логином
  аккаунта вместо `"Unknown service."`
- Все ожидаемые yandex-колонки присутствуют в живой `music_library.db`.
- `get_watchlist_artists` возвращает непустой список (защита от ловушки
  `optional_columns`).
- Реальная загрузка трека через Yandex проходит валидацию кандидатов и доходит до
  библиотеки.
- Тест-скан watchlist берёт ID артистов из колонки, а не резолвит по имени.

**Публикация.** Push ветки `main` в `AronMav/SoulSync`.

## Риски

| Риск | Митигация |
| --- | --- |
| Миграция правит схему живой БД | Бэкап `music_library.db` перед деплоем; rollback-образ |
| Удаление `_ensure_schema()` оставит колонки несозданными, если каноническая миграция не отработает на каком-то пути | Тест проверяет наличие колонок после инициализации БД; проверка живой БД в приёмке |
| Поведение матчера изменится для не-Yandex источников | Правка аддитивна (добавление элемента в кортеж); прогон `tests/downloads` на регресс |
