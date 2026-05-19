"""Tests for proxion_messenger_core.solid — Solid Protocol URI adapter."""

import pytest

from proxion_messenger_core import SolidResolver, SolidResolverError, permission_to_solid_url


# ---------------------------------------------------------------------------
# SolidResolver.resolve — basic functionality
# ---------------------------------------------------------------------------


def test_resolve_basic_path():
    """Resolve a simple stash:// URI to a Solid URL."""
    resolver = SolidResolver("https://alice.example/")
    url = resolver.resolve("stash://alice/shared/file.txt")
    assert url == "https://alice.example/shared/file.txt"


def test_resolve_strips_owner_segment():
    """The owner segment is stripped; only the path remains."""
    resolver = SolidResolver("https://alice.example/")
    url = resolver.resolve("stash://alice/data/resource")
    # URL should have path /data/resource, not /alice/data/resource
    assert url == "https://alice.example/data/resource"
    # alice in domain is expected, but owner segment should not appear in path
    assert "/alice/data" not in url


def test_resolve_preserves_trailing_slash():
    """Trailing slashes in the path are preserved."""
    resolver = SolidResolver("https://alice.example/")
    url = resolver.resolve("stash://alice/photos/")
    assert url.endswith("/")


def test_resolve_root_path():
    """stash://alice/ (just owner) resolves to the pod root."""
    resolver = SolidResolver("https://alice.example/")
    url = resolver.resolve("stash://alice/")
    assert url == "https://alice.example/"


def test_resolve_nested_path():
    """Nested paths are correctly resolved."""
    resolver = SolidResolver("https://alice.example/")
    url = resolver.resolve("stash://alice/shared/photos/2024/img.jpg")
    assert url == "https://alice.example/shared/photos/2024/img.jpg"


# ---------------------------------------------------------------------------
# SolidResolver.resolve — error cases
# ---------------------------------------------------------------------------


def test_resolve_non_stash_uri_raises():
    """Non-stash:// URIs raise SolidResolverError."""
    resolver = SolidResolver("https://alice.example/")
    with pytest.raises(SolidResolverError, match="not a stash:// URI"):
        resolver.resolve("https://alice.example/file.txt")


def test_resolve_http_uri_raises():
    """HTTP/HTTPS URIs raise SolidResolverError."""
    resolver = SolidResolver("https://alice.example/")
    with pytest.raises(SolidResolverError):
        resolver.resolve("http://example.com/resource")


def test_resolve_plain_path_raises():
    """Plain paths without stash:// raise SolidResolverError."""
    resolver = SolidResolver("https://alice.example/")
    with pytest.raises(SolidResolverError):
        resolver.resolve("/shared/file.txt")


# ---------------------------------------------------------------------------
# SolidResolver — pod_base_url property
# ---------------------------------------------------------------------------


def test_pod_base_url_auto_adds_slash():
    """pod_base_url ensures a trailing slash."""
    resolver = SolidResolver("https://alice.example")
    assert resolver.pod_base_url == "https://alice.example/"


def test_pod_base_url_preserves_existing_slash():
    """If already trailing with /, don't double-slash."""
    resolver = SolidResolver("https://alice.example/")
    assert resolver.pod_base_url == "https://alice.example/"


# ---------------------------------------------------------------------------
# SolidResolver.covers — prefix matching
# ---------------------------------------------------------------------------


def test_covers_exact_match():
    """covers() returns True for exact matches."""
    resolver = SolidResolver("https://alice.example/")
    assert resolver.covers("stash://a/x", "stash://a/x")


def test_covers_root_covers_all():
    """Root path / covers all resources."""
    resolver = SolidResolver("https://alice.example/")
    assert resolver.covers("/", "stash://a/x")
    assert resolver.covers("/", "stash://a/y/z")


def test_covers_prefix_with_trailing_slash():
    """Prefix with / covers resources under that path."""
    resolver = SolidResolver("https://alice.example/")
    assert resolver.covers("stash://a/photos/", "stash://a/photos/img.jpg")
    assert resolver.covers("stash://a/photos/", "stash://a/photos/2024/pic.jpg")


def test_covers_non_prefix_denied():
    """Non-prefix paths are not covered."""
    resolver = SolidResolver("https://alice.example/")
    assert not resolver.covers("stash://a/photos/", "stash://a/other/file")
    assert not resolver.covers("stash://a/photos/", "stash://a/photo/file")  # note: no trailing /


def test_covers_missing_trailing_slash_not_prefix():
    """Prefix without trailing slash is not a true prefix."""
    resolver = SolidResolver("https://alice.example/")
    # "stash://a/photo" does not cover "stash://a/photos/img"
    # even though "stash://a/photo" is a string prefix
    assert not resolver.covers("stash://a/photo", "stash://a/photos/img")


def test_covers_empty_permission_resource():
    """Empty string does not match."""
    resolver = SolidResolver("https://alice.example/")
    assert not resolver.covers("", "stash://a/x")


# ---------------------------------------------------------------------------
# permission_to_solid_url helper function
# ---------------------------------------------------------------------------


def test_permission_to_solid_url_stash_uri():
    """Helper resolves stash:// URIs."""
    resolver = SolidResolver("https://alice.example/")
    url = permission_to_solid_url("stash://alice/data/", resolver)
    assert url == "https://alice.example/data/"


def test_permission_to_solid_url_plain_path_leading_slash():
    """Helper handles plain paths with leading /."""
    resolver = SolidResolver("https://alice.example/")
    url = permission_to_solid_url("/data/file.txt", resolver)
    assert url == "https://alice.example/data/file.txt"


def test_permission_to_solid_url_plain_path_no_leading_slash():
    """Helper strips leading / from plain paths and appends to base."""
    resolver = SolidResolver("https://alice.example/")
    url = permission_to_solid_url("data/file.txt", resolver)
    assert url == "https://alice.example/data/file.txt"


def test_permission_to_solid_url_root_path():
    """Helper handles root path /."""
    resolver = SolidResolver("https://alice.example/")
    url = permission_to_solid_url("/", resolver)
    assert url == "https://alice.example/"


def test_permission_to_solid_url_different_pod_base():
    """Helper works with different pod base URLs."""
    resolver = SolidResolver("https://bob.solidcommunity.net/profile/bob/")
    url = permission_to_solid_url("/shared/resources/", resolver)
    assert url == "https://bob.solidcommunity.net/profile/bob/shared/resources/"
