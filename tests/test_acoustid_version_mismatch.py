"""AcoustID verification rejects version mismatches (instrumental / live / etc).

Discord report (corruption [BWC]): downloads coming through as instrumentals
when the user expected vocal versions. Slipped past AcoustID verification
because the ``_normalize`` step strips parentheticals and version-suffix
tags ("(Instrumental)", "- Live", etc) so that legitimate name variations
don't fail the title-similarity check. Side effect: "Song Name" and
"Song Name (Instrumental)" both normalize to "song name", title sim is
1.0, file passes verification despite being the wrong cut.

Fix: detect the version label on each side BEFORE normalization runs.
If the expected and matched versions disagree, return FAIL — the
fingerprint did identify a real song, just not the version the caller
asked for.

Fork note (83a7fe56): the one-sided ``original -> instrumental/remix``
direction was later relaxed into an annotation-error valve — with
fingerprint >= 0.90 and agreeing title/artist the download is
accepted, because AcoustID/MB mis-annotate the linked recording often
enough that the strict gate produced ~387/530 false quarantines on a
real discography scan.  The tests below pin the FORK contract
(accepting the annotation error); the reverse direction stays strict.

Reuses ``MusicMatchingEngine.detect_version_type`` so the same patterns
that the pre-download Soulseek matcher uses also drive post-download
verification (no duplicated regex tables).
"""

from __future__ import annotations

import pytest

from core.acoustid_verification import (
    AcoustIDVerification,
    VerificationResult,
)


@pytest.fixture
def verifier():
    """Verifier with the network/fingerprint side stubbed so tests can
    drive the title/artist comparison logic directly."""
    v = AcoustIDVerification()

    class _StubClient:
        def is_available(self):
            return True, "available"

        def lookup_with_status(self, path):
            return None  # tests inject via _stub_lookup below

    v.acoustid_client = _StubClient()
    return v


def _stub_lookup(verifier, *, recordings, best_score=0.95):
    verifier.acoustid_client.lookup_with_status = lambda path: {
        "recordings": recordings,
        "best_score": best_score,
        "recording_mbids": [r.get("id") for r in recordings if r.get("id")],
    }


# ---------------------------------------------------------------------------
# The headline bug — instrumental returned where vocal was expected.
# ---------------------------------------------------------------------------


def test_instrumental_annotation_with_high_confidence_passes(verifier):
    """User asked for the bare cut; the fingerprint matched an
    "(Instrumental)"-annotated recording with fp 0.95 and agreeing
    title/artist.  Fork contract (83a7fe56): AcoustID/MB annotation
    errors are accepted rather than quarantined — the track PASSES the
    version gate.  Below fp 0.90 the mismatch stays strict."""
    _stub_lookup(verifier, recordings=[
        {"title": "In My Feelings (Instrumental)", "artist": "Drake"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "In My Feelings",
        "Drake",
    )
    assert result == VerificationResult.PASS


def test_instrumental_request_with_vocal_file_fails(verifier):
    """Symmetric case: user asked for the instrumental cut explicitly,
    file's fingerprint matched the regular vocal version. Also FAIL —
    they're different recordings."""
    _stub_lookup(verifier, recordings=[
        {"title": "In My Feelings", "artist": "Drake"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "In My Feelings (Instrumental)",
        "Drake",
    )
    assert result == VerificationResult.FAIL


# ---------------------------------------------------------------------------
# Different-version mismatches (live vs acoustic, etc) — also FAIL.
# ---------------------------------------------------------------------------


def test_different_versions_disagree_fails(verifier):
    """Caller asked for the live cut; file is the acoustic cut. Both
    are non-original versions, but they're different non-original
    versions — must FAIL."""
    _stub_lookup(verifier, recordings=[
        {"title": "Hello (Acoustic)", "artist": "Adele"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "Hello (Live at Wembley)",
        "Adele",
    )
    assert result == VerificationResult.FAIL


# ---------------------------------------------------------------------------
# Regression guards — version match must NOT cause false-FAIL.
# ---------------------------------------------------------------------------


def test_original_to_original_passes(verifier):
    """Plain track to plain track — no version on either side. Verify
    the version gate doesn't get in the way of the normal happy path."""
    _stub_lookup(verifier, recordings=[
        {"title": "Bohemian Rhapsody", "artist": "Queen"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "Bohemian Rhapsody",
        "Queen",
    )
    assert result == VerificationResult.PASS


def test_matching_versions_pass(verifier):
    """Both expected and matched are the live version of the same song.
    Versions agree — must PASS."""
    _stub_lookup(verifier, recordings=[
        {"title": "Hello (Live at Wembley)", "artist": "Adele"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "Hello (Live at Wembley)",
        "Adele",
    )
    assert result == VerificationResult.PASS


# ---------------------------------------------------------------------------
# Secondary scan path — version gate must apply to fallback recordings too.
# ---------------------------------------------------------------------------


def test_instrumental_annotation_accepted_with_distractor_recording(verifier):
    """Same fork contract (83a7fe56) with a distractor in the list: the
    best-scoring recording is the requested song carrying an
    "(Instrumental)" annotation (fp 0.95, agreeing title/artist), and a
    different track is also present.  The annotation is treated as an
    MB/AcoustID metadata error — the track PASSES rather than being
    quarantined for the version tag."""
    _stub_lookup(verifier, recordings=[
        {"title": "Some Other Song", "artist": "Drake"},
        {"title": "In My Feelings (Instrumental)", "artist": "Drake"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "In My Feelings",
        "Drake",
    )
    assert result == VerificationResult.PASS
