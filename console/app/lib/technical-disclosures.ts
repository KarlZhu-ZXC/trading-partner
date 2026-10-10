export type ChartDisclosure = { code: string; message: string };
export type TechnicalChartWarning = { code: string; message?: string };
export const DATE_LABEL_TIME_LIMITATION = "SMC_FUTURES_DATE_LABEL_CONFIRMATION_TIME_UNKNOWN";

const SMC_GUIDANCE: Record<string, string> = {
  [DATE_LABEL_TIME_LIMITATION]: "Futures bar and SMC occurrence/confirmation timestamps are source date labels, not verified availability times. Historical cutoff filtering is unavailable; current structures describe the available snapshot only.",
  SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL: "The first and last weeks may be partial. SMC confirmation uses the available bars; structures involving an unfinished week remain provisional.",
  SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE: "Some candles open at zero or a negative price. Percentage-based fair-value gaps are unavailable for those candles.",
  SMC_ATR_200_UNAVAILABLE_ORDER_BLOCK_FILTER_AND_EQH_EQL: "History is too short for the 200-bar volatility estimate. Order-block filtering and equal-high/low levels are limited.",
};

const WARNING_GUIDANCE: Record<string, string> = {
  FUTURES_DAILY_CONSERVATIVE_COMPLETION_BOUND: "Daily futures rows use a conservative 17:00 New York availability bound on the ending trade date. This is not a proven last-trade time or the actual holiday close.",
  FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN: "Daily futures rows retain native New York date labels and are withheld until the next local date. Session coverage and actual completion time are unknown. Derived SMC occurrence and confirmation timestamps are date labels, not verified availability times.",
  TECHNICAL_WEEKLY_BOUNDARIES_MAY_BE_PARTIAL: "Weekly bars use available daily observations. The first and last weeks may be incomplete.",
  CALENDAR_PERIOD_AGGREGATED_FROM_DAILY_BARS: "Calendar-period bars use available daily observations. The first and last periods may be incomplete.",
  TECHNICAL_HISTORY_INCOMPLETE: "Some indicators do not have enough history and remain unavailable.",
  TECHNICAL_DATA_NOT_FRESH: "Chart data is delayed, stale, or of unknown age. Check the source timestamp before using these levels.",
  US_NON_SESSION_DAILY_BAR: "Some daily rows fall outside exchange sessions. Their timestamps do not establish a regular trading close.",
  LATEST_ADJUSTED_BAR_UNAVAILABLE: "The newest adjusted daily bar is unavailable. This chart uses earlier available bars.",
  FUTURES_CONTRACT_NOT_SPOT: "This chart uses futures contract prices. Spot prices can differ.",
  CONTINUOUS_FUTURES_ROLL_RISK: "The continuous front-month contract can change at rolls, creating jumps in chart levels.",
  FALLBACK_SOURCE: "The chart uses the configured fallback data source.",
  OTC_BROKER_FEED: "Prices come from a broker OTC feed.",
  DUKASCOPY_SWFX_NOT_LBMA: "Gold and silver prices use broker OTC quotes. Auction benchmark prices can differ.",
  VOLUME_BEST_BID_ASK_NOT_EXCHANGE: "Volume uses broker quote-side activity, with a different basis from exchange traded volume.",
  ROLLING_CFD_NOT_SPOT: "This chart uses rolling CFD prices. Physical spot and specific futures prices can differ.",
};

export function smcDisclosures(limitations: readonly string[]): ChartDisclosure[] {
  return [...new Set(limitations.map((code) => code.trim()).filter(Boolean))].map((code) => ({
    code,
    message: SMC_GUIDANCE[code] ?? "An additional SMC limitation applies. Review the source detail before using these levels.",
  }));
}

export function chartWarningDisclosures(warnings: readonly TechnicalChartWarning[]): ChartDisclosure[] {
  const seen = new Set<string>();
  return warnings.flatMap((warning) => {
    const code = warning.code.trim();
    if (!code || seen.has(code)) return [];
    seen.add(code);
    const sourceMessage = warning.message?.trim();
    return [{
      code,
      message: WARNING_GUIDANCE[code] ?? (
        sourceMessage && sourceMessage !== "Provider supplied this warning code."
          ? sourceMessage
          : "A source limitation applies to this chart. Review the source detail before using these levels."
      ),
    }];
  });
}
