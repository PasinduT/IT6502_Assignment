import { MessageSquare, Pencil, Plus, Trash2, X } from "lucide-react";
import type { ChatSession } from "../types/chat";

type Props = {
  sessions: ChatSession[];
  activeId: string;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
};

export function Sidebar({
  sessions,
  activeId,
  open,
  onClose,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onClearAll,
}: Props) {
  return (
    <>
      {open && (
        <button
          aria-label="Close menu"
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col bg-ink text-white transition-transform md:static md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex items-center justify-between px-5 pb-4 pt-6">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-emerald-300">
              Sri Lanka
            </div>
            <div className="mt-1 text-lg font-semibold">Tax Assistant</div>
          </div>
          <button aria-label="Close sidebar" className="rounded-lg p-2 md:hidden" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <button
          className="mx-4 flex items-center justify-center gap-2 rounded-xl bg-saffron px-4 py-3 font-semibold text-ink transition hover:bg-amber-300"
          onClick={onNew}
        >
          <Plus size={18} /> New conversation
        </button>

        <div className="mt-5 flex-1 space-y-1 overflow-y-auto px-3">
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`group flex items-center rounded-xl ${session.id === activeId ? "bg-white/12" : "hover:bg-white/7"}`}
            >
              <button
                className="flex min-w-0 flex-1 items-center gap-3 px-3 py-3 text-left"
                onClick={() => onSelect(session.id)}
              >
                <MessageSquare className="shrink-0 text-emerald-300" size={17} />
                <span className="truncate text-sm">{session.title}</span>
              </button>
              <button
                aria-label={`Rename ${session.title}`}
                className="p-2 text-white/60 hover:text-white"
                onClick={() => onRename(session.id)}
              >
                <Pencil size={14} />
              </button>
              <button
                aria-label={`Delete ${session.title}`}
                className="mr-1 p-2 text-white/60 hover:text-red-300"
                onClick={() => onDelete(session.id)}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <div className="border-t border-white/10 p-4">
          <button className="text-sm text-white/60 hover:text-white" onClick={onClearAll}>
            Clear all local chats
          </button>
          <p className="mt-2 text-xs leading-5 text-white/40">Stored only in this browser.</p>
        </div>
      </aside>
    </>
  );
}

