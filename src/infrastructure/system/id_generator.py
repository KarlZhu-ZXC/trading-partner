"""UUIDv7-backed entity ID generator (via uuid6)."""

from __future__ import annotations

from uuid6 import uuid7

from domain.common.ids import EntityIdPrefix, format_entity_id


class Uuid7IdGenerator:
    def new(self, prefix: EntityIdPrefix) -> str:
        if not isinstance(prefix, EntityIdPrefix):
            raise TypeError("prefix must be EntityIdPrefix; arbitrary strings are not allowed")
        token = str(uuid7()).lower()
        return format_entity_id(prefix, token)
