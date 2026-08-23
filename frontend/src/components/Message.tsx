import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";
import type { ChatMessage } from "../types/chat";

export function Message({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);
  const assistant = message.role === "assistant";

  async function copy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <article className={`flex ${assistant ? "justify-start" : "justify-end"}`}>
      <div
        className={`max-w-[88%] rounded-2xl px-4 py-3 text-[15px] leading-7 md:max-w-[78%] md:px-5 ${
          assistant
            ? "border border-emerald-950/10 bg-white text-slate-800 shadow-sm"
            : "bg-leaf text-white"
        }`}
      >
        <div className="whitespace-pre-wrap">{message.content}</div>
        {assistant && message.citations && message.citations.length > 0 && (
          <div className="mt-4 border-t border-slate-200 pt-3">
            <p className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-500">Sources</p>
            <ol className="space-y-2">
              {message.citations.map((citation) => {
                const label = [
                  citation.section && `Section ${citation.section}`,
                  citation.page != null && `p. ${citation.page}`,
                  citation.tax_year && `Tax year ${citation.tax_year}`,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const content = (
                  <>
                    <span className="font-semibold">[{citation.id}] {citation.title}</span>
                    {label && <span className="block text-xs text-slate-500">{label}</span>}
                  </>
                );
                return (
                  <li key={`${citation.id}-${citation.title}`} className="text-sm leading-5">
                    {citation.url ? (
                      <a
                        className="inline-flex items-start gap-1 text-leaf hover:underline"
                        href={citation.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <span>{content}</span><ExternalLink className="mt-0.5 shrink-0" size={13} />
                      </a>
                    ) : (
                      content
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
        {assistant && (
          <button
            className="mt-3 flex items-center gap-1 text-xs text-slate-400 hover:text-leaf"
            onClick={copy}
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy"}
          </button>
        )}
      </div>
    </article>
  );
}

