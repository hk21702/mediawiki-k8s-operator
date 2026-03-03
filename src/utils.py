# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utility functions for the MediaWiki charm."""


def escape_php_string(s: str) -> str:
    """Escape PHP special characters in string."""
    return s.replace("\\", "\\\\").replace("'", "\\'")
