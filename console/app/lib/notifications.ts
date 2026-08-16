export const CONSOLE_NOTIFICATION_EVENT = "trading-partner:console-notification";

export type ConsoleNotificationTone = "success" | "error" | "info";

export type ConsoleNotificationDetail = {
  title: string;
  message?: string;
  tone?: ConsoleNotificationTone;
  durationMs?: number;
};

export function notifyConsole(detail: ConsoleNotificationDetail) {
  window.dispatchEvent(new CustomEvent<ConsoleNotificationDetail>(CONSOLE_NOTIFICATION_EVENT, { detail }));
}
