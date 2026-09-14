"use client";

import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, DescriptionList, Disclosure, Empty, ErrorNote, FormField, Input, Select, Table, Textarea, formatDate } from "../components/ui";
import { getJson, sendJsonMethod } from "../lib/api";

type Assumptions = { normalization_factor: string; normalization_step: string; pe_multiple: string; pe_step: string; business_model: string; rationale: string };
type Source = { instrument_id: string; source: string; eps_diluted: string; currency: string; unit: string; share_basis: string; period_start: string; period_end: string; filed_at: string; accession: string; filing_form: string; as_of: string; shares_outstanding: string | null; shares_period_end: string | null; warning_codes: string[] };
type SourceReceipt = { source_token: string; source: Source; expires_at: string };
type Snapshot = { method: string; normalized_eps: string; per_share_value: string; sensitivity: { normalization_factor: string; pe_multiple: string; per_share_value: string }[]; value_basis: string; enterprise_value: null; aggregate_equity_value: null; source: Source; assumptions: Assumptions; warnings: string[] };
type SavedVersion = { version_id: string; created_at: string; supersedes_version_id: string | null; snapshot: Snapshot };
type History = { items: SavedVersion[]; version_identity: unknown };
const emptyAssumptions = (): Assumptions => ({ normalization_factor: "", normalization_step: "", pe_multiple: "", pe_step: "", business_model: "", rationale: "" });
const numericFields = [
  ["normalization_factor", "EPS Normalization Factor"], ["normalization_step", "Normalization Sensitivity Step"],
  ["pe_multiple", "P/E Multiple"], ["pe_step", "P/E Sensitivity Step"],
] as const;

function unpack<T>(response: unknown): T {
  const value = (response as { data?: T } | null)?.data;
  if (value == null) throw new Error("The response did not contain valuation data.");
  return value;
}

function SourceFacts({ source }: { source: Source }) {
  return <DescriptionList columns={2} items={[
    { label: "Annual Diluted EPS", value: `${source.eps_diluted} ${source.currency}`, detail: `${source.unit} · ${source.share_basis}` },
    { label: "Reporting Period", value: `${source.period_start} → ${source.period_end}` },
    { label: "Filing", value: `${source.filing_form} · ${source.filed_at}`, detail: source.accession },
    { label: "Source Timestamp", value: source.source, detail: formatDate(source.as_of) },
    { label: "Reported Shares", value: source.shares_outstanding ?? "Unavailable", detail: source.shares_period_end ?? "No share-count date" },
    { label: "Source Limitations", value: source.warning_codes.join(" · ") || "No additional source warnings" },
  ]} />;
}

function ScenarioResult({ snapshot }: { snapshot: Snapshot }) {
  return <>
    <DescriptionList columns={2} items={[
      { label: "Method", value: snapshot.method },
      { label: "Normalized EPS", value: `${snapshot.normalized_eps} ${snapshot.source.currency}` },
      { label: "Scenario Value per Share", value: `${snapshot.per_share_value} ${snapshot.source.currency}`, detail: snapshot.value_basis },
      { label: "Enterprise / Aggregate Equity Value", value: "Not calculated" },
    ]} />
    <p>Annual diluted EPS × your normalization factor × your P/E. As-reported share basis; no current-share adjustment or investment recommendation.</p>
    <Table><thead><tr><th scope="col">Normalization</th><th scope="col">P/E</th><th scope="col">Value / Share ({snapshot.source.currency})</th></tr></thead>
      <tbody>{snapshot.sensitivity.map((row, index) => <tr key={index}><td>{row.normalization_factor}</td><td>{row.pe_multiple}</td><td>{row.per_share_value}</td></tr>)}</tbody></Table>
    {snapshot.warnings.length > 0 && <p>{snapshot.warnings.join(" · ")}</p>}
  </>;
}

export function ValuationLedger({ subjectId, instrumentId }: { subjectId: string; instrumentId: string | null }) {
  if (!instrumentId?.startsWith("equity:US:")) return <Card kicker="ASSUMPTION LEDGER" title="Valuation Scenarios"><Empty>Valuation scenarios currently support US stock Research Subjects only.</Empty></Card>;
  return <ValuationWorkspace key={`${subjectId}:${instrumentId}`} subjectId={subjectId} instrumentId={instrumentId} />;
}

