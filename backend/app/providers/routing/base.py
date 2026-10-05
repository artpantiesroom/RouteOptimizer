from __future__ import annotations

from abc import ABC, abstractmethod


class Router(ABC):
    @abstractmethod
    async def compute_matrix(self, *args, **kwargs):
        raise NotImplementedError
