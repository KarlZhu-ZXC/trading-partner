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
import type { AgentImageAttachment, AgentMessage, AgentReceipt } from "../lib/agent-api";
import { parseCopilotResearch } from "../lib/agent-api";
import { CopilotResearchProgress } from "./copilot-research-progress";
import { AgentMessageContent } from "./agent-message-content";
import { Disclosure } from "./ui";
import { IconButton, TextLink } from "./ui/controls";

type Dict = Record<string, unknown>;

type AgentMessageCardProps = {
  message: AgentMessage;
  receipts: AgentReceipt[];
  recoveredStatus?: string;
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
  return new Intl.DateTimeFormat("en-US", {
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
    <TextLink
      aria-label="Open Generated Chart in a New Tab"
      className={`agent-message-artifact${failed ? " failed" : ""}`}
      href={objectUrl ?? "#"}
      onClick={(event) => { if (!objectUrl) event.preventDefault(); }}
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
    </TextLink>
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

function AgentImagePreview({ attachment }: { attachment: AgentImageAttachment }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let currentObjectUrl: string | null = null;
    setObjectUrl(null);
    setFailed(false);
    void authenticatedFetch(attachment.url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        if (blob.type !== attachment.media_type) throw new Error("Unexpected image type");
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
  }, [attachment.media_type, attachment.url]);

  return (
    <figure className={`agent-message-image${failed ? " failed" : ""}`}>
      {objectUrl ? (
        <img
          alt={attachment.original_name || "Attached image"}
          height={attachment.height}
          src={objectUrl}
          width={attachment.width}
        />
      ) : (
        <figcaption>{failed ? "Image preview unavailable" : "Loading image preview…"}</figcaption>
      )}
    </figure>
  );
}

export function AgentImageGallery({ attachments }: { attachments: AgentImageAttachment[] }) {
  if (attachments.length === 0) return null;
  return (
    <div className="agent-message-images" aria-label="Attached Images">
      {attachments.map((attachment) => (
        <AgentImagePreview attachment={attachment} key={attachment.attachment_id} />
      ))}
    </div>
  );
}

export function AgentReceiptCard({ receipt }: { receipt: AgentReceipt }) {
  return (
    <article className="agent-rail-receipt" id={`copilot-receipt-${receipt.receipt_id}`}>
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
  recoveredStatus,
}: AgentMessageCardProps) {
  const isUser = message.role === "USER";
  const modelReceipt = asRecord(message.model_receipt);
  const research = parseCopilotResearch(modelReceipt.research);
  const evidenceRefs = useMemo(() => {
    let envelope = modelReceipt.answer_envelope;
    if (typeof envelope === "string") { try { envelope = JSON.parse(envelope); } catch { envelope = null; } }
    const blocks = asRecord(envelope).blocks;
    const refs = (Array.isArray(blocks) ? blocks : []).flatMap((block) => { const values = asRecord(block).evidence_refs; return Array.isArray(values) ? values.filter((value): value is string => typeof value === "string") : []; });
    return [...new Set([...refs, ...(research?.verified_refs ?? [])])].slice(0, 64).map((ref) => ({ ref, receipt: receipts.find((receipt) => ref === receipt.request_id || ref.startsWith(`${receipt.request_id}/`)) }));
  }, [modelReceipt.answer_envelope, receipts, research?.verified_refs]);
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
        <span>{isUser ? "You" : "Copilot"}</span>
        <div className="agent-message-meta">
          <time>{displayDate(message.created_at)}</time>
          <div className="agent-message-actions" aria-label={`${isUser ? "User" : "Copilot"} message actions`}>
            <IconButton
              aria-label={copied ? "Message Copied" : "Copy Message"}
              className={copied ? "success" : ""}
              onClick={() => onCopy(message)}
              size="sm"
              title={copied ? "Copied" : "Copy"}
              type="button"
            >
              {copied ? <Check aria-hidden="true" size={11} /> : <Copy aria-hidden="true" size={11} />}
            </IconButton>
            {isUser ? (
              <IconButton
                aria-label="Edit This Prompt and Resend"
                disabled={disabled}
                onClick={() => onEdit(message)}
                size="sm"
                title="Edit and Resend as a New Turn"
                type="button"
              >
                <PencilLine aria-hidden="true" size={11} />
              </IconButton>
            ) : (
              <IconButton
                aria-label="Retry the Prompt for This Response"
                disabled={disabled}
                onClick={() => onRetry(message)}
                size="sm"
                title="Retry as a New Turn"
                type="button"
              >
                <RefreshCw aria-hidden="true" size={11} />
              </IconButton>
            )}
          </div>
        </div>
      </header>
      <AgentImageGallery attachments={message.attachments} />
      <AgentMessageContent content={message.content} />
      {research && (!isUser || recoveredStatus) && <CopilotResearchProgress research={research} recoveredStatus={isUser ? recoveredStatus : undefined} receipts={receipts} usage={isUser ? undefined : asRecord(modelReceipt.usage)} />}
      {evidenceRefs.length > 0 && <div aria-label="Answer Evidence">{evidenceRefs.map(({ ref, receipt }) => receipt ? <TextLink key={ref} href={`#copilot-receipt-${receipt.receipt_id}`} onClick={(event) => { event.preventDefault(); const target = document.getElementById(`copilot-receipt-${receipt.receipt_id}`); const disclosure = target?.closest("details"); if (disclosure) disclosure.open = true; window.requestAnimationFrame(() => target?.scrollIntoView({ block: "nearest", behavior: "auto" })); }}>{ref}</TextLink> : <span key={ref}>{ref} · receipt unavailable</span>)}</div>}
      <AgentArtifactGallery urls={artifactUrls} />
      {!!sourceUrls.length && (
        <div className="agent-rail-source-block" aria-label="Web Sources">
          <span>Web Context · {sourceUrls.length}</span>
          {sourceUrls.map((url, index) => (
            <TextLink href={url} key={url} rel="noopener noreferrer" target="_blank">
              <ExternalLink aria-hidden="true" size={10} /> {index + 1}. {sourceHostname(url)}
            </TextLink>
          ))}
        </div>
      )}
      {!!receipts.length && (
        <Disclosure
          className="agent-message-evidence"
          title={`Evidence & Tools · ${receipts.length}`}
          variant="compact"
        >
          {receipts.map((receipt) => <AgentReceiptCard key={receipt.receipt_id} receipt={receipt} />)}
        </Disclosure>
      )}
    </article>
  );
}
