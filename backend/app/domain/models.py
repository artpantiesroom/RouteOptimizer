from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Coordinate:
    latitude: float
    longitude: float


@dataclass
class Address:
    original: str


@dataclass
class Stop:
    original_address: str
    coordinate: Optional[Coordinate] = None
