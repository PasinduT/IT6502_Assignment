export type Citation = {
  id: string;
  title: string;
  document_type?: string | null;
  section?: string | null;
  page?: number | null;
  page_end?: number | null;
  sheet?: string | null;
  cell_range?: string | null;
  authority_level?: string | null;
  status?: string | null;
  source_id?: string | null;
  published_date?: string | null;
  effective_from?: string | null;
  tax_year?: string | null;
  url?: string | null;
};

export type GuideImage = {
  id: string;
  url: string;
  alt: string;
  caption?: string | null;
  source_id: string;
  page?: number | null;
};

export type GuideStep = {
  number: number;
  title: string;
  instruction: string;
  image?: GuideImage | null;
  citation_ids: string[];
};

export type Guide = {
  title: string;
  steps: GuideStep[];
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  citations?: Citation[];
  guide?: Guide | null;
};

export type ChatSession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
};

export type ChatResponse = {
  answer: string;
  citations: Citation[];
  guide?: Guide | null;
};
