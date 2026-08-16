"use client";

import Link from "next/link";
import type { ReactNode } from "react";

type AgentMessageContentProps = {
  content: string;
};

const INLINE_TOKEN = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<]+|\b(?:case|monitor|trade_plan|thesis|event|report)_[A-Za-z0-9_-]+\b)/g;

function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:")
      && !url.username
      && !url.password
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function entityHref(entityId: string): string | null {
  if (entityId.startsWith("case_")) return `/research#subject-${encodeURIComponent(entityId)}`;
  if (entityId.startsWith("monitor_")) return `/monitors#monitor-${encodeURIComponent(entityId)}`;
  if (entityId.startsWith("trade_plan_")) return "/decision-workbench";
  if (entityId.startsWith("thesis_")) return "/research";
  if (entityId.startsWith("event_") || entityId.startsWith("report_")) return "/research";
  return null;
}

function inlineContent(value: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(INLINE_TOKEN)) {
    const index = match.index ?? 0;
    if (index > cursor) parts.push(value.slice(cursor, index));
    const token = match[0];
    const key = `${keyPrefix}-${index}`;
    if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("[")) {
      const linkMatch = token.match(/^\[([^\]]+)\]\((.+)\)$/);
      const href = linkMatch ? safeExternalUrl(linkMatch[2]) : null;
      parts.push(href
        ? <a href={href} key={key} rel="noopener noreferrer" target="_blank">{linkMatch?.[1]}</a>
        : token);
    } else if (token.startsWith("http://") || token.startsWith("https://")) {
      const punctuation = token.match(/[.,;:!?]+$/)?.[0] ?? "";
      const rawUrl = punctuation ? token.slice(0, -punctuation.length) : token;
      const href = safeExternalUrl(rawUrl);
      parts.push(href
        ? <a href={href} key={key} rel="noopener noreferrer" target="_blank">{rawUrl}</a>
        : rawUrl);
      if (punctuation) parts.push(punctuation);
    } else {
      const href = entityHref(token);
      parts.push(href ? <Link className="agent-entity-link" href={href} key={key}>{token}</Link> : token);
    }
    cursor = index + token.length;
  }
  if (cursor < value.length) parts.push(value.slice(cursor));
  return parts;
}

function isTableSeparator(line: string): boolean {
  const cells = line.trim().replace(/^\||\|$/g, "").split("|");
  return cells.length > 1 && cells.every((cell) => /^\s*:?-{3,}:?\s*$/.test(cell));
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

export function AgentMessageContent({ content }: AgentMessageContentProps) {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    if (/^```/.test(line.trim())) {
      const language = line.trim().slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(<pre className="agent-message-code" key={`code-${index}`}><code data-language={language || undefined}>{code.join("\n")}</code></pre>);
      continue;
    }

    if (index + 1 < lines.length && line.includes("|") && isTableSeparator(lines[index + 1])) {
      const header = tableCells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) rows.push(tableCells(lines[index++]));
      blocks.push(
        <div className="agent-message-table-wrap" key={`table-${index}`}>
          <table><thead><tr>{header.map((cell, cellIndex) => <th key={cellIndex}>{inlineContent(cell, `th-${index}-${cellIndex}`)}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{header.map((_, cellIndex) => <td key={cellIndex}>{inlineContent(row[cellIndex] ?? "", `td-${index}-${rowIndex}-${cellIndex}`)}</td>)}</tr>)}</tbody></table>
        </div>,
      );
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const children = inlineContent(heading[2], `heading-${index}`);
      blocks.push(level === 1 ? <h3 key={index}>{children}</h3> : level === 2 ? <h4 key={index}>{children}</h4> : <h5 key={index}>{children}</h5>);
      index += 1;
      continue;
    }

    const listMatch = line.match(/^\s*(?:[-*+]|(\d+)\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[1]);
      const items: string[] = [];
      while (index < lines.length) {
        const match = lines[index].match(/^\s*(?:[-*+]|(\d+)\.)\s+(.+)$/);
        if (!match || Boolean(match[1]) !== ordered) break;
        items.push(match[2]);
        index += 1;
      }
      const children = items.map((item, itemIndex) => <li key={itemIndex}>{inlineContent(item, `li-${index}-${itemIndex}`)}</li>);
      blocks.push(ordered ? <ol key={`ol-${index}`}>{children}</ol> : <ul key={`ul-${index}`}>{children}</ul>);
      continue;
    }

    const paragraph: string[] = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim()
      && !/^(?:```|#{1,3}\s|\s*(?:[-*+]|\d+\.)\s)/.test(lines[index])) {
      if (lines[index].includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])) break;
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`p-${index}`}>{inlineContent(paragraph.join("\n"), `p-${index}`)}</p>);
  }

  return <div className="agent-message-content">{blocks}</div>;
}
