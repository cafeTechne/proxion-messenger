from proxion_messenger_core.solid import SolidResolver, SolidResolverError
import pytest


def test_resolve_back_round_trips():
    r = SolidResolver("https://alice.solidcommunity.net/")
    http = r.resolve("stash://alice/shared/photos/img.jpg")
    back = r.resolve_back(http, owner="alice")
    assert back == "stash://alice/shared/photos/img.jpg"


def test_resolve_back_default_owner():
    r = SolidResolver("https://pod.example.com/")
    back = r.resolve_back("https://pod.example.com/data/file.txt")
    assert back == "stash://pod/data/file.txt"


def test_resolve_back_rejects_foreign_url():
    r = SolidResolver("https://alice.example.com/")
    with pytest.raises(SolidResolverError):
        r.resolve_back("https://bob.example.com/data/file.txt")


def test_resolve_back_container():
    r = SolidResolver("http://localhost:3000/")
    back = r.resolve_back(
        "http://localhost:3000/messages/thread/abc/",
        owner="messages",
    )
    assert back == "stash://messages/messages/thread/abc/"
