"""Token attenuation/derivation logic."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Tuple

from .context import Caveat
from .errors import AttenuationError
from .tokens import Token, issue_token


def derive_token(
    parent: Token,
    narrower_perms: Iterable[Tuple[str, str]],
    extra_caveats: Iterable[Caveat],
    now: datetime,
    signing_key: bytes,
) -> Token:
    narrower = frozenset(narrower_perms)
    if not narrower:
        raise AttenuationError("derived permissions must be non-empty")
    if not narrower.issubset(parent.permissions):
        raise AttenuationError("permission widening is not allowed")
    if now >= parent.exp:
        raise AttenuationError("parent token expired")
    combined_caveats = tuple(parent.caveats) + tuple(extra_caveats)
    return issue_token(
        permissions=narrower,
        exp=parent.exp,
        aud=parent.aud,
        caveats=combined_caveats,
        holder_key_fingerprint=parent.holder_key_fingerprint,
        signing_key=signing_key,
        now=now,
        parent_token_id=parent.token_id,
    )
