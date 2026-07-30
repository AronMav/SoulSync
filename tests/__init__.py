"""Marks the repository test suite as a package.

Without this file ``tests`` is a namespace package, and any regular ``tests``
package that happens to sit in site-packages wins the import. yandex_music
3.0.0 ships exactly that — its own test suite as a top-level ``tests`` package —
so a full ``pytest tests`` run collapsed at collection with

    ImportError: cannot import name 'requires_symlinks' from 'tests.conftest'

resolving to the dependency's conftest instead of ours. Several modules import
from ``tests.conftest``, so this is not optional once yandex_music is installed.
"""
