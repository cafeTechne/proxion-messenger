"""Write a real ACP room grant using throwaway CSS credentials on stdin.

Called only by web/smoke_pod_access.mjs; secrets never appear in argv/output.
"""
import json
import sys
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.acp import detect_acl_mode, set_acl_multi_auto
from proxion_messenger_core.css_auth import CssClientCredentials, DpopSolidClient
from proxion_messenger_core.room import set_room_acl
from proxion_messenger_core.solid import SolidResolver


def main():
    config = json.load(sys.stdin)
    account = config["account"]
    credentials = CssClientCredentials(config["css"], account["clientId"], account["clientSecret"], Ed25519PrivateKey.generate())
    with DpopSolidClient(SolidResolver(account["storageRoot"]), credentials) as client:
        uri = "stash://pod/proxion/rooms/python-acp/"
        assert detect_acl_mode(client, uri, strict=True) == "acp"
        if config["encoding"] == "turtle":
            acr = set_acl_multi_auto(client, uri, account["webId"], config["members"], ["Read", "Append"], "ess")
        else:
            room = SimpleNamespace(stash_root=uri, read_only=config.get("readOnly", False))
            acr = set_room_acl(room, client, account["webId"], config["members"])
        print(json.dumps({"acr": acr}))


if __name__ == "__main__":
    main()
