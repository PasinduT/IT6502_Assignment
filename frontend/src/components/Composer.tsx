import { ArrowUp, LoaderCircle } from "lucide-react";
import { FormEvent, KeyboardEvent, useState } from "react";

type Props = { disabled: boolean; onSend: (value: string) => void };

export function Composer({ disabled, onSend }: Props) {
  const [value, setValue] = useState("");

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const question = value.trim();
    if (!question || disabled) return;
    onSend(question);
    setValue("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form className="mx-auto w-full max-w-3xl" onSubmit={submit}>
      <div className="flex items-end gap-2 rounded-2xl border border-emerald-950/15 bg-white p-2 shadow-soft focus-within:border-leaf/50">
        <textarea
          aria-label="Ask a Sri Lankan tax question"
          className="max-h-40 min-h-12 flex-1 resize-none bg-transparent px-3 py-3 text-[15px] outline-none placeholder:text-slate-400"
          maxLength={8000}
          placeholder="Ask about Sri Lankan tax or portal steps…"
          rows={1}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          aria-label="Send message"
          className="mb-1 grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-ink text-white transition hover:bg-leaf disabled:cursor-not-allowed disabled:opacity-40"
          disabled={disabled || !value.trim()}
          type="submit"
        >
          {disabled ? <LoaderCircle className="animate-spin" size={18} /> : <ArrowUp size={19} />}
        </button>
      </div>
      <p className="px-2 pt-2 text-center text-[11px] leading-4 text-slate-500">
        AI-generated informational guidance. Verify important decisions with official Sri Lankan sources or a qualified professional. Do not share credentials or confidential taxpayer data.
      </p>
    </form>
  );
}

