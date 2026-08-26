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
  it("renders Markdown structure in assistant responses", () => {
    const { container } = render(
      <Message
        message={assistantMessage(
          "## Filing summary\n\nUse **Form 14** and `submit()` it.\n\n- Gather records\n- File return",
        )}
      />,
    );

    expect(screen.getByRole("heading", { name: "Filing summary", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Form 14")).toHaveStyle({ fontWeight: "bold" });
    expect(screen.getByText("submit()").tagName).toBe("CODE");
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders GitHub-flavored Markdown tables", () => {
    render(
      <Message
        message={assistantMessage(
          "| Year | Rate |\n| --- | ---: |\n| 2026 | 18% |",
        )}
      />,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Year" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "18%" })).toBeInTheDocument();
  });

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

  it("does not create links for unsafe protocols", () => {
    render(<Message message={assistantMessage("[Unsafe](javascript:alert(1))")} />);

    expect(screen.queryByRole("link", { name: "Unsafe" })).not.toBeInTheDocument();
    expect(screen.getByText("Unsafe")).toBeInTheDocument();
  });
});
