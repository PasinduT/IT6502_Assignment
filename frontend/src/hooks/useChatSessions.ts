import { useEffect, useState, useSyncExternalStore } from "react";
import { createSession, loadSessions, saveSessions } from "../lib/chatStorage";
import type { ChatMessage, ChatSession } from "../types/chat";

const SESSION_PATH = /^\/chat\/([^/]+)\/?$/;

function sessionIdFromUrl(): string | null {
  const match = window.location.pathname.match(SESSION_PATH);
  return match?.[1] ?? null;
}

function subscribeToUrl(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

function navigateToSession(id: string, replace = false) {
  const path = `/chat/${id}${window.location.search}${window.location.hash}`;
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function initialState(): ChatSession[] {
  const stored = loadSessions();
  return stored.length ? stored : [createSession()];
}

export function useChatSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>(initialState);
  const requestedId = useSyncExternalStore(subscribeToUrl, sessionIdFromUrl, () => null);
  const activeSession = sessions.find((session) => session.id === requestedId) ?? sessions[0];
  const activeId = activeSession.id;

  useEffect(() => {
    saveSessions(sessions);
  }, [sessions]);

  useEffect(() => {
    if (requestedId !== activeSession.id) navigateToSession(activeSession.id, true);
  }, [activeSession.id, requestedId]);

  function newSession() {
    const session = createSession();
    setSessions((current) => [session, ...current]);
    navigateToSession(session.id);
    return session.id;
  }

  function setActiveId(id: string) {
    if (sessions.some((session) => session.id === id)) navigateToSession(id);
  }

  function updateSession(id: string, update: Partial<ChatSession>) {
    setSessions((current) =>
      current.map((session) =>
        session.id === id
          ? { ...session, ...update, updatedAt: new Date().toISOString() }
          : session,
      ),
    );
  }

  function addMessage(id: string, message: ChatMessage) {
    setSessions((current) =>
      current.map((session) =>
        session.id === id
          ? {
              ...session,
              messages: [...session.messages, message],
              updatedAt: new Date().toISOString(),
            }
          : session,
      ),
    );
  }

  function removeSession(id: string) {
    const remaining = sessions.filter((session) => session.id !== id);
    if (!remaining.length) {
      const replacement = createSession();
      setSessions([replacement]);
      navigateToSession(replacement.id, true);
      return;
    }
    setSessions(remaining);
    if (activeId === id) navigateToSession(remaining[0].id, true);
  }

  function clearAll() {
    const replacement = createSession();
    setSessions([replacement]);
    navigateToSession(replacement.id, true);
  }

  return {
    sessions,
    activeSession,
    activeId,
    setActiveId,
    newSession,
    updateSession,
    addMessage,
    removeSession,
    clearAll,
  };
}

