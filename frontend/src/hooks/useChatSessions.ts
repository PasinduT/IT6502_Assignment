import { useEffect, useState } from "react";
import { createSession, loadSessions, saveSessions } from "../lib/chatStorage";
import type { ChatMessage, ChatSession } from "../types/chat";

function initialState(): ChatSession[] {
  const stored = loadSessions();
  return stored.length ? stored : [createSession()];
}

export function useChatSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>(initialState);
  const [activeId, setActiveId] = useState(() => sessions[0].id);

  useEffect(() => saveSessions(sessions), [sessions]);

  const activeSession = sessions.find((session) => session.id === activeId) ?? sessions[0];

  function newSession() {
    const session = createSession();
    setSessions((current) => [session, ...current]);
    setActiveId(session.id);
    return session.id;
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
      setActiveId(replacement.id);
      return;
    }
    setSessions(remaining);
    if (activeId === id) setActiveId(remaining[0].id);
  }

  function clearAll() {
    const replacement = createSession();
    setSessions([replacement]);
    setActiveId(replacement.id);
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

