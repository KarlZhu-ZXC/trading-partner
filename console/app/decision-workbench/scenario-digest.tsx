"use client";

import { Disclosure } from "../components/ui";

const SCENARIOS = ["UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION"] as const;

function truncate(value: string, maximum = 260): string {
  return value.length <= maximum ? value : `${value.slice(0, maximum).trim()}…`;
}

function parseScenarios(value: string): {
  preamble: string;
  scenarios: Array<{ name: string; detail: string }>;
} {
  const expression = /(UPSIDE|SIDEWAYS|PULLBACK|INVALIDATION)[：:]/g;
  const matches = Array.from(value.matchAll(expression));
  if (matches.length < 4) return { preamble: value, scenarios: [] };
  const preamble = value.slice(0, matches[0].index).trim();
  const scenarios = matches.map((match, index) => {
    const start = (match.index ?? 0) + match[0].length;
    const end = matches[index + 1]?.index ?? value.length;
    return { name: match[1], detail: value.slice(start, end).trim() };
  }).filter((item) => SCENARIOS.includes(item.name as (typeof SCENARIOS)[number]));
  return { preamble, scenarios };
}

export function ScenarioDigest({ value }: { value: string }) {
  const parsed = parseScenarios(value);
  if (parsed.scenarios.length !== 4) return <p>{value}</p>;
  return <div className="scenario-digest">
    {parsed.preamble ? <p>{truncate(parsed.preamble, 320)}</p> : null}
    <div className="scenario-digest-grid">
      {parsed.scenarios.map((scenario) => <section key={scenario.name}><strong>{scenario.name}</strong><span>{truncate(scenario.detail)}</span></section>)}
    </div>
    <Disclosure title="View Full Thesis" variant="panel"><p>{value}</p></Disclosure>
  </div>;
}

export { parseScenarios };
