"""Yandex Music download client — download lossless FLAC from Yandex Music.

Follows the same DownloadSourcePlugin contract as the Deezer / Tidal / Qobuz
clients: search via the `yandex_music` library, download via the proven
`yandex-music-downloader` CLI (quality 2 = native FLAC), then hand the file to
SoulSync's shared post-processing pipeline (integrity → AcoustID → metadata
enrichment → organize into the library).

Auth: a Yandex Music OAuth token (Я.Плюс required for lossless). Read from the
`yandex.token` config setting or the `YANDEX_MUSIC_TOKEN` env var.

This source is RU-native and paid-via-rubles — a reliable lossless fallback that
does not depend on the fragile public Qobuz proxies.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from core.download_plugins.base import DownloadSourcePlugin
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult
from utils.logging_config import get_logger

logger = get_logger("yandex_download")

# yandex-music-downloader quality codes: 0=AAC64, 1=AAC192, 2=FLAC(lossless)
_YMDL_QUALITY_FLAC = "2"
# Minimum valid file size (100KB — anything smaller is an error/placeholder)
_MIN_FILE_SIZE = 100 * 1024
# FLAC ≈ 1411 kbps CD-quality; used to estimate size + stamp quality
_FLAC_BITRATE = 1411
_FLAC_SAMPLE_RATE = 44100
_FLAC_BIT_DEPTH = 16


class YandexDownloadClient(DownloadSourcePlugin):
    """Yandex Music download source (search: yandex_music lib, download: ymdl CLI)."""

    def __init__(self, download_path: str = None):
        from config.settings import config_manager
        self._config = config_manager

        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')
        self.download_path = Path(download_path)
        try:
            self.download_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"Could not verify download path {self.download_path}: {e}")

        # Engine reference is populated by set_engine() at registration time.
        self._engine = None
        # Shutdown check callback (set by web_server).
        self.shutdown_check = None

        # Credentials / tooling — config first, env fallback (mirrors musicdl).
        self._token = (config_manager.get('yandex.token', '')
                       or os.environ.get('YANDEX_MUSIC_TOKEN', ''))
        self._ymdl_bin = (config_manager.get('yandex.ymdl_bin', '')
                          or os.environ.get('YANDEX_YMDL_BIN', '')
                          or 'yandex-music-downloader')

        # Lazy yandex_music.Client (built on first use — keeps import + init
        # off the hot path and out of registry construction).
        self._client = None
        self._client_lock = threading.Lock()

        logger.info(f"Yandex download client initialized (download path: {self.download_path}, "
                    f"token: {'set' if self._token else 'MISSING'})")

    # ─── Lifecycle wiring ────────────────────────────────────────────
    def set_engine(self, engine):
        """Engine callback — wires the central thread worker + state store."""
        self._engine = engine

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def is_configured(self) -> bool:
        """True iff a Yandex token is present (Я.Плюс needed for lossless)."""
        return bool(self._token)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._token:
            return None
        with self._client_lock:
            if self._client is None:
                try:
                    from yandex_music import Client
                    self._client = Client(self._token).init()
                except Exception as e:
                    logger.error(f"Yandex client init failed: {e}")
                    return None
        return self._client

    async def check_connection(self) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._get_client() is not None)

    # ─── Search ──────────────────────────────────────────────────────
    async def search(self, query: str, timeout: int = None,
                     progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: str) -> Tuple[List[TrackResult], List[AlbumResult]]:
        client = self._get_client()
        if client is None:
            logger.warning("Yandex not configured/authenticated — cannot search")
            return [], []
        try:
            res = client.search(query)
            block = getattr(res, 'tracks', None)
            items = (getattr(block, 'results', None) or []) if block else []
            results: List[TrackResult] = []
            for t in items[:30]:
                tid = str(getattr(t, 'id', '') or '')
                if not tid:
                    continue
                artists = ', '.join(a.name for a in (getattr(t, 'artists', None) or []) if getattr(a, 'name', None))
                title = getattr(t, 'title', '') or ''
                albums = getattr(t, 'albums', None) or []
                album = (getattr(albums[0], 'title', '') if albums else '') or ''
                dur_ms = getattr(t, 'duration_ms', 0) or 0
                dur_s = dur_ms // 1000
                est_size = dur_s * 176400  # ~1411 kbps FLAC
                tr = TrackResult(
                    username='yandex',
                    filename=f"{tid}||{artists} - {title}",
                    size=est_size,
                    bitrate=_FLAC_BITRATE,
                    duration=dur_ms,
                    quality='flac',
                    free_upload_slots=999,   # streaming — no P2P constraints
                    upload_speed=999999,
                    queue_length=0,
                    artist=artists,
                    title=title,
                    album=album,
                    track_number=None,
                    sample_rate=_FLAC_SAMPLE_RATE,
                    bit_depth=_FLAC_BIT_DEPTH,
                )
                results.append(tr)
            logger.info(f"Yandex search for '{query}' returned {len(results)} results")
            return results, []
        except Exception as e:
            logger.error(f"Yandex search failed: {e}")
            return [], []

    # ─── Download ────────────────────────────────────────────────────
    async def download(self, username: str, filename: str,
                       file_size: int = 0) -> Optional[str]:
        """Start a download. Returns download_id immediately."""
        if not self._token:
            logger.error("Yandex not configured — cannot download")
            return None
        if self._engine is None:
            raise RuntimeError("Yandex client has no engine reference — cannot dispatch download")

        # filename: "track_id||display_name"
        parts = filename.split('||', 1)
        track_id = parts[0]
        display_name = parts[1] if len(parts) > 1 else f"Track {track_id}"

        return self._engine.worker.dispatch(
            source_name='yandex',
            target_id=track_id,
            display_name=display_name,
            original_filename=filename,
            impl_callable=self._download_sync,
            extra_record_fields={
                'track_id': track_id,
                'display_name': display_name,
                'size': file_size,
                'error': None,
            },
            thread_name=f'yandex-dl-{track_id}',
        )

    def _set_error(self, download_id: str, message) -> None:
        if self._engine is None:
            return
        msg = message if isinstance(message, str) else str(message)
        self._engine.update_record('yandex', download_id, {'error': msg})

    def _is_cancelled(self, download_id: str) -> bool:
        if self._engine is None:
            return False
        record = self._engine.get_record('yandex', download_id)
        return record is not None and record.get('state') == 'Cancelled'

    def _download_sync(self, download_id: str, track_id: str, display_name: str) -> Optional[str]:
        """Download FLAC via the yandex-music-downloader CLI into an isolated
        work dir, then flatten the single .flac into download_path."""
        if self.shutdown_check and self.shutdown_check():
            if self._engine is not None:
                self._engine.update_record('yandex', download_id, {'state': 'Aborted'})
            return None

        work = self.download_path / f"_yandex_{download_id[:8]}"
        try:
            work.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._set_error(download_id, f'workdir error: {e}')
            return None

        cmd = [
            self._ymdl_bin,
            '--token', self._token,
            '--quality', _YMDL_QUALITY_FLAC,
            '--embed-cover',
            '--dir', str(work),
            '--track-id', str(track_id),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            shutil.rmtree(work, ignore_errors=True)
            self._set_error(download_id, 'ymdl timeout')
            return None
        except Exception as e:
            shutil.rmtree(work, ignore_errors=True)
            self._set_error(download_id, f'ymdl error: {e}')
            return None

        if self._is_cancelled(download_id):
            shutil.rmtree(work, ignore_errors=True)
            return None

        # Yandex delivers lossless as FLAC-in-MP4 → ymd writes a .m4a (not native .flac).
        # Find either, then remux the .m4a to native .flac (lossless, tags preserved) so the
        # rest of SoulSync's pipeline (quality guard / AcoustID) sees a real FLAC.
        produced = sorted(glob.glob(str(work / '**' / '*.flac'), recursive=True)
                          + glob.glob(str(work / '**' / '*.m4a'), recursive=True))
        if not produced:
            tail = (proc.stderr or proc.stdout or 'no audio produced').strip().splitlines()[-1:]
            shutil.rmtree(work, ignore_errors=True)
            self._set_error(download_id, f'ymdl rc={proc.returncode}: {tail}')
            return None

        src = produced[0]
        if src.lower().endswith('.m4a'):
            remuxed = src[:-4] + '.flac'
            try:
                rp = subprocess.run(['ffmpeg', '-y', '-i', src, '-map', '0:a',
                                     '-c:a', 'flac', '-map_metadata', '0', remuxed],
                                    capture_output=True, text=True, timeout=600)
            except Exception as e:
                shutil.rmtree(work, ignore_errors=True)
                self._set_error(download_id, f'remux error: {e}')
                return None
            if rp.returncode == 0 and os.path.isfile(remuxed):
                try:
                    os.remove(src)
                except OSError:
                    pass
                src = remuxed
            else:
                tail = (rp.stderr or '').strip().splitlines()[-1:]
                shutil.rmtree(work, ignore_errors=True)
                self._set_error(download_id, f'remux rc={rp.returncode}: {tail}')
                return None
        safe_name = self._sanitize_filename(display_name)
        out_path = str(self.download_path / f"{safe_name}.flac")
        moved = True
        try:
            if os.path.realpath(src) != os.path.realpath(out_path):
                shutil.move(src, out_path)
            else:
                out_path = src
                moved = False
        except Exception as e:
            logger.warning(f"Yandex flatten-move failed ({e}); using nested path")
            out_path = src
            moved = False
        if moved:
            shutil.rmtree(work, ignore_errors=True)

        try:
            file_size = os.path.getsize(out_path)
        except OSError:
            file_size = 0
        if file_size < _MIN_FILE_SIZE:
            logger.warning(f"Yandex file too small ({file_size} bytes): {out_path}")
            try:
                os.remove(out_path)
            except OSError:
                pass
            self._set_error(download_id, f'File too small ({file_size} bytes)')
            return None

        if self._engine is not None:
            self._engine.update_record('yandex', download_id, {
                'size': file_size,
                'transferred': file_size,
                'progress': 99.9,
            })
        logger.info(f"Yandex download complete: {out_path} ({file_size / 1048576:.1f} MB, flac)")
        return out_path

    # ─── Download Status ─────────────────────────────────────────────
    def _record_to_status(self, record: dict) -> DownloadStatus:
        return DownloadStatus(
            id=record['id'],
            filename=record['filename'],
            username=record['username'],
            state=record['state'],
            progress=record['progress'],
            size=record.get('size', 0),
            transferred=record.get('transferred', 0),
            speed=record.get('speed', 0),
            file_path=record.get('file_path'),
        )

    async def get_all_downloads(self) -> List[DownloadStatus]:
        if self._engine is None:
            return []
        return [self._record_to_status(r)
                for r in self._engine.iter_records_for_source('yandex')]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        if self._engine is None:
            return None
        record = self._engine.get_record('yandex', download_id)
        return self._record_to_status(record) if record is not None else None

    async def cancel_download(self, download_id: str, username: str = None,
                              remove: bool = False) -> bool:
        if self._engine is None:
            return False
        if self._engine.get_record('yandex', download_id) is None:
            return False
        self._engine.update_record('yandex', download_id, {'state': 'Cancelled'})
        if remove:
            self._engine.remove_record('yandex', download_id)
        return True

    async def clear_all_completed_downloads(self) -> bool:
        if self._engine is None:
            return True
        terminal = {'Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted'}
        for record in list(self._engine.iter_records_for_source('yandex')):
            if record.get('state') in terminal:
                self._engine.remove_record('yandex', record['id'])
        return True

    # ─── Utilities ───────────────────────────────────────────────────
    @staticmethod
    def _sanitize_filename(name: str) -> str:
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = name.strip('. ')
        return name[:200] if name else 'unknown'
