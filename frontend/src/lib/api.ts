import type { ChatMessage, ChatResponse } from "../types/chat";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code = "UNKNOWN_ERROR",
  ) {
    super(message);
  }
}

export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: messages.slice(-12).map(({ role, content }) => ({ role, content })),
      }),
    });
  } catch {
    throw new ApiError("Could not connect to the assistant. Check your connection.", "NETWORK_ERROR");
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      payload?.error?.message || "The assistant could not answer right now.",
      payload?.error?.code || "API_ERROR",
    );
  }
  return payload as ChatResponse;
}

