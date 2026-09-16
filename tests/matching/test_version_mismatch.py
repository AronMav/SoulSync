"""Boundary tests for ``core.matching.version_mismatch``.

Pin every shape the version-mismatch escape valve has to handle so
future drift fails here instead of at runtime against a real download:
one-sided bare cases (the live-recording MB-metadata-gap that issue
#607 reported), two-sided real mismatches (live vs remix — keep
strict), high vs low fingerprint score gates, title/artist threshold
gates, defensive paths.
"""

from __future__ import annotations

import pytest

from core.matching.version_mismatch import is_acceptable_version_mismatch


class TestEqualVersions:
    def test_same_version_trivially_accepted(self):
        # Equal version strings — no mismatch to decide. True.
        assert is_acceptable_version_mismatch(
            'live', 'live',
            fingerprint_score=0.0,
            title_similarity=0.0,
            artist_similarity=0.0,
        ) is True

    def test_both_original_accepted(self):
        assert is_acceptable_version_mismatch(
            'original', 'original',
            fingerprint_score=0.0,
            title_similarity=0.0,
            artist_similarity=0.0,
        ) is True


class TestOneSidedBareMismatch:
    """Issue #607 example 2: expected has annotation, AcoustID's MB
    record is bare. Accept when fingerprint + bare titles + artist
    all line up."""

    def test_live_vs_original_high_confidence_accepted(self):
        # Reporter's exact case: "Clarity (Live at ...)" vs "Clarity"
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.95,
            title_similarity=0.95,
            artist_similarity=1.0,
        ) is True

    def test_original_vs_live_high_confidence_accepted(self):
        # Same case in the other direction.
        assert is_acceptable_version_mismatch(
            'original', 'live',
            fingerprint_score=0.95,
            title_similarity=0.95,
            artist_similarity=1.0,
        ) is True

    def test_live_at_thresholds_accepted(self):
        # Exactly at the thresholds for the live-aware case.
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.85,
            title_similarity=0.70,
            artist_similarity=0.60,
        ) is True


class TestNonLiveOneSidedMismatchStaysStrict:
    """Other version markers (instrumental / remix / acoustic / etc)
    have distinct fingerprints AND MB usually annotates them in the
    recording title, so one-sided differences stay a real mismatch in
    most directions — reject regardless of how high the supporting
    scores are.

    ONE deliberate fork exception (commit 83a7fe56): a one-sided
    ``'original' -> 'instrumental'/'remix'`` difference (user asked
    for the bare cut; AcoustID's linked recording carries the marker)
    with fingerprint >= 0.90 and agreeing title/artist is accepted as
    a MusicBrainz/AcoustID annotation error — that valve removed
    ~387/530 false quarantines on a real discography scan.  The
    reverse direction (user explicitly wanted the instrumental/remix,
    file is the vocal) stays strict."""

    def test_remix_vs_original_rejected_at_high_confidence(self):
        assert is_acceptable_version_mismatch(
            'remix', 'original',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False

    def test_instrumental_vs_original_rejected_at_high_confidence(self):
        # The exact case test_acoustid_version_mismatch.py:
        # test_instrumental_returned_for_vocal_request_fails pins —
        # vocal asked, instrumental returned, must FAIL.
        assert is_acceptable_version_mismatch(
            'instrumental', 'original',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False

    def test_original_vs_instrumental_accepted_as_annotation_error(self):
        # 83a7fe56 valve: bare expected, AcoustID recording carries an
        # "instrumental" marker, fingerprint 0.99 with agreeing
        # title/artist -> treated as an MB/AcoustID metadata error.
        assert is_acceptable_version_mismatch(
            'original', 'instrumental',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is True

    def test_acoustic_vs_original_rejected_at_high_confidence(self):
        assert is_acceptable_version_mismatch(
            'acoustic', 'original',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False

    def test_demo_vs_original_rejected(self):
        assert is_acceptable_version_mismatch(
            'demo', 'original',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False


class TestTwoSidedMismatchStaysStrict:
    """Both sides have version markers but they disagree. Real
    different-recording mismatch — must reject regardless of how
    high the other scores are."""

    def test_live_vs_remix_rejected_even_at_max(self):
        assert is_acceptable_version_mismatch(
            'live', 'remix',
            fingerprint_score=1.0,
            title_similarity=1.0,
            artist_similarity=1.0,
        ) is False

    def test_acoustic_vs_instrumental_rejected(self):
        assert is_acceptable_version_mismatch(
            'acoustic', 'instrumental',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False

    def test_live_vs_acoustic_rejected(self):
        assert is_acceptable_version_mismatch(
            'live', 'acoustic',
            fingerprint_score=0.95,
            title_similarity=0.90,
            artist_similarity=1.0,
        ) is False


class TestThresholdGates:
    """One-sided + bare but one of the supporting signals is too weak.
    Reject — fall through to FAIL."""

    def test_low_fingerprint_score_rejected(self):
        # Fingerprint score below threshold. Don't trust it enough.
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.50,
            title_similarity=0.95,
            artist_similarity=1.0,
        ) is False

    def test_low_title_similarity_rejected(self):
        # Bare titles disagree → different songs, not just MB metadata gap.
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.95,
            title_similarity=0.30,
            artist_similarity=1.0,
        ) is False

    def test_low_artist_similarity_rejected(self):
        # Wrong artist — definitely not the same recording.
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.95,
            title_similarity=0.95,
            artist_similarity=0.20,
        ) is False

    def test_just_below_score_threshold_rejected(self):
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.849,  # default threshold 0.85
            title_similarity=0.95,
            artist_similarity=1.0,
        ) is False

    def test_just_below_title_threshold_rejected(self):
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.95,
            title_similarity=0.699,  # default threshold 0.70
            artist_similarity=1.0,
        ) is False

    def test_just_below_artist_threshold_rejected(self):
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.95,
            title_similarity=0.95,
            artist_similarity=0.599,  # default threshold 0.60
        ) is False

    def test_original_vs_instrumental_below_loose_threshold_rejected(self):
        # The annotation-error valve for original -> instrumental needs
        # fp >= 0.90 (83a7fe56); below it the mismatch stays strict.
        assert is_acceptable_version_mismatch(
            'original', 'instrumental',
            fingerprint_score=0.89,  # valve requires >= 0.90
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False


class TestCustomThresholds:
    def test_custom_score_threshold_accepts_when_loosened(self):
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.70,
            title_similarity=0.95,
            artist_similarity=1.0,
            score_threshold=0.65,
        ) is True

    def test_custom_score_threshold_rejects_when_tightened(self):
        assert is_acceptable_version_mismatch(
            'live', 'original',
            fingerprint_score=0.90,
            title_similarity=0.95,
            artist_similarity=1.0,
            score_threshold=0.95,
        ) is False


