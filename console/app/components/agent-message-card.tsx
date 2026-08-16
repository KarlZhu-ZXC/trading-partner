"use client";

import {
  Check,
  Copy,
  ExternalLink,
  PencilLine,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "../lib/api";
import type { AgentMessage, AgentReceipt } from "../lib/agent-api";
import { AgentMessageContent } from "./agent-message-content";

type Dict = Record<string, unknown>;

type AgentMessageCardProps = {
  message: AgentMessage;
  receipts: AgentReceipt[];
  copied: boolean;
  disabled: boolean;
  onCopy: (message: AgentMessage) => void;
  onEdit: (message: AgentMessage) => void;
  onRetry: (message: AgentMessage) => void;
};

function asRecord(value: unknown): Dict {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Dict)
    : {};
}

function displayDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function safeWebLinks(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.filter((item): item is string => {
    if (typeof item !== "string" || item.length > 2_048) return false;
    try {
      const parsed = new URL(item);
      return (parsed.protocol === "https:" || parsed.protocol === "http:")
        && Boolean(parsed.hostname)
        && !parsed.username
        && !parsed.password;
    } catch {
      return false;
    }
  }))).slice(0, 20);
}

function safeArtifactLinks(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.filter((item): item is string =>
    typeof item === "string"
      && item.length <= 512
      && /^\/api\/agent\/artifacts\/[A-Za-z0-9][A-Za-z0-9._-]*\.png$/.test(item),
  ))).slice(0, 20);
}

function sourceHostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "source";
  }
}

function AgentArtifactPreview({ url }: { url: string }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let currentObjectUrl: string | null = null;
    setObjectUrl(null);
    setFailed(false);
    void authenticatedFetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        if (!blob.type.startsWith("image/png")) throw new Error("Unexpected artifact type");
        currentObjectUrl = URL.createObjectURL(blob);
        setObjectUrl(currentObjectUrl);
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setFailed(true);
      });
    return () => {
      controller.abort();
      if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    };
  }, [url]);

  return (
    <a
      aria-label="Open Generated Chart in a New Tab"
      className={`agent-message-artifact${failed ? " failed" : ""}`}
      href={objectUrl ?? undefined}
      rel="noopener noreferrer"
      target="_blank"
    >
      {objectUrl ? (
        <span
          aria-label="Generated Chart"
          className="agent-message-artifact-image"
          role="img"
          style={{ backgroundImage: `url(${objectUrl})` }}
        />
      ) : (
        <span>{failed ? "Chart preview unavailable" : "Loading chart preview…"}</span>
      )}
    </a>
  );
}

export function AgentArtifactGallery({ urls }: { urls: string[] }) {
  const safeUrls = safeArtifactLinks(urls);
  if (safeUrls.length === 0) return null;
  return (
    <div className="agent-message-artifacts" aria-label="Generated Charts">
      {safeUrls.map((url) => <AgentArtifactPreview key={url} url={url} />)}
    </div>
  );
}

export function AgentReceiptCard({ receipt }: { receipt: AgentReceipt }) {
  return (
    <article className="agent-rail-receipt">
      <header><strong>{receipt.capability}</strong><time>{displayDate(receipt.created_at)}</time></header>
      <span>{receipt.operation}</span>
      {!!receipt.source_codes.length && <small>Source · {receipt.source_codes.join(" · ")}</small>}
      {!!receipt.warning_codes.length && <small className="warn">Warning · {receipt.warning_codes.join(" · ")}</small>}
      {!!receipt.error_codes.length && <small className="bad">Error · {receipt.error_codes.join(" · ")}</small>}
    </article>
  );
}

export function AgentMessageCard({
  message,
  receipts,
  copied,
  disabled,
  onCopy,
  onEdit,
  onRetry,
}: AgentMessageCardProps) {
  const isUser = message.role === "USER";
  const modelReceipt = asRecord(message.model_receipt);
  const sourceUrls = useMemo(
    () => safeWebLinks(modelReceipt.web_source_urls),
    [modelReceipt.web_source_urls],
  );
  const artifactUrls = useMemo(
    () => safeArtifactLinks(modelReceipt.artifact_urls),
    [modelReceipt.artifact_urls],
  );

  return (
    <article className={`agent-rail-message ${isUser ? "user" : "assistant"}`}>
      <header>
        <span>{isUser ? "You" : "Agent"}</span>
        <div className="agent-message-meta">
          <time>{displayDate(message.created_at)}</time>
          <div className="agent-message-actions" aria-label={`${isUser ? "User" : "Agent"} message actions`}>
            <button
              aria-label={copied ? "Message Copied" : "Copy Message"}
              className={copied ? "success" : ""}
              onClick={() => onCopy(message)}
              title={copied ? "Copied" : "Copy"}
              type="button"
            >
              {copied ? <Check aria-hidden="true" size={11} /> : <Copy aria-hidden="true" size={11} />}
            </button>
            {isUser ? (
              <button
                aria-label="Edit This Prompt and Resend"
                disabled={disabled}
                onClick={() => onEdit(message)}
                title="Edit and Resend as a New Turn"
                type="button"
              >
                <PencilLine aria-hidden="true" size={11} />
              </button>
            ) : (
              <button
                aria-label="Retry the Prompt for This Response"
                disabled={disabled}
                onClick={() => onRetry(message)}
                title="Retry as a New Turn"
                type="button"
              >
                <RefreshCw aria-hidden="true" size={11} />
              </button>
            )}
          </div>
        </div>
      </header>
      <AgentMessageContent content={message.content} />
      <AgentArtifactGallery urls={artifactUrls} />
      {!!sourceUrls.length && (
        <div className="agent-rail-source-block" aria-label="Web Sources">
          <span>Web Context · {sourceUrls.length}</span>
          {sourceUrls.map((url, index) => (
            <a href={url} key={url} rel="noopener noreferrer" target="_blank">
              <ExternalLink aria-hidden="true" size={10} /> {index + 1}. {sourceHostname(url)}
            </a>
          ))}
        </div>
      )}
      {!!receipts.length && (
        <details className="agent-message-evidence">
          <summary>Evidence &amp; Tools · {receipts.length}</summary>
          <div>{receipts.map((receipt) => <AgentReceiptCard key={receipt.receipt_id} receipt={receipt} />)}</div>
        </details>
      )}
    </article>
  );
}
