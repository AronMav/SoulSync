"""Yandex Music metadata provider for the watchlist scanner.

Plugs into ``get_client_for_source('yandex')``. Implements the minimal metadata
client interface the watchlist scanner consumes:

    * ``search_artists(query, limit)``    -> resolve an artist name to a Yandex id
    * ``get_artist(artist_id)``           -> artist image payload
    * ``get_artist_albums(artist_id...)`` -> album objects carrying ``release_date``
    * ``get_album(album_id)``             -> dict with embedded track items

Yandex has the most complete Russian catalogue and matches Russian artists
accurately, unlike Deezer/MusicBrainz (which mis-resolved e.g. the US singer
"Meiko" to an unrelated Japanese artist). Artist resolution is name-based and
deliberately STRICT — only an exact (case-insensitive) name match is accepted,
so an ambiguous name falls back to the next source rather than pulling a wrong
artist's discography.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.settings import config_manager

logger = logging.getLogger(__name__)


@dataclass
class YArtist:
    id: str
    name: str


@dataclass
class YAlbum:
    id: str
    name: str
    release_date: str
    album_type: str
    total_tracks: int
    images: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class YTrack:
    """Track search result — mirrors the metadata-interface Track dataclass
    (iTunes/Spotify/Deezer shape): ``artists`` is a list of NAMES, ``album`` a
    name string."""
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int = 0
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, Any]] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None


@dataclass
class YSearchAlbum:
    """Album search result — mirrors the metadata-interface Album dataclass."""
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, Any]] = None
    explicit: Optional[bool] = None


def _cover_images(cover_uri: Optional[str]) -> List[Dict[str, Any]]:
    if not cover_uri:
        return []
    url = "https://" + str(cover_uri).replace("%%", "1000x1000")
    return [{"url": url, "height": 1000, "width": 1000}]


def _norm_date(release_date: Any, year: Any) -> str:
    # Yandex release_date is ISO like '2026-05-15T00:00:00+03:00'; the scanner's
    # is_album_after_timestamp handles 'YYYY-MM-DD' (len 10) and 'YYYY' (len 4).
    if release_date:
        return str(release_date)[:10]
    if year:
        return str(year)
    return ""


def _norm_type(t: Optional[str]) -> str:
    if t in ("single", "ep", "compilation"):
        return t
    return "album"


class YandexMetadataClient:
    """Minimal Yandex metadata client used for watchlist discovery."""

    def __init__(self, token: str):
        self._token = token
        self._client = None
        self._init_lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._init_lock:
                if self._client is None:
                    import yandex_music
                    self._client = yandex_music.Client(self._token).init()
        return self._client

    # --- artist resolution -------------------------------------------------
    def search_artists(self, query: str, limit: int = 10, **kwargs) -> List[YArtist]:
        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('yandex')
        try:
            res = self.client.search(query, type_="artist")
            results = res.artists.results if (res and res.artists) else []
        except Exception as e:
            logger.debug("yandex search_artists %r: %s", query, e)
            return []
        q = (query or "").strip().lower()
        # STRICT: only exact (case-insensitive) name matches — avoids grabbing a
        # more popular but unrelated artist. No exact match -> let the scanner
        # fall through to the next source.
        exact = [YArtist(id=str(a.id), name=a.name or "")
                 for a in (results or []) if (a.name or "").strip().lower() == q]
        return exact[:limit]

    def get_artist(self, artist_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('yandex')
        try:
            info = self.client.artists_brief_info(artist_id)
            a = info.artist
            cover = getattr(a, "cover", None)
            return {
                "id": str(artist_id),
                "name": getattr(a, "name", ""),
                "images": _cover_images(cover.uri if cover else None),
                "_source": "yandex",
            }
        except Exception as e:
            logger.debug("yandex get_artist %s: %s", artist_id, e)
            return None

    # --- discography -------------------------------------------------------
    def get_artist_albums(self, artist_id: str, album_type: str = "album,single",
                          limit: int = 50, **kwargs) -> Optional[List[YAlbum]]:
        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('yandex')
        try:
            artists = self.client.artists(artist_id)
            if not artists:
                return None
            # Artist.get_albums() returns the FULL own discography (direct_albums),
            # newest first, with release dates — unlike artists_brief_info().albums
            # which only surfaces ~6 popular releases and can MISS the newest one.
            # Deliberately not the artist's guest/featured appearances, so a
            # watched artist never pulls whole albums by OTHER artists.
            raw = list(artists[0].get_albums(page_size=100) or [])
        except Exception as e:
            logger.debug("yandex get_artist_albums %s: %s", artist_id, e)
            return None  # None => this source failed; scanner tries the next one.
        out: List[YAlbum] = []
        seen = set()
        for al in raw:
            if al.id in seen:
                continue
            seen.add(al.id)
            out.append(YAlbum(
                id=str(al.id),
                name=al.title or "",
                release_date=_norm_date(getattr(al, "release_date", None), getattr(al, "year", None)),
                album_type=_norm_type(getattr(al, "type", None)),
                total_tracks=getattr(al, "track_count", 0) or 0,
                images=_cover_images(getattr(al, "cover_uri", None)),
            ))
        return out[:limit] if limit else out

    def get_album(self, album_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('yandex')
        try:
            full = self.client.albums_with_tracks(album_id)
        except Exception as e:
            logger.debug("yandex get_album %s: %s", album_id, e)
            return None
        if not full:
            return None
        items: List[Dict[str, Any]] = []
        for vol in (full.volumes or []):
            for t in vol:
                items.append({
                    "id": str(t.id),
                    "name": t.title or "",
                    "artists": [{"name": ar.name, "id": str(ar.id)} for ar in (t.artists or [])],
                    "duration_ms": getattr(t, "duration_ms", 0) or 0,
                    "external_urls": {"yandex": f"https://music.yandex.ru/album/{album_id}/track/{t.id}"},
                    "_source": "yandex",
                })
        return {
            "id": str(album_id),
            "name": full.title or "",
            "images": _cover_images(getattr(full, "cover_uri", None)),
            "artists": [{"name": ar.name, "id": str(ar.id)} for ar in (full.artists or [])],
            "release_date": _norm_date(getattr(full, "release_date", None), getattr(full, "year", None)),
            "total_tracks": getattr(full, "track_count", 0) or len(items),
            "album_type": _norm_type(getattr(full, "type", None)),
            "external_urls": {"yandex": f"https://music.yandex.ru/album/{album_id}"},
            "uri": f"yandex:album:{album_id}",
            "_source": "yandex",
            "tracks": {"items": items, "total": len(items)},
        }


    # --- graceful degradation ---------------------------------------------
    # The watchlist scanner only needs search_artists/get_artist/
    # get_artist_albums/get_album (above). Other metadata flows (free-text
    # album/track search, import matching) call the richer provider interface.
    # Yandex returns empty for those so that selecting it as the app-wide
    # primary source never crashes — those flows fall back to the configured
    # secondary provider instead. (Yandex here is purpose-built for new-release
    # discovery, not free-text catalogue search.)
    @staticmethod
    def _cover_url(cover_uri):
        imgs = _cover_images(cover_uri)
        return imgs[0]["url"] if imgs else None

    def _yt_track(self, t) -> YTrack:
        albs = getattr(t, "albums", None) or []
        alb = albs[0] if albs else None
        alb_id = str(alb.id) if alb else ""
        cover = getattr(t, "cover_uri", None) or (getattr(alb, "cover_uri", None) if alb else None)
        return YTrack(
            id=str(t.id),
            name=t.title or "",
            artists=[ar.name for ar in (t.artists or [])] or ["Unknown Artist"],
            album=(getattr(alb, "title", "") if alb else "") or "",
            duration_ms=getattr(t, "duration_ms", 0) or 0,
            external_urls={"yandex": f"https://music.yandex.ru/album/{alb_id}/track/{t.id}"},
            image_url=self._cover_url(cover),
            release_date=_norm_date(getattr(alb, "release_date", None) if alb else None,
                                    getattr(alb, "year", None) if alb else None),
            disc_number=1,
            album_type=_norm_type(getattr(alb, "type", None) if alb else None),
            total_tracks=getattr(alb, "track_count", None) if alb else None,
        )

    def search_tracks(self, query, limit=20, **k):
        try:
            res = self.client.search(query, type_="track")
            results = res.tracks.results if (res and res.tracks) else []
        except Exception as e:
            logger.debug("yandex search_tracks %r: %s", query, e)
            return []
        return [self._yt_track(t) for t in (results or [])[:limit]]

    def search_track(self, query, limit=20, **k):
        return self.search_tracks(query, limit=limit)

    def search_albums(self, query, limit=20, **k):
        try:
            res = self.client.search(query, type_="album")
            results = res.albums.results if (res and res.albums) else []
        except Exception as e:
            logger.debug("yandex search_albums %r: %s", query, e)
            return []
        out = []
        for al in (results or [])[:limit]:
            out.append(YSearchAlbum(
                id=str(al.id),
                name=al.title or "",
                artists=[ar.name for ar in (al.artists or [])] or ["Unknown Artist"],
                release_date=_norm_date(getattr(al, "release_date", None), getattr(al, "year", None)),
                total_tracks=getattr(al, "track_count", 0) or 0,
                album_type=_norm_type(getattr(al, "type", None)),
                image_url=self._cover_url(getattr(al, "cover_uri", None)),
                external_urls={"yandex": f"https://music.yandex.ru/album/{al.id}"},
            ))
        return out

    def search_album(self, query, limit=20, **k):
        return self.search_albums(query, limit=limit)

    def get_track_details(self, track_id, *a, **k):
        try:
            tracks = self.client.tracks([track_id])
            if not tracks:
                return None
            return self._yt_track(tracks[0])
        except Exception as e:
            logger.debug("yandex get_track_details %s: %s", track_id, e)
            return None

    def get_artist_top_tracks(self, artist_id, limit=10, *a, **k):
        try:
            info = self.client.artists_brief_info(artist_id)
            popular = getattr(info, "popular_tracks", None) or []
            return [self._yt_track(t) for t in popular[:limit]]
        except Exception as e:
            logger.debug("yandex get_artist_top_tracks %s: %s", artist_id, e)
            return []

    def get_artist_info(self, artist_id, *a, **k):
        return self.get_artist(artist_id)


_client_singleton: Optional[YandexMetadataClient] = None
_singleton_lock = threading.Lock()


def get_yandex_metadata_client() -> Optional[YandexMetadataClient]:
    """Return a shared client, or None when no Yandex token is configured."""
    global _client_singleton
    token = config_manager.get("yandex.token") or ""
    if not token:
        return None
    if _client_singleton is None or _client_singleton._token != token:
        with _singleton_lock:
            if _client_singleton is None or _client_singleton._token != token:
                _client_singleton = YandexMetadataClient(token)
    return _client_singleton
