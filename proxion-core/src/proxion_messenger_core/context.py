"""Request context and caveat definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional


@dataclass(frozen=True)
class RequestContext:
    action: str
    resource: str
    aud: str
    now: datetime
    ip: Optional[str] = None
    device_nonce: Optional[str] = None
    method: Optional[str] = None


CaveatPredicate = Callable[[RequestContext], bool]


@dataclass(frozen=True)
class Caveat:
    id: str
    predicate: CaveatPredicate

    def evaluate(self, ctx: RequestContext) -> bool:
        return self.predicate(ctx)
