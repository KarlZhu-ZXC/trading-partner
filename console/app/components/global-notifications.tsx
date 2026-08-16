"use client";

import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import {
  CONSOLE_NOTIFICATION_EVENT,
  type ConsoleNotificationDetail,
} from "../lib/notifications";

type NotificationItem = ConsoleNotificationDetail & { id: string };

export function GlobalNotifications() {
  const [items, setItems] = useState<NotificationItem[]>([]);

  useEffect(() => {
    const timers = new Map<string, number>();
    function remove(id: string) {
      setItems((current) => current.filter((item) => item.id !== id));
      const timer = timers.get(id);
      if (timer != null) window.clearTimeout(timer);
      timers.delete(id);
    }
    function receive(event: Event) {
      const detail = (event as CustomEvent<ConsoleNotificationDetail>).detail;
      const id = crypto.randomUUID();
      setItems((current) => [...current, { ...detail, id }].slice(-3));
      timers.set(id, window.setTimeout(() => remove(id), detail.durationMs ?? 4200));
    }
    window.addEventListener(CONSOLE_NOTIFICATION_EVENT, receive);
    return () => {
      window.removeEventListener(CONSOLE_NOTIFICATION_EVENT, receive);
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  if (items.length === 0) return null;
  return (
    <div className="global-notification-stack" aria-label="Notifications">
      {items.map((item) => {
        const tone = item.tone ?? "info";
        const Icon = tone === "success" ? CheckCircle2 : tone === "error" ? XCircle : Info;
        return (
          <div className={`global-notification ${tone}`} role={tone === "error" ? "alert" : "status"} key={item.id}>
            <Icon aria-hidden="true" />
            <div><strong>{item.title}</strong>{item.message ? <span>{item.message}</span> : null}</div>
            <button type="button" aria-label="Dismiss Notification" onClick={() => setItems((current) => current.filter((candidate) => candidate.id !== item.id))}><X aria-hidden="true" /></button>
          </div>
        );
      })}
    </div>
  );
}
