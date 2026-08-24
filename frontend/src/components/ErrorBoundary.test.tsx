import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ErrorBoundary } from "./ErrorBoundary";

function BrokenComponent(): ReactNode {
  throw new Error("test failure");
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  it("shows a reload action when a child throws", () => {
    render(
      <ErrorBoundary>
        <BrokenComponent />
      </ErrorBoundary>,
    );

    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});