"""Repair job: verify tracks by fingerprinting against Deezer 30s previews.

Downloads the official Deezer preview for each track, fingerprints both
the preview and the library file with chromaprint (fpcalc), and compares
the raw fingerprints over the overlapping segment.

Catches wrong downloads that duration comparison misses: a 214s file can
contain a completely different song that happens to be the same length.
Especially effective for Russian/Cyrillic music where AcoustID has poor
database coverage — Deezer previews are authoritative.

The job creates findings only — no files are modified or deleted without
user approval.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob

logger = logging.getLogger("repair_jobs.deezer_preview")

# Similarity threshold: above = correct track, below = wrong.
# 0.75 is conservative: same-song fingerprints typically score 0.85+.
SIMILARITY_THRESHOLD = 0.75

# Below this = clear mismatch (flag as warning). Between the two = ambiguous.
CLEAR_MISMATCH_THRESHOLD = 0.60


def _popcount(n: int) -> int:
    """Count set bits in a 32-bit integer."""
    return bin(n).count("1")


def _compare_fingerprints(fp_a: List[int], fp_b: List[int]) -> float:
    """Compare two raw chromaprint fingerprints using sliding window.

    Deezer previews start ~30-120s into the track, not from the
    beginning, so a naive position-0 comparison fails even for
    correct files. We slide the shorter fingerprint across the
    longer one and return the best (highest) similarity.

    Returns bit-level similarity (0.0–1.0).
    """
    if len(fp_a) < 20 or len(fp_b) < 20:
        return 0.0

    # Identify the shorter (preview) and longer (file) fingerprint
    if len(fp_a) <= len(fp_b):
        short, long_fp = fp_a, fp_b
    else:
        short, long_fp = fp_b, fp_a

    short_len = len(short)
    max_offset = len(long_fp) - short_len
    if max_offset <= 0:
        max_offset = 0

    best_sim = 0.0
    # Slide in steps to keep it fast (each step ≈ 0.27s of audio)
    step = 2
    best_offset = 0
    for offset in range(0, max_offset + 1, step):
        matching = 0
        for i in range(short_len):
            matching += 32 - _popcount(long_fp[offset + i] ^ short[i])
        sim = matching / (short_len * 32)
        if sim > best_sim:
            best_sim = sim
            best_offset = offset

    # Refine around the best offset with step=1
    if best_offset > 0:
        for offset in range(max(0, best_offset - step), min(max_offset + 1, best_offset + step + 1)):
            matching = 0
            for i in range(short_len):
                matching += 32 - _popcount(long_fp[offset + i] ^ short[i])
            sim = matching / (short_len * 32)
            if sim > best_sim:
                best_sim = sim

    return best_sim


def _fingerprint_raw(path: str) -> Optional[List[int]]:
    """Get raw chromaprint fingerprint via fpcalc."""
    try:
        result = subprocess.run(
            ["fpcalc", "-raw", path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.strip().split("\n"):
            if line.startswith("FINGERPRINT="):
                raw = line.split("=", 1)[1].strip()
                return [int(x) for x in raw.split(",")]
    except Exception as exc:
        logger.debug("fpcalc failed for %s: %s", path, exc)
    return None


def _get_deezer_track(deezer_id: str) -> Optional[dict]:
    """Fetch track info from Deezer public API."""
    try:
        url = f"https://api.deezer.com/track/{deezer_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "SoulSync/3.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("Deezer lookup failed for %s: %s", deezer_id, exc)
    return None


@register_job
class DeezerPreviewVerifierJob(RepairJob):
    job_id = "deezer_preview_verifier"
    display_name = "Deezer Preview Verifier"
    description = (
        "Verifies tracks by comparing audio fingerprints against official "
        "Deezer 30s previews. Catches wrong downloads that duration checks miss."
    )
    help_text = (
        "Downloads the official 30-second preview from Deezer for each track, "
        "fingerprints both the preview and your library file with chromaprint, "
        "and compares the fingerprints.\n\n"
        "This catches cases where:\n"
        "• SoulSeek delivered a completely different song with matching duration\n"
        "• The wrong version (remix, cover, instrumental) was downloaded\n"
        "• Tags look correct but the audio doesn't match\n\n"
        "Especially effective for Russian/Cyrillic music where AcoustID has "
        "poor database coverage — Deezer previews are authoritative.\n\n"
        "Requirements: tracks must have a Deezer ID (972/1243 in your library).\n\n"
        "Settings:\n"
        "• similarity_threshold: above this = correct (default 0.75)\n"
        "• clear_mismatch_threshold: below this = definitely wrong (default 0.60)\n"
        "• batch_size: tracks per scan run (each track needs a preview download)\n"
    )
    icon = "repair-icon-preview"
    default_enabled = True
    default_interval_hours = 48
    default_settings = {
        "similarity_threshold": 0.75,
        "clear_mismatch_threshold": 0.60,
        "batch_size": 100,
    }
    setting_options: Dict[str, list] = {}
    auto_fix = False

    def _get_settings(self, context: JobContext) -> dict:
        cm = getattr(context, "config_manager", None)
        if cm is None:
            return dict(self.default_settings)
        result = {}
        for key, default in self.default_settings.items():
            result[key] = cm.get(self.get_config_key(key), default)
        return result

    def _setting_float(self, context: JobContext, key: str, default: float) -> float:
        cm = getattr(context, "config_manager", None)
        if cm is None:
            return default
        try:
            return float(cm.get(self.get_config_key(key), default) or default)
        except (TypeError, ValueError):
            return default

    def _setting_int(self, context: JobContext, key: str, default: int) -> int:
        cm = getattr(context, "config_manager", None)
        if cm is None:
            return default
        try:
            return int(cm.get(self.get_config_key(key), default) or default)
        except (TypeError, ValueError):
            return default

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM tracks "
                    "WHERE file_path IS NOT NULL AND file_path != '' "
                    "AND deezer_id IS NOT NULL AND deezer_id != ''"
                )
                return (cursor.fetchone() or [0])[0]
            finally:
                conn.close()
        except Exception:
            return 0

    def _resolve_file_path(self, file_path: str, context: JobContext,
                          title: str = "", artist: str = "") -> Optional[str]:
        """Resolve the DB file path to a path this process can read.

        Tries: path resolver -> common prefixes -> fuzzy glob by title.
        """
        if not file_path:
            return None

        # 1. Try the shared path resolver
        try:
            from core.library.path_resolver import resolve_library_file_path
            resolved = resolve_library_file_path(
                file_path,
                transfer_folder=getattr(context, "transfer_folder", None),
                config_manager=getattr(context, "config_manager", None),
            )
            if resolved and os.path.isfile(resolved):
                return resolved
        except Exception:  # noqa: S110 - best-effort reporting
            pass

        # 2. Try common prefixes
        for prefix in ["/music", "/music/SoulSync", ""]:
            full = os.path.join(prefix, file_path) if not os.path.isabs(file_path) else file_path
            if os.path.isfile(full):
                return full

        # 3. Fuzzy fallback: glob search by title (handles renamed/moved files)
        if title:
            import glob as _glob
            safe = title.replace("/", "_").replace("\\", "_").strip()
            if safe:
                for ext in ("flac", "mp3", "m4a"):
                    matches = _glob.glob(f"/music/**/*{safe}*.{ext}", recursive=True)
                    if matches:
                        return matches[0]
        return None

    def _verify_track(
        self, row: Dict[str, Any], context: JobContext,
        sim_threshold: float, mismatch_threshold: float,
    ) -> Tuple[Optional[float], Optional[str], Optional[dict]]:
        """Verify a single track. Returns (similarity, source, details) or (None, None, None)."""
        deezer_id = row.get("deezer_id")
        if not deezer_id:
            return None, None, None

        # 1. Get Deezer track info + preview URL
        track_info = _get_deezer_track(str(deezer_id))
        if not track_info:
            return None, None, None
        preview_url = track_info.get("preview")
        if not preview_url:
            return None, None, None

        # 2. Resolve file path
        file_path = self._resolve_file_path(
            row.get("file_path", ""), context,
            title=row.get("title", ""), artist=row.get("artist_name", ""),
        )
        if not file_path:
            return None, None, None

        # 3. Fingerprint the library file
        fp_file = _fingerprint_raw(file_path)
        if not fp_file:
            return None, None, None

        # 4. Download preview and fingerprint it
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3", dir="/tmp")
            os.close(fd)
            # Use timeout to prevent hangs on slow/expired CDN URLs
            import socket as _sock
            _sock.setdefaulttimeout(15)
            try:
                urllib.request.urlretrieve(preview_url, tmp_path)
            except Exception as exc:
                logger.debug("preview download failed for %s: %s", deezer_id, exc)
                return None, None, None
            fp_preview = _fingerprint_raw(tmp_path)
            if not fp_preview:
                return None, None, None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # 5. Compare fingerprints
        similarity = _compare_fingerprints(fp_preview, fp_file)

        details = {
            "deezer_id": deezer_id,
            "deezer_title": track_info.get("title"),
            "deezer_artist": track_info.get("artist", {}).get("name"),
            "deezer_duration": track_info.get("duration"),
            "similarity": round(similarity, 4),
            "fp_items_compared": min(len(fp_preview), len(fp_file)),
        }

        return similarity, "Deezer Preview", details

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        sim_threshold = self._setting_float(context, "similarity_threshold", SIMILARITY_THRESHOLD)
        mismatch_threshold = self._setting_float(context, "clear_mismatch_threshold", CLEAR_MISMATCH_THRESHOLD)
        batch_size = self._setting_int(context, "batch_size", 100)

        # Get checkpoint for resumable scanning
        cm = getattr(context, "config_manager", None)
        checkpoint = 0
        if cm:
            try:
                checkpoint = int(cm.get(self.get_config_key("checkpoint"), 0) or 0)
            except (TypeError, ValueError):
                checkpoint = 0

        # Load tracks with deezer_id
        conn = context.db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT t.id, t.title, t.duration, t.file_path,
                       t.deezer_id, t.deezer_match_status,
                       ar.name AS artist_name, ar.thumb_url AS artist_thumb,
                       al.title AS album_title, al.thumb_url AS album_thumb,
                       ar.id AS artist_row_id
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND t.deezer_id IS NOT NULL AND t.deezer_id != ''
                ORDER BY t.created_at
                LIMIT ? OFFSET ?
                """,
                (batch_size, checkpoint),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        total = len(rows)
        if context.report_progress:
            try:
                context.report_progress(
                    phase=f"Verifying {total} tracks via Deezer previews…",
                    total=total,
                )
            except Exception:  # noqa: S110 - best-effort reporting
                pass

        for i, row in enumerate(rows):
            if context.check_stop() or context.wait_if_paused():
                break
            result.scanned += 1

            title = row["title"] or "Unknown"
            artist = row["artist_name"] or "Unknown"

            if context.update_progress:
                try:
                    context.update_progress(i + 1, total)
                except Exception:  # noqa: S110 - best-effort reporting
                    pass
            if context.report_progress:
                try:
                    context.report_progress(
                        phase=f"Verifying {i + 1}/{total} tracks…",
                        log_line=f"{artist} — {title}",
                        scanned=i + 1, total=total,
                    )
                except Exception:  # noqa: S110 - best-effort reporting
                    pass

            similarity, source, details = self._verify_track(
                row, context, sim_threshold, mismatch_threshold,
            )

            if similarity is None:
                result.skipped += 1
                continue

            if similarity >= sim_threshold:
                # Correct track
                continue

            # Mismatch or ambiguous — create finding
            if context.create_finding:
                severity = "warning" if similarity < mismatch_threshold else "info"
                finding_type = "preview_mismatch" if similarity < mismatch_threshold else "preview_ambiguous"
                verdict = "WRONG TRACK" if similarity < mismatch_threshold else "POSSIBLY WRONG"
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type=finding_type,
                        severity=severity,
                        entity_type="track",
                        entity_id=str(row["id"]),
                        file_path=row["file_path"],
                        title=f"{verdict}: {artist} - {title}",
                        description=(
                            f'Audio fingerprint matches the Deezer preview with only '
                            f'{similarity:.0%} similarity. Expected >{sim_threshold:.0%}. '
                            f'This is likely the wrong track.\n\n'
                            f'Deezer says: {details.get("deezer_title", "?")} '
                            f'by {details.get("deezer_artist", "?")}'
                        ),
                        details={
                            "track_id": row["id"],
                            "title": row["title"],
                            "artist": row["artist_name"],
                            "album": row["album_title"],
                            "album_thumb_url": row["album_thumb"],
                            "artist_thumb_url": row["artist_thumb"],
                            "artist_id": row["artist_row_id"],
                            "similarity": round(similarity, 4),
                            "threshold": sim_threshold,
                            "verdict": verdict,
                            "source": source,
                            "source_details": details,
                            "original_path": row["file_path"],
                            # Fields for _fix_acoustid_mismatch redownload handler
                            "expected_title": row["title"] or "",
                            "expected_artist": row["artist_name"] or "",
                            "album_title": row["album_title"] or "",
                        },
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as exc:
                    logger.debug("create_finding failed for track %s: %s", row["id"], exc)
                    result.errors += 1

        # Update checkpoint
        if cm and total > 0:
            new_checkpoint = checkpoint + total
            try:
                cm.set(self.get_config_key("checkpoint"), new_checkpoint)
            except Exception:  # noqa: S110 - best-effort reporting
                pass

        return result
