"""Compatibility re-export for VerifiedMarketSnapshot contract validation.

Authoritative pure implementation lives in ``domain.market.validation``.
This module preserves the D6a import surface for codecs and existing callers;
it must not contain a second implementation body.
"""

from __future__ import annotations

from domain.market.validation import validate_verified_market_snapshot

__all__ = ["validate_verified_market_snapshot"]
