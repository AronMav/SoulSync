"""Background worker that enriches the library with Yandex Music IDs.

Full parity with the other enrichment workers (Deezer/Spotify/…): processes
artists → albums → tracks, writing ``yandex_id`` / ``yandex_match_status`` /
``yandex_last_attempted`` on each table. Matching goes through the shared
``YandexMetadataClient`` and is name-gated (never stores a wrong ID); when a
name is ambiguous across several artists it is disambiguated by catalogue
overlap with what the library actually owns (same approach DeezerWorker uses).

The artist IDs this worker resolves are reused by the watchlist new-release
scan (``WatchlistScanner._library_yandex_id``), so enrichment and discovery
share one resolution instead of each searching by name.
"""
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.yandex_metadata_client import get_yandex_metadata_client
from core.worker_utils import interruptible_sleep, artist_name_matches, _names_equivalent

logger = get_logger("yandex_worker")


class YandexWorker:
    """Enrich library artists, albums and tracks with Yandex Music IDs."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = get_yandex_metadata_client()

        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self._stop_event = threading.Event()

        self.current_item = None
        self.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
        self.retry_days = 30

        self._ensure_schema()
        logger.info("Yandex background worker initialized")

    # --- schema (self-healing fallback; canonical schema lives in music_database) ---
    def _ensure_schema(self):
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            for table in ("artists", "albums", "tracks"):
                cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
                for col in ("yandex_id", "yandex_match_status", "yandex_last_attempted"):
                    if col not in cols:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                        logger.info("Added %s.%s column", table, col)
            conn.commit()
        except Exception as e:
            logger.error("Yandex worker schema ensure failed: %s", e)
        finally:
            if conn:
                conn.close()

    # --- lifecycle ---------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Yandex background worker started")

    def stop(self):
        if not self.running:
            return
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)

    def pause(self):
        if self.running:
            self.paused = True
            logger.info("Yandex worker paused")

    def resume(self):
        if self.running:
            self.paused = False
            logger.info("Yandex worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending()
        progress = self._progress()
        alive = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = alive and not self.paused and self.stats['pending'] == 0 and self.current_item is None
        return {
            'enabled': True,
            'running': alive and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress,
        }

    # --- main loop ---------------------------------------------------------
    def _run(self):
        logger.info("Yandex worker thread started")
        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue
                self.current_item = None
                if self.client is None:
                    interruptible_sleep(self._stop_event, 30)
                    continue
                item = self._next_item()
                if not item:
                    interruptible_sleep(self._stop_event, 15)
                    continue
                self.current_item = item
                if item.get('id') is None:
                    continue
                self._process_item(item)
                interruptible_sleep(self._stop_event, 1.5)
            except Exception as e:
                logger.error("Yandex worker loop error: %s", e)
                interruptible_sleep(self._stop_event, 5)
        logger.info("Yandex worker thread finished")

    def _process_item(self, item):
        t = item['type']
        try:
            if t == 'artist':
                self._process_artist(item['id'], item['name'])
            elif t == 'album':
                self._process_album(item['id'], item['name'], item.get('artist', ''))
            elif t == 'track':
                self._process_track(item['id'], item['name'], item.get('artist', ''))
        except Exception as e:
            logger.error("yandex _process_item %s#%s: %s", t, item.get('id'), e)
            self.stats['errors'] += 1
            self._mark(t, item['id'], 'not_found')

    # --- work queue (artists -> albums -> tracks) --------------------------
    def _next_item(self) -> Optional[Dict[str, Any]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            cutoff = (datetime.utcnow() - timedelta(days=self.retry_days)).strftime('%Y-%m-%d %H:%M:%S')

            cur.execute("SELECT id, name FROM artists WHERE yandex_match_status IS NULL "
                        "AND id IS NOT NULL AND name IS NOT NULL ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            cur.execute("SELECT a.id, a.title, ar.name FROM albums a JOIN artists ar ON a.artist_id = ar.id "
                        "WHERE a.yandex_match_status IS NULL AND a.id IS NOT NULL AND a.title IS NOT NULL "
                        "ORDER BY a.id ASC LIMIT 1")
            row = cur.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2]}

            cur.execute("SELECT t.id, t.title, ar.name FROM tracks t JOIN artists ar ON t.artist_id = ar.id "
                        "WHERE t.yandex_match_status IS NULL AND t.id IS NOT NULL AND t.title IS NOT NULL "
                        "ORDER BY t.id ASC LIMIT 1")
            row = cur.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # retry oldest not_found artist
            cur.execute("SELECT id, name FROM artists WHERE yandex_match_status = 'not_found' "
                        "AND yandex_last_attempted < ? AND name IS NOT NULL "
                        "ORDER BY yandex_last_attempted ASC LIMIT 1", (cutoff,))
            row = cur.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}
            return None
        except Exception as e:
            logger.error("yandex _next_item: %s", e)
            return None
        finally:
            if conn:
                conn.close()

    # --- matching ----------------------------------------------------------
    def _owned_album_titles(self, artist_id):
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            rows = cur.execute("SELECT title FROM albums WHERE artist_id = ? AND title IS NOT NULL", (artist_id,)).fetchall()
            return {(r[0] or '').strip().lower() for r in rows if r[0]}
        except Exception:
            return set()
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id, artist_name):
        results = self.client.search_artists(artist_name, limit=5)  # strict exact-name candidates
        if not results:
            self._mark('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            return
        chosen = results[0]
        if len(results) > 1:
            # Disambiguate same-name artists by catalogue overlap with owned albums.
            owned = self._owned_album_titles(artist_id)
            if owned:
                best, best_overlap = chosen, -1
                for cand in results:
                    try:
                        titles = {(al.name or '').strip().lower()
                                  for al in (self.client.get_artist_albums(cand.id, limit=40) or [])}
                    except Exception:
                        titles = set()
                    overlap = len(owned & titles)
                    if overlap > best_overlap:
                        best, best_overlap = cand, overlap
                chosen = best
        self._store('artist', artist_id, str(chosen.id))
        self.stats['matched'] += 1
        logger.info("Matched artist '%s' -> Yandex ID %s", artist_name, chosen.id)

    def _process_album(self, album_id, album_title, artist_name):
        query = f"{artist_name} {album_title}".strip()
        try:
            results = self.client.search_albums(query, limit=5)
        except Exception:
            results = []
        match = None
        for al in results:
            names = al.artists or []
            if album_title and _names_equivalent(al.name, album_title) and \
               (not artist_name or any(artist_name_matches(artist_name, n) for n in names)):
                match = al
                break
        if match:
            self._store('album', album_id, str(match.id))
            self.stats['matched'] += 1
        else:
            self._mark('album', album_id, 'not_found')
            self.stats['not_found'] += 1

    def _process_track(self, track_id, track_title, artist_name):
        query = f"{artist_name} {track_title}".strip()
        try:
            results = self.client.search_tracks(query, limit=5)
        except Exception:
            results = []
        match = None
        for t in results:
            names = t.artists or []
            if track_title and _names_equivalent(t.name, track_title) and \
               (not artist_name or any(artist_name_matches(artist_name, n) for n in names)):
                match = t
                break
        if match:
            self._store('track', track_id, str(match.id))
            self.stats['matched'] += 1
        else:
            self._mark('track', track_id, 'not_found')
            self.stats['not_found'] += 1

    # --- db writes ---------------------------------------------------------
    _TABLE = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}

    def _store(self, etype, eid, yid):
        table = self._TABLE[etype]
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            cur.execute(f"""
                UPDATE {table} SET
                    yandex_id = ?, yandex_match_status = 'matched',
                    yandex_last_attempted = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (yid, eid))
            conn.commit()
        except Exception as e:
            logger.error("yandex _store %s#%s: %s", etype, eid, e)
            self.stats['errors'] += 1
        finally:
            if conn:
                conn.close()

    def _mark(self, etype, eid, status):
        table = self._TABLE[etype]
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            cur.execute(f"""
                UPDATE {table} SET
                    yandex_match_status = ?, yandex_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, eid))
            conn.commit()
        except Exception as e:
            logger.error("yandex _mark %s#%s: %s", etype, eid, e)
        finally:
            if conn:
                conn.close()

    # --- stats -------------------------------------------------------------
    def _count_pending(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE yandex_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums  WHERE yandex_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks  WHERE yandex_match_status IS NULL AND id IS NOT NULL)
            """)
            r = cur.fetchone()
            return r[0] if r else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()

    def _progress(self) -> Dict[str, Dict[str, int]]:
        out = {}
        conn = None
        try:
            conn = self.db._get_connection()
            cur = conn.cursor()
            for key, table in (('artists', 'artists'), ('albums', 'albums'), ('tracks', 'tracks')):
                cur.execute(f"SELECT COUNT(*), "
                            f"SUM(CASE WHEN yandex_match_status IS NOT NULL THEN 1 ELSE 0 END) FROM {table}")
                row = cur.fetchone()
                total = row[0] or 0
                processed = row[1] or 0
                out[key] = {'matched': processed, 'total': total,
                            'percent': int(processed / total * 100) if total else 0}
            return out
        except Exception:
            return out
        finally:
            if conn:
                conn.close()