function ValuationWorkspace({ subjectId, instrumentId }: { subjectId: string; instrumentId: string }) {
  const [history, setHistory] = useState<History | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyNonce, setHistoryNonce] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [source, setSource] = useState<SourceReceipt | null>(null);
  const [assumptions, setAssumptions] = useState<Assumptions>(emptyAssumptions);
  const [preview, setPreview] = useState<Snapshot | null>(null);
  const [prior, setPrior] = useState<SavedVersion | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [authorization, setAuthorization] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const active = useRef(true);
  const mutation = useRef<AbortController | null>(null);
  const attempt = useRef<{ payload: string; key: string } | null>(null);
  const base = `/api/research/${encodeURIComponent(subjectId)}/valuation`;

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; mutation.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setHistoryLoading(true); setHistoryError(null);
    void getJson(base, controller.signal).then((response) => {
      if (controller.signal.aborted || !active.current) return;
      const next = unpack<History>(response);
      if (!Array.isArray(next.items)) throw new Error("Invalid valuation history.");
      setHistory(next);
    }).catch(() => { if (!controller.signal.aborted && active.current) setHistoryError("Valuation history could not be read. Retry to restore coverage."); })
      .finally(() => { if (!controller.signal.aborted && active.current) setHistoryLoading(false); });
    return () => controller.abort();
  }, [base, historyNonce]);

  function clearApproval() { setPreview(null); setConfirmed(false); setAuthorization(""); attempt.current = null; setError(null); setMessage(null); }
  function edit(name: keyof Assumptions, value: string) { setAssumptions((current) => ({ ...current, [name]: value })); clearApproval(); }
  async function perform<T>(kind: string, path: string, body: unknown, apply: (value: T) => void) {
    if (mutation.current) return;
    const controller = new AbortController(); mutation.current = controller;
    setBusy(kind); setError(null); setMessage(null);
    try {
      const response = await sendJsonMethod(`${base}${path}`, "POST", body, controller.signal);
      if (active.current && !controller.signal.aborted) apply(unpack<T>(response));
    } catch (cause) {
      if (active.current && !controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Valuation operation failed.");
    } finally {
      if (mutation.current === controller) mutation.current = null;
      if (active.current && !controller.signal.aborted) setBusy(null);
    }
  }
  function refreshSource() {
    clearApproval(); setSource(null);
    void perform<SourceReceipt>("source", "/source", {}, (value) => {
      if (value.source.instrument_id !== instrumentId) throw new Error("Source does not match this Instrument.");
      setSource(value);
    });
  }
  const complete = Object.values(assumptions).every((value) => value.trim().length > 0);
  const supported = assumptions.business_model === "operating_company";
  function calculate() {
    if (!source || !complete || !supported) return;
    clearApproval();
    void perform<Snapshot>("calculate", "/calculate", { source_token: source.source_token, assumptions }, (value) => {
      if (value.source.instrument_id !== instrumentId) throw new Error("Calculation does not match this Instrument.");
      setPreview(value);
    });
  }
  function save() {
    if (!preview || !source || !confirmed || !authorization.trim()) return;
    const body = { source_token: source.source_token, assumptions, supersedes_version_id: prior?.version_id ?? null, authorization_note: authorization.trim(), confirmed: true };
    const fingerprint = JSON.stringify(body);
    if (attempt.current?.payload !== fingerprint) attempt.current = { payload: fingerprint, key: crypto.randomUUID() };
    void perform<SavedVersion>("save", "/versions", { ...body, idempotency_key: attempt.current.key }, (value) => {
      setPrior(value); setConfirmed(false); setAuthorization(""); setPreview(null); attempt.current = null;
      setMessage("Saved an immutable valuation version. This does not confirm a judgment or authorize a trade.");
      setHistoryNonce((value) => value + 1);
    });
  }
  function restore(version: SavedVersion) {
    if (version.snapshot.source.instrument_id !== instrumentId) { setError("Saved source does not match this Instrument."); return; }
    setPrior(version); setAssumptions({ ...version.snapshot.assumptions }); setSource(null); clearApproval();
    setMessage("Restored user assumptions. Retrieve a source explicitly before calculating a new version; the saved record remains unchanged.");
  }

  return <Card kicker="ASSUMPTION LEDGER" title="Valuation Scenarios">
    <p>Source facts, USER assumptions and calculated scenarios remain separate. Saving a scenario does not confirm a Thesis or Decision.</p>
    <ErrorNote>{error}</ErrorNote>{message && <p role="status">{message}</p>}
    <Disclosure title="Source Facts" defaultOpen>
      <Button variant="secondary" disabled={busy !== null} onClick={refreshSource}>{busy === "source" ? "Retrieving SEC Source…" : "Retrieve SEC Annual EPS"}</Button>
      {source ? <><SourceFacts source={source.source} /><p>Source receipt expires {formatDate(source.expires_at)}.</p></> : <Empty>No source loaded. Retrieval occurs only when requested.</Empty>}
    </Disclosure>
    <Disclosure title="USER Assumptions" defaultOpen>
      <FormField label="Business Model" required><Select required value={assumptions.business_model} disabled={busy !== null} onChange={(event) => edit("business_model", event.target.value)}>
        <option value="">Choose business model</option><option value="operating_company">Operating Company</option><option value="bank">Bank</option><option value="insurance">Insurance</option><option value="reit">REIT</option><option value="other">Other</option>
      </Select></FormField>
      {assumptions.business_model && !supported && <p role="status">This earnings method does not support banks, insurers, REITs or other business models.</p>}
      {numericFields.map(([key, label]) => <FormField key={key} label={label} required><Input required inputMode="decimal" value={assumptions[key]} disabled={busy !== null} onChange={(event) => edit(key, event.target.value)} /></FormField>)}
      <FormField label="Assumption Rationale" required><Textarea required value={assumptions.rationale} disabled={busy !== null} onChange={(event) => edit("rationale", event.target.value)} /></FormField>
      <Button disabled={busy !== null || !source || !complete || !supported} onClick={calculate}>{busy === "calculate" ? "Calculating…" : "Calculate Scenarios"}</Button>
    </Disclosure>
    <Disclosure title="Computed Scenarios" defaultOpen>
      {preview ? <ScenarioResult snapshot={preview} /> : <Empty>Complete your assumptions and calculate to inspect the nine sensitivity scenarios.</Empty>}
    </Disclosure>
    <Disclosure title="Save a Version" defaultOpen>
      <p>{prior ? `New version will supersede exactly ${prior.version_id}.` : "No prior version selected. The saved version will start a new chain."}</p>
      <FormField label="Save Authorization Note" required><Textarea required value={authorization} disabled={busy !== null || !preview} onChange={(event) => { setAuthorization(event.target.value); setConfirmed(false); attempt.current = null; }} /></FormField>
      <FormField label="I reviewed these source facts, assumptions and scenarios and authorize saving this version." required><Input type="checkbox" required checked={confirmed} disabled={busy !== null || !preview} onChange={(event) => setConfirmed(event.target.checked)} /></FormField>
      <Button disabled={busy !== null || !preview || !confirmed || !authorization.trim()} onClick={save}>{busy === "save" ? "Saving Version…" : "Save Valuation Version"}</Button>
    </Disclosure>
    <Disclosure title="Saved History" defaultOpen>
      <Button variant="secondary" disabled={historyLoading || busy !== null} onClick={() => setHistoryNonce((value) => value + 1)}>Refresh History</Button>
      <ErrorNote>{historyError}</ErrorNote>{historyLoading && <p role="status">Reading saved versions…</p>}
      {history?.items.length === 0 && <Empty>No saved valuation versions.</Empty>}
      {history?.items.map((version) => <Disclosure key={version.version_id} title={formatDate(version.created_at)} description={version.version_id}>
        <Badge value={version.version_id === prior?.version_id ? "SELECTED" : "SAVED"} />
        <p>Supersedes: {version.supersedes_version_id ?? "None"}</p>
        <SourceFacts source={version.snapshot.source} />
        <DescriptionList columns={2} items={Object.entries(version.snapshot.assumptions).map(([label, value]) => ({ label: `USER · ${label}`, value }))} />
        <ScenarioResult snapshot={version.snapshot} />
        <Button variant="secondary" disabled={busy !== null} onClick={() => restore(version)}>Restore Assumptions From This Version</Button>
      </Disclosure>)}
    </Disclosure>
  </Card>;
}
