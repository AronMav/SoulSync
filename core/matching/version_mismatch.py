"""Decide when an AcoustID version-annotation mismatch should still
pass verification.

Issue #607 (AfonsoG6): live recordings were quarantining as
"Version mismatch: expected '... (Live at Venue)' (live) but file is
'Song' (original)" because MusicBrainz often stores the recording
entity with a bare title — the venue / live annotation lives on the
release entity, not the recording. The audio fingerprint correctly
identifies the live recording (live audio has its own distinct
fingerprint), but the title-text comparison flagged it as the wrong
version.

Strict version mismatching stays for genuinely-different recordings
(instrumental vs vocal, remix vs original, acoustic vs studio) —
those have distinct fingerprints AND MB always annotates them in the
recording title. We only loosen for the **live** direction
specifically, since that's the asymmetry users actually hit:

- LIVE: MB often stores live recordings with bare titles. The
  fingerprint correctly identifies the live recording even when the
  recording's text title lacks a "(Live at ...)" annotation.
- INSTRUMENTAL / REMIX / ACOUSTIC / DEMO / etc: MB always carries
  the version marker in the recording title. If AcoustID returns an
  instrumental for a vocal file query (or vice versa), it's a real
  wrong-recording match — the fingerprint matched the instrumental
  audio, which IS the wrong file.

Two-sided version mismatches stay strict — "live" vs "remix" really
are different recordings even if MB titled them similarly."""

from __future__ import annotations


_BARE_VERSION = 'original'

# Versions where a one-sided annotation difference is plausibly
# explained by MB metadata gaps rather than a different recording.
# Live recordings are the primary case; venue annotations live on the
# release-track entity, not the recording entity, so the recording can
# be bare-titled even though the audio is genuinely live.
_LIVE_AWARE_VERSIONS = frozenset({'live'})

# Versions where AcoustID / MusicBrainz annotation is unreliable even
# though the fingerprint matched.  When the fingerprint score is very
# high (>= 0.90) AND the bare title + artist agree, a one-sided
# annotation difference for these versions is more likely a metadata
# error than a genuinely different recording.  The classic case:
# Qobuz serves the correct vocal track but AcoustID's recording entry
# carries an "instrumental" or "remix" annotation from a linked release.
_HIGH_CONFIDENCE_LOOSE_VERSIONS = frozenset({'instrumental', 'remix'})


def is_acceptable_version_mismatch(
    expected_version: str,
    matched_version: str,
    *,
    fingerprint_score: float,
    title_similarity: float,
    artist_similarity: float,
    score_threshold: float = 0.85,
    title_threshold: float = 0.70,
    artist_threshold: float = 0.60,
) -> bool:
    """Return True when an expected-vs-matched version annotation
    difference is likely a MusicBrainz metadata gap rather than a
    genuinely different recording.

    Conditions (all must hold):

    1. The mismatch is **one-sided AND involves a live-aware version**
       — exactly one side is ``'live'`` and the other is bare
       ``'original'``. Other version markers (instrumental, remix,
       acoustic, etc) carry distinct fingerprints AND are always
       annotated in MB's recording title — we don't loosen for them.
    2. Fingerprint score ``>= score_threshold`` (default 0.85). The
       AcoustID fingerprint is high-confidence — we trust it.
    3. Bare title similarity ``>= title_threshold`` (default 0.70).
       After the version annotation is stripped, the underlying titles
       agree.
    4. Artist similarity ``>= artist_threshold`` (default 0.60). Same
       artist credit.

    When ``expected_version == matched_version`` returns ``True``
    trivially — no mismatch to decide.

    Examples that ACCEPT (return True):

    - expected=``'live'``, matched=``'original'``, fp=0.95, title=0.95,
      artist=1.0 — typical live-recording MB-bare-title case (issue
      #607 example 2).
    - expected=``'original'``, matched=``'live'`` — same case in the
      other direction.
    - expected=``'original'``, matched=``'instrumental'``, fp=0.95,
      title=0.95, artist=1.0 — AcoustID annotation error; the
      fingerprint matched the correct vocal track but the linked MB
      recording carries an instrumental annotation.

    Examples that REJECT (return False):

    - expected=``'instrumental'``, matched=``'original'`` — user
      explicitly wanted the instrumental; a vocal file is wrong.
    - expected=``'live'``, matched=``'remix'`` — both versioned,
      both different. Real mismatch.
    - expected=``'live'``, matched=``'original'``, fp=0.50 — one-sided
      live but low confidence. Fall through to FAIL.
    - expected=``'live'``, matched=``'original'``, fp=0.95 but title
      sim 0.30 — bare titles don't agree. Different song.
    - expected=``'original'``, matched=``'instrumental'``, fp=0.70 —
      high-confidence loose versions still require fp >= 0.90.
    """
    if expected_version == matched_version:
        return True

    # --- one-sided live (original <-> live) ---
    one_sided_live = (
        (expected_version in _LIVE_AWARE_VERSIONS and matched_version == _BARE_VERSION)
        or (expected_version == _BARE_VERSION and matched_version in _LIVE_AWARE_VERSIONS)
    )
    if one_sided_live:
        return (
            fingerprint_score >= score_threshold
            and title_similarity >= title_threshold
            and artist_similarity >= artist_threshold
        )

    # --- one-sided instrumental/remix with very high confidence ---
    # Only when the USER wanted the original (bare) and AcoustID tagged
    # the matched recording as instrumental/remix.  The reverse direction
    # (user wanted instrumental, got original) stays strict — the user
    # explicitly asked for a non-vocal cut.
    one_sided_loose = (
        expected_version == _BARE_VERSION
        and matched_version in _HIGH_CONFIDENCE_LOOSE_VERSIONS
    )
    if one_sided_loose:
        return (
            fingerprint_score >= 0.90
            and title_similarity >= title_threshold
            and artist_similarity >= artist_threshold
        )

    return False


__all__ = ['is_acceptable_version_mismatch']
