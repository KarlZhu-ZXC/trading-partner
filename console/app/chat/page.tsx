"use client";

import { ConsoleShell } from "../components/console-shell";
import { ChatWorkspace } from "./chat-workspace";

export default function ChatPage() {
  return (
    <ConsoleShell active="chat" eyebrow="Shared Agent Runtime" title="Agent Chat">
      <ChatWorkspace />
    </ConsoleShell>
  );
}
