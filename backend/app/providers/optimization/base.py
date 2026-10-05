from __future__ import annotations

from abc import ABC, abstractmethod


class Optimizer(ABC):
    @abstractmethod
    async def optimize(self, *args, **kwargs):
        raise NotImplementedError