class TestAdvisoryLabelMismatchAccepted:
    """``(Explicit)``/``(Clean)`` are lyrics-treatment labels on the same
    recording, not version cuts — MusicBrainz carries them inconsistently,
    so comparing them as versions quarantined correct downloads (live case:
    Qobuz "Welcome 2 Detroit (Album Version (Explicit))" vs a bare MB title,
    fingerprint 0.98 / title 1.00 / artist 0.95). Any combination of
    explicit/clean/bare on the two sides must be accepted outright."""

    def test_explicit_vs_original_accepted(self):
        assert is_acceptable_version_mismatch(
            'explicit', 'original',
            fingerprint_score=0.98,
            title_similarity=1.0,
            artist_similarity=0.95,
        ) is True

    def test_original_vs_explicit_accepted(self):
        assert is_acceptable_version_mismatch(
            'original', 'explicit',
            fingerprint_score=0.98,
            title_similarity=1.0,
            artist_similarity=0.95,
        ) is True

    def test_clean_vs_original_accepted(self):
        assert is_acceptable_version_mismatch(
            'clean', 'original',
            fingerprint_score=0.98,
            title_similarity=1.0,
            artist_similarity=0.95,
        ) is True

    def test_clean_vs_explicit_accepted(self):
        # Both are advisory labels on the same recording — the pair is
        # accepted regardless of which side carries which label.
        assert is_acceptable_version_mismatch(
            'clean', 'explicit',
            fingerprint_score=0.98,
            title_similarity=1.0,
            artist_similarity=0.95,
        ) is True

    def test_advisory_vs_genuine_version_stays_strict(self):
        # "(Explicit live)" vs "(Clean)" — one side carries a genuine
        # version marker, the other doesn't: the real version gate applies.
        assert is_acceptable_version_mismatch(
            'explicit', 'live',
            fingerprint_score=0.98,
            title_similarity=1.0,
            artist_similarity=0.95,
        ) is False

    def test_genuine_version_vs_advisory_stays_strict(self):
        # Instrumental vs an advisory-labeled original: real mismatch
        # (wrong cut), the advisory fold must not weaken this.
        assert is_acceptable_version_mismatch(
            'instrumental', 'explicit',
            fingerprint_score=0.99,
            title_similarity=0.99,
            artist_similarity=0.99,
        ) is False
