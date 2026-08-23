import type { ChatSession } from "../types/chat";

export const STORAGE_KEY = "lk-tax-assistant:sessions:v1";

export function createSession(): ChatSession {
  const now = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    title: "New conversation",
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

export function loadSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const sessions = JSON.parse(raw) as ChatSession[];
    if (!Array.isArray(sessions)) return [];
    return sessions.filter(
      (session) =>
        typeof session?.id === "string" &&
        typeof session?.title === "string" &&
        Array.isArray(session?.messages),
    );
  } catch {
    return [];
  }
}

export function saveSessions(sessions: ChatSession[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

export function titleFromQuestion(question: string): string {
  const compact = question.replace(/\s+/g, " ").trim();
  return compact.length > 42 ? `${compact.slice(0, 42)}…` : compact;
}

