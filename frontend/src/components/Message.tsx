import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Guide } from "./Guide";
import type { ChatMessage } from "../types/chat";

function AssistantContent({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href, ...props }) =>
            href ? (
              <a
                {...props}
                className="font-medium text-leaf underline decoration-leaf/30 underline-offset-2 hover:decoration-leaf"
                href={href}
                rel="noreferrer"
                target={href.startsWith("#") ? undefined : "_blank"}
              >
                {children}
              </a>
            ) : (
              <>{children}</>
            ),
          blockquote: ({ children, ...props }) => (
            <blockquote
              {...props}
              className="my-3 border-l-4 border-emerald-200 pl-4 italic text-slate-600"
            >
              {children}
            </blockquote>
          ),
          h1: ({ children, ...props }) => (
            <h1 {...props} className="mb-3 mt-5 text-xl font-bold leading-7 first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children, ...props }) => (
            <h2 {...props} className="mb-2 mt-5 text-lg font-bold leading-7 first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children, ...props }) => (
            <h3 {...props} className="mb-2 mt-4 font-bold first:mt-0">
              {children}
            </h3>
          ),
          // Trust images only through the structured guide contract. Markdown image syntax
          // in an answer is represented as text and never becomes an <img> element.
          img: ({ alt }) => <span>{alt ? `[Image: ${alt}]` : "[Image omitted]"}</span>,
          ol: ({ children, ...props }) => (
            <ol {...props} className="my-3 list-decimal space-y-1 pl-6">
              {children}
            </ol>
          ),
          p: ({ children, ...props }) => (
            <p {...props} className="mb-3 last:mb-0">
              {children}
            </p>
          ),
          pre: ({ children, ...props }) => (
            <pre
              {...props}
              className="my-3 max-w-full overflow-x-auto rounded-xl bg-slate-900 p-4 text-sm leading-6 text-slate-100"
            >
              {children}
            </pre>
          ),
          table: ({ children, ...props }) => (
            <div className="my-3 overflow-x-auto rounded-lg border border-slate-200">
              <table {...props} className="w-full border-collapse text-left text-sm">
                {children}
              </table>
            </div>
          ),
          th: ({ children, ...props }) => (
            <th {...props} className="border-b border-slate-200 bg-slate-50 px-3 py-2 font-semibold">
              {children}
            </th>
          ),
          td: ({ children, ...props }) => (
            <td {...props} className="border-b border-slate-100 px-3 py-2 align-top last:border-b-0">
              {children}
            </td>
          ),
          ul: ({ children, ...props }) => (
            <ul {...props} className="my-3 list-disc space-y-1 pl-6">
              {children}
            </ul>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

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
        {assistant ? (
          <AssistantContent content={message.content} />
        ) : (
          <div className="whitespace-pre-wrap">{message.content}</div>
        )}
        {assistant && message.guide && (
          <Guide
            guide={message.guide}
            citations={message.citations}
            citationAnchorPrefix={`citation-${message.id}`}
          />
        )}
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
                  <li
                    key={`${citation.id}-${citation.title}`}
                    id={`citation-${message.id}-${citation.id}`}
                    className="scroll-mt-4 text-sm leading-5"
                  >
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
