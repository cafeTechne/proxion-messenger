"""UPnP automatic port mapping for gateway reachability.

Best-effort: returns None silently on any failure. miniupnpc is optional.
"""
from __future__ import annotations
from typing import Optional


def try_upnp_map(internal_port: int, external_port: Optional[int] = None,
                 protocol: str = "TCP") -> Optional[str]:
    """Attempt to create a UPnP port mapping.

    Returns the external http://ip:port URL if successful, None otherwise.
    """
    try:
        import miniupnpc  # type: ignore[import]
    except ImportError:
        return None
    try:
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        ndevices = upnp.discover()
        if ndevices == 0:
            return None
        upnp.selectigd()
        ext_port = external_port or internal_port
        try:
            upnp.deleteportmapping(ext_port, protocol)
        except Exception:
            pass
        result = upnp.addportmapping(
            ext_port, protocol,
            upnp.lanaddr, internal_port,
            "Proxion Gateway", "",
        )
        if not result:
            return None
        external_ip = upnp.externalipaddress()
        if external_ip:
            return f"http://{external_ip}:{ext_port}"
        return None
    except Exception:
        return None


def remove_upnp_map(external_port: int, protocol: str = "TCP") -> None:
    """Best-effort removal of UPnP mapping on shutdown."""
    try:
        import miniupnpc  # type: ignore[import]
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        if upnp.discover() > 0:
            upnp.selectigd()
            upnp.deleteportmapping(external_port, protocol)
    except Exception:
        pass
