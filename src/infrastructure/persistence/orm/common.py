"""Shared SQLAlchemy column types for persistence models."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Text, TypeDecorator
from sqlalchemy.engine import Dialect

HEX64_CHECK = (
    "length({col}) = 64 AND {col} = lower({col}) "
    "AND {col} NOT GLOB '*[^0-9a-f]*'"
)

EVIDENCE_TYPE_IN = (
    "'market_snapshot','fundamental_snapshot','financial_statement',"
    "'company_action','company_news','global_news','research_report',"
    "'technical_signal','sentiment','macro','account_snapshot',"
    "'portfolio_snapshot','user_observation',"
    "'a_share_announcement','a_share_interactive_qa','a_share_analyst_report',"
    "'a_share_consensus_estimate','a_share_capital_flow',"
    "'a_share_northbound_flow','a_share_chip_distribution',"
    "'a_share_dragon_tiger','a_share_margin_financing','a_share_block_trade',"
    "'a_share_shareholder_count','a_share_unlock','a_share_dividend',"
    "'a_share_order_book','a_share_tick','a_share_limit_ecology',"
    "'a_share_market_heat','a_share_concept_heat','a_share_option_snapshot',"
    "'sec_filing','sec_company_fact','us_insider_activity','us_10b5_1',"
    "'us_pre_post_market','us_news_sentiment','fred_macro',"
    "'stocktwits_sentiment','reddit_sentiment','prediction_market',"
    "'correction'"
)


class JsonStringTuple(TypeDecorator[tuple[str, ...]]):
    """Store ``tuple[str, ...]`` as a compact JSON array."""

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: tuple[str, ...] | list[str] | None, dialect: Dialect
    ) -> str:
        if value is None:
            return "[]"
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))

    def process_result_value(self, value: str | None, dialect: Dialect) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
        parsed: Any = json.loads(value)
        if not isinstance(parsed, list):
            msg = "JSON string tuple column must decode to a list"
            raise TypeError(msg)
        return tuple(str(item) for item in parsed)
