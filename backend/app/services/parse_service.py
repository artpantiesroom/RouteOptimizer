from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class ParsedAddress:
    index: int
    original: str
    trimmed: str
    is_blank: bool
    is_duplicate: bool = False
    duplicate_of: int | None = None


@dataclass
class ParsedList:
    items: List[ParsedAddress]
    total: int
    non_blank: int


class ParseService:
    _WS = re.compile(r"\s+")
    _APOS = re.compile(r"['ʼ’]")

    def _normalize_key(self, s: str) -> str:
        # collapse whitespace, normalize apostrophes, casefold
        t = self._APOS.sub("'", s)
        t = self._WS.sub(" ", t).strip()
        return t.casefold()

    def parse(self, text: str, max_lines: int = 100, max_line_length: int = 500) -> ParsedList:
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines]

        items: List[ParsedAddress] = []
        for i, line in enumerate(lines):
            original = line
            # enforce max line length
            if len(original) > max_line_length:
                original_clamped = original[:max_line_length]
            else:
                original_clamped = original
            trimmed = original_clamped.strip()
            is_blank = trimmed == ""
            items.append(
                ParsedAddress(
                    index=i,
                    original=original_clamped,
                    trimmed=trimmed,
                    is_blank=is_blank,
                )
            )

        # detect duplicates among non-blank: map normalized key -> first occurrence index
        key_to_first_index: dict[str, int] = {}
        for item in items:
            if item.is_blank:
                continue
            key = self._normalize_key(item.trimmed)
            if key not in key_to_first_index:
                key_to_first_index[key] = item.index

        for item in items:
            if item.is_blank:
                continue
            key = self._normalize_key(item.trimmed)
            first = key_to_first_index.get(key)
            if first is not None and first != item.index:
                item.is_duplicate = True
                item.duplicate_of = first
            else:
                item.is_duplicate = False
                item.duplicate_of = None

        non_blank = sum(1 for i in items if not i.is_blank)
        return ParsedList(items=items, total=len(items), non_blank=non_blank)
