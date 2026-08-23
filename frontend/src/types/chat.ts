export type Citation = {
  id: string;
  title: string;
  document_type?: string | null;
  section?: string | null;
  page?: number | null;
  published_date?: string | null;
  effective_from?: string | null;
  tax_year?: string | null;
  url?: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  citations?: Citation[];
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
};

