import { X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation, Guide as GuideModel, GuideImage } from "../types/chat";

type GuideProps = {
  guide?: GuideModel | null;
  citations?: Citation[];
  citationAnchorPrefix?: string;
};

function isSafeImageUrl(url: string): boolean {
  return /^https:\/\//i.test(url);
}

function StepInstruction({ instruction }: { instruction: string }) {
  return (
    <div className="markdown-content guide-instruction">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Images in instruction Markdown are never trusted. Approved guide imagery is
          // rendered only from the structured step.image field below.
          img: () => null,
          a: ({ children, href, ...props }) =>
            href ? (
              <a
                {...props}
                href={href}
                target={href.startsWith("#") ? undefined : "_blank"}
                rel={href.startsWith("#") ? undefined : "noreferrer"}
                className="font-medium text-leaf underline decoration-leaf/30 underline-offset-2 hover:decoration-leaf"
              >
                {children}
              </a>
            ) : (
              <>{children}</>
            ),
        }}
      >
        {instruction}
      </ReactMarkdown>
    </div>
  );
}

function StepCitations({
  ids,
  citations,
  citationAnchorPrefix,
}: {
  ids: string[];
  citations: Citation[];
  citationAnchorPrefix: string;
}) {
  const citationMap = new Map(citations.map((citation) => [citation.id, citation]));
  const linked = ids
    .map((id) => ({ id, citation: citationMap.get(id) }))
    .filter((item): item is { id: string; citation: Citation } => Boolean(item.citation));

  if (!linked.length) return null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
      <span className="font-semibold uppercase tracking-wider text-slate-500">Sources</span>
      {linked.map(({ id, citation }) => (
        <a
          key={id}
          href={`#${citationAnchorPrefix}-${citation.id}`}
          className="rounded-full border border-emerald-950/10 bg-emerald-50 px-2.5 py-1 font-medium text-leaf hover:border-leaf/40 hover:underline"
          aria-label={`View citation ${citation.id}: ${citation.title}`}
        >
          [{citation.id}]
        </a>
      ))}
    </div>
  );
}

function GuideStepImage({ image, onExpand }: { image: GuideImage; onExpand: () => void }) {
  const [failed, setFailed] = useState(false);

  if (!isSafeImageUrl(image.url) || failed) {
    return (
      <div className="mt-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-sm text-slate-500">
        <span role="img" aria-label={`${image.alt} unavailable`}>Image unavailable.</span>
      </div>
    );
  }

  return (
    <figure className="mt-3 min-w-0">
      <button
        type="button"
        className="guide-image-button"
        onClick={onExpand}
        aria-label={`Expand image: ${image.alt}`}
      >
        <img
          className="guide-image"
          src={image.url}
          alt={image.alt}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      </button>
      {image.caption && <figcaption className="mt-2 text-xs leading-5 text-slate-500">{image.caption}</figcaption>}
    </figure>
  );
}

function ExpandedImage({ image, onClose }: { image: GuideImage; onClose: () => void }) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div
      className="guide-image-dialog"
      role="dialog"
      aria-modal="true"
      aria-label={`Expanded image: ${image.alt}`}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="guide-image-dialog-panel">
        <button
          type="button"
          autoFocus
          className="absolute right-3 top-3 rounded-full bg-white/90 p-2 text-slate-600 shadow-sm hover:text-ink"
          onClick={onClose}
          aria-label="Close expanded image"
        >
          <X size={18} />
        </button>
        <img src={image.url} alt={image.alt} />
        {image.caption && <p className="mt-2 pr-10 text-xs leading-5 text-slate-500">{image.caption}</p>}
        <a
          href={image.url}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-block text-xs font-medium text-leaf underline underline-offset-2"
        >
          Open original image in a new tab
        </a>
      </div>
    </div>
  );
}

export function Guide({ guide, citations = [], citationAnchorPrefix = "citation" }: GuideProps) {
  const [expandedImage, setExpandedImage] = useState<GuideImage | null>(null);
  const guideTitleId = `guide-title-${useId().replace(/:/g, "")}`;

  if (!guide) return null;

  return (
    <section className="mt-5 border-t border-emerald-950/10 pt-4" aria-labelledby={guideTitleId}>
      <h2 id={guideTitleId} className="text-lg font-bold leading-7 text-ink">{guide.title}</h2>
      <ol className="mt-4 space-y-4" aria-label={guide.title}>
        {guide.steps.map((step) => (
          <li key={step.number} className="guide-step rounded-xl border border-emerald-950/10 bg-emerald-50/40 p-4">
            <h3 className="font-semibold text-ink">{step.number}. {step.title}</h3>
            <StepInstruction instruction={step.instruction} />
            {step.image && (
              <GuideStepImage image={step.image} onExpand={() => setExpandedImage(step.image ?? null)} />
            )}
            <StepCitations
              ids={step.citation_ids}
              citations={citations}
              citationAnchorPrefix={citationAnchorPrefix}
            />
          </li>
        ))}
      </ol>
      {expandedImage && <ExpandedImage image={expandedImage} onClose={() => setExpandedImage(null)} />}
    </section>
  );
}
