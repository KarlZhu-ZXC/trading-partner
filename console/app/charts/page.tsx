"use client";

import { useEffect, useRef, useState } from "react";
import { ConsoleShell } from "../components/console-shell";
import { InteractiveTechnicalChart, type InteractiveTechnicalScene } from "../components/interactive-technical-chart";
import { ActionButton, Button, Card, ErrorNote, FieldLabel, Input, LinkButton, Select, Textarea } from "../components/ui";
import { postApi, useApi } from "../lib/api";
import { chartSceneMatches, stageChartResearchContext, type ChartStructure } from "../lib/chart-research-context";
import styles from "../styles/technical-chart.module.css";

export default function ChartsPage() {
  const [instrument, setInstrument] = useState("");
  const [subject, setSubject] = useState("");
  const [interval, setInterval] = useState<"1d" | "1w">("1d");
  const [scene, setScene] = useState<InteractiveTechnicalScene | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<{ structure: ChartStructure; cutoff: string | null } | null>(null);
  const [note, setNote] = useState("");
  const [target, setTarget] = useState<"thesis" | "plan">("thesis");
  const research = useApi<{ subjects: Array<{ subject: { subject_id: string; primary_instrument_id: string }; state: { ok: boolean; data?: { current_trade_plan?: { plan_id: string; version: number; instrument_id: string; subject_id: string; status: string; currency: string; reference_price: string; reference_price_at: string; stop_price: string | null; valid_from: string; valid_until: string | null; conditions: Array<{ condition_code: string; phase: string; mode: string; description: string; threshold: string | null; unit: string | null; comparator: string | null; instrument_id: string | null }> } } } }> }>("/api/research", { enabled: Boolean(subject) });
  const subjectState = research.data?.subjects.find((item) => item.subject.subject_id === subject && item.subject.primary_instrument_id === instrument.trim());
  const plan = subjectState?.state.ok ? subjectState.state.data?.current_trade_plan : null;
  const matchingPlan = plan?.status.toUpperCase() === "ACTIVE" && plan.instrument_id === instrument.trim() && plan.subject_id === subject ? plan : null;
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    setInstrument(query.get("instrument_id") ?? "");
    setSubject(query.get("subject_id") ?? "");
    return () => { generation.current += 1; controller.current?.abort(); };
  }, []);
  function reset() {
    generation.current += 1; controller.current?.abort(); setBusy(false); setScene(null); setSelection(null); setNote(""); setError(null);
  }
  async function openChart() {
    const identity = instrument.trim();
    if (!identity) { setError("Choose an Instrument first."); return; }
    reset(); const requestId = generation.current;
    const abort = new AbortController(); controller.current = abort; setBusy(true);
    try {
      const response = await postApi<{ result?: { ok?: boolean; data?: InteractiveTechnicalScene } }>("/api/tools/invoke", {
        tool_name: "technical_get_snapshot", preserve_full_result: true,
        arguments: { instrument_id: identity, intervals: [interval], lookback_sessions: interval === "1w" ? 1000 : 500, include_bars: true },
      }, { signal: abort.signal });
      if (requestId !== generation.current) return;
      const data = response.result?.data;
      if (!response.result?.ok || !data || !Array.isArray(data.bars) || !Array.isArray(data.timeframes) || !chartSceneMatches(data, identity, interval) || !data.price_basis) throw new Error("No matching chart snapshot is available. Retry Open Chart.");
      setScene(data);
    } catch (cause) { if (requestId === generation.current) setError(cause instanceof Error ? cause.message : "Chart unavailable"); }
    finally { if (requestId === generation.current) setBusy(false); }
  }
  function stage() {
    if (!scene || !selection || !subject || !scene.as_of || !scene.algorithm_version) return;
    const smcVersion = scene.timeframes.find((frame) => frame.interval === scene.bars_interval)?.smart_money?.algorithm_version;
    if (!smcVersion) { setError("Missing SMC algorithm provenance."); return; }
    try {
      const url = stageChartResearchContext(window.sessionStorage, {
        version: 1, id: crypto.randomUUID(), created_at: new Date().toISOString(), instrument_id: scene.instrument_id,
        subject_id: subject, snapshot_as_of: scene.as_of, algorithm_version: scene.algorithm_version,
        smc_algorithm_version: smcVersion, interval: scene.bars_interval, price_basis: scene.price_basis,
        target, ...selection, note,
      });
      window.location.assign(url);
    } catch { setError("Unable to stage this session context. Check browser storage availability."); }
  }
  return <ConsoleShell active="charts"><Card kicker="SOURCED TECHNICALS" title="Chart Workspace">
    <p>Open Chart explicitly retrieves provider bars. Adjustment basis is determined by the provider and disclosed in the snapshot. SMC is derived evidence; a reviewed Thesis and formal Trade Plan remain separate.</p>
    <div className={styles.toolbar}>
      <label><FieldLabel required>Instrument ID</FieldLabel><Input required value={instrument} onChange={(event) => { reset(); setInstrument(event.target.value); }} /></label>
      <label><FieldLabel required>Chart Period</FieldLabel><Select required value={interval} onChange={(event) => { reset(); setInterval(event.target.value as "1d" | "1w"); }}><option value="1d">Daily</option><option value="1w">Weekly</option></Select></label>
      <ActionButton busy={busy} onClick={() => { void openChart(); }}>Open Chart</ActionButton>
    </div>
    <ErrorNote>{error}</ErrorNote>
    {scene && <InteractiveTechnicalChart key={`${scene.instrument_id}:${scene.bars_interval}:${scene.price_basis}:${scene.algorithm_version}:${scene.as_of}`} scene={scene} onContextReset={() => { setSelection(null); setNote(""); }} onSelectStructure={(structure, cutoff) => { setSelection({ structure, cutoff }); setNote(""); }} />}
    {subject && <Card kicker="REVIEWED INTENT" title="Formal Trade Plan">
      <p>Separate from derived SMC. Plan prices have no comparable adjustment basis in this contract, so they are not drawn on the chart.</p>
      {research.loading ? <p>Loading durable Plan…</p> : research.error ? <ErrorNote>{research.error}</ErrorNote> : !subjectState ? <p>This Subject does not match the selected Instrument.</p> : !subjectState.state.ok ? <ErrorNote>Research state is unavailable.</ErrorNote> : matchingPlan ? <div>
        <p>{matchingPlan.plan_id} · v{matchingPlan.version} · ACTIVE</p>
        <p>Reference {matchingPlan.reference_price} {matchingPlan.currency} at {matchingPlan.reference_price_at} · Stop {matchingPlan.stop_price ?? "not set"}</p>
        <p>Valid {matchingPlan.valid_from} – {matchingPlan.valid_until ?? "no end recorded"}</p>
        {matchingPlan.conditions.map((condition) => <p key={condition.condition_code}>{condition.phase} · {condition.mode} · {condition.description}{condition.threshold !== null ? ` · ${condition.comparator ?? ""} ${condition.threshold} ${condition.unit ?? ""}` : ""}{condition.instrument_id ? ` · ${condition.instrument_id}` : ""}</p>)}
      </div> : <p>No matching ACTIVE Plan is recorded.</p>}
      <LinkButton href={`/research?subject_id=${encodeURIComponent(subject)}`}>Review Plan in Research</LinkButton>
    </Card>}
    {selection && <Card kicker="USER REVIEW" title="Research Context"><p>{selection.structure.kind} · {selection.structure.values}. This stages a draft only; it does not confirm a Thesis or create a Plan.</p>
      {!subject && <p>Open this chart from a Research Subject to bind the context to that subject.</p>}
      <label><span>Draft Destination</span><Select value={target} onChange={(event) => setTarget(event.target.value as "thesis" | "plan")}><option value="thesis">Thesis</option><option value="plan">Trade Plan</option></Select></label>
      <label><span>Your Interpretation</span><Textarea maxLength={8000} value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <Button disabled={!subject || !scene?.as_of || !scene?.algorithm_version} onClick={stage}>Continue in Research</Button>
      <Button onClick={() => setSelection(null)}>Discard Context</Button>
    </Card>}
  </Card></ConsoleShell>;
}
