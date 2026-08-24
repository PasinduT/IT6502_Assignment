import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { Message } from "./Message";

function assistantMessage(content: string): ChatMessage {
  return {
    id: "message-1",
    role: "assistant",
    content,
    createdAt: "2026-08-24T00:00:00.000Z",
  };
}

describe("Message", () => {
  it("displays HTTPS Markdown images in assistant responses", () => {
    render(<Message message={assistantMessage("Example:\n![Tax chart](https://example.com/chart.png)")} />);

    expect(screen.getByRole("img", { name: "Tax chart" })).toHaveAttribute(
      "src",
      "https://example.com/chart.png",
    );
  });

  it("leaves unsafe Markdown image URLs as text", () => {
    const { container } = render(
      <Message message={assistantMessage("![Unsafe](javascript:alert(1))")} />,
    );
    const message = within(container);

    expect(message.queryByRole("img")).not.toBeInTheDocument();
    expect(message.getByText("![Unsafe](javascript:alert(1))")).toBeInTheDocument();
  });
});
