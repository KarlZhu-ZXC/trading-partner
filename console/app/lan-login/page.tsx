"use client";

import { FormEvent, useState } from "react";
import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";

export default function LanLoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/lan-auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const payload = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? `HTTP ${response.status}`);
      window.location.replace("/");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to sign in");
      setSubmitting(false);
    }
  }

  return (
    <main className="lan-login-shell">
      <section className="lan-login-card" aria-labelledby="lan-login-title">
        <div className="lan-login-brand">
          <img alt="" height="44" src="/assets/trading-partner-brand/logo.png" width="44" />
          <div>
            <strong>Trading Partner</strong>
            <span>TRUSTED LAN ACCESS</span>
          </div>
        </div>
        <div className="lan-login-lock" aria-hidden="true"><LockKeyhole /></div>
        <p className="eyebrow">LOCAL HUB</p>
        <h1 id="lan-login-title">Unlock This Console</h1>
        <p className="lan-login-copy">
          Enter the password configured on the Mac running Trading Partner.
          Your session stays in this browser for 12 hours.
        </p>
        <form className="lan-login-form" onSubmit={submit}>
          <label htmlFor="lan-password"><b className="required-mark" aria-hidden="true">*</b>LAN Password</label>
          <input
            autoComplete="current-password"
            autoFocus
            id="lan-password"
            minLength={16}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Enter LAN password"
            required
            type="password"
            value={password}
          />
          {error ? <p className="lan-login-error" role="alert">{error}</p> : null}
          <button disabled={submitting || password.length < 1} type="submit">
            {submitting ? "Unlocking…" : "Unlock Console"}
            <ArrowRight aria-hidden="true" />
          </button>
        </form>
        <div className="lan-login-note">
          <ShieldCheck aria-hidden="true" />
          <span>The data API remains bound to 127.0.0.1 on the host Mac.</span>
        </div>
      </section>
    </main>
  );
}
