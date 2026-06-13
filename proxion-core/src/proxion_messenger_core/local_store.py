"""
SQLite-backed persistence for local (pod-free) relay mode.

Stores rooms, room membership, messages, display names, and DM threads so
that gateway restarts and browser reloads can fully restore session state.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from ._store._base import _StoreBase
from ._store.messages import MessageStoreMixin
from ._store.rooms import RoomStoreMixin
from ._store.dms import DmStoreMixin
from ._store.federation import FederationStoreMixin
from ._store.devices import DeviceStoreMixin
from ._store.identity import IdentityStoreMixin
from ._store.security import SecurityStoreMixin


class LocalStore(
    MessageStoreMixin,
    RoomStoreMixin,
    DmStoreMixin,
    FederationStoreMixin,
    DeviceStoreMixin,
    IdentityStoreMixin,
    SecurityStoreMixin,
    _StoreBase,
):
    """SQLite-backed local persistence. Composed from per-domain mixins;
    schema, migrations, and connection management live in _StoreBase."""
    pass
