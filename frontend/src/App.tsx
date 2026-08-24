import { Menu, RefreshCw, Scale } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { Message } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import { useChatSessions } from "./hooks/useChatSessions";
import { sendChat } from "./lib/api";
import { titleFromQuestion } from "./lib/chatStorage";
import type { ChatMessage } from "./types/chat";

const suggestions = [
  "What Sri Lankan tax topics can you help with?",
  "How do I find a return in the tax portal?",
  "Explain the source and effective date of your answer.",
];

export default function App() {
  const chats = useChatSessions();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [chats.activeSession.messages, loading]);

  async function submit(question: string, retry = false) {
    if (loading) return;
    const sessionId = chats.activeSession.id;
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: question,
      createdAt: new Date().toISOString(),
    };
    const outgoing = retry ? chats.activeSession.messages : [...chats.activeSession.messages, userMessage];
    if (!retry) {
      chats.addMessage(sessionId, userMessage);
      if (chats.activeSession.messages.length === 0) {
        chats.updateSession(sessionId, { title: titleFromQuestion(question) });
      }
    }
    setLoading(true);
    setError(null);
    try {
      const response = await sendChat(outgoing);
      chats.addMessage(sessionId, {
        id: crypto.randomUUID(),
        role: "assistant",
        content: response.answer,
        citations: response.citations,
        createdAt: new Date().toISOString(),
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The assistant could not answer right now.");
    } finally {
      setLoading(false);
    }
  }

  function rename(id: string) {
    const session = chats.sessions.find((item) => item.id === id);
    const title = window.prompt("Conversation name", session?.title ?? "");
    if (title?.trim()) chats.updateSession(id, { title: title.trim().slice(0, 80) });
  }

  const lastUser = [...chats.activeSession.messages].reverse().find((message) => message.role === "user");

  return (
    <div className="flex h-dvh overflow-hidden bg-cream text-ink">
      <Sidebar
        sessions={chats.sessions}
        activeId={chats.activeId}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={(id) => { chats.setActiveId(id); setSidebarOpen(false); setError(null); }}
        onNew={() => { chats.newSession(); setSidebarOpen(false); setError(null); }}
        onRename={rename}
        onDelete={chats.removeSession}
        onClearAll={chats.clearAll}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-emerald-950/10 bg-cream/90 px-4 backdrop-blur md:px-7">
          <button className="rounded-lg p-2 md:hidden" aria-label="Open menu" onClick={() => setSidebarOpen(true)}>
            <Menu size={21} />
          </button>
          <Scale className="text-leaf" size={21} />
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold md:text-base">{chats.activeSession.title}</h1>
            <p className="text-xs text-slate-500">Sri Lanka-only · Curated sources</p>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full max-w-3xl flex-col px-4 py-8 md:px-6 md:py-12">
            {chats.activeSession.messages.length === 0 ? (
              <div className="my-auto py-8 text-center">
                <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-ink text-saffron shadow-soft">
                  <Scale size={27} />
                </div>
                <h2 className="mt-5 text-2xl font-semibold tracking-tight md:text-3xl">Ask with evidence in view.</h2>
                <p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-slate-600">
                  Get grounded answers about Sri Lankan tax rules and text-based guidance for navigating the tax portal.
                </p>
                <div className="mx-auto mt-7 grid max-w-xl gap-2 md:grid-cols-3">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      className="rounded-xl border border-emerald-950/10 bg-white p-3 text-left text-xs leading-5 text-slate-600 transition hover:border-leaf/40 hover:text-ink"
                      onClick={() => submit(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {chats.activeSession.messages.map((message) => <Message key={message.id} message={message} />)}
                {loading && (
                  <div className="flex justify-start">
                    <div className="flex items-center gap-2 rounded-2xl border border-emerald-950/10 bg-white px-4 py-3 text-sm text-slate-500">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-leaf" /> Searching approved sources…
                    </div>
                  </div>
                )}
                {error && (
                  <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
                    <p>{error}</p>
                    {lastUser && (
                      <button className="mt-2 inline-flex items-center gap-1 font-semibold" onClick={() => submit(lastUser.content, true)}>
                        <RefreshCw size={14} /> Retry
                      </button>
                    )}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-emerald-950/10 bg-cream px-4 pb-3 pt-3 md:px-6">
          <Composer disabled={loading} onSend={submit} />
        </div>
      </main>
    </div>
  );
}

