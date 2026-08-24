import { Component, type ReactNode } from "react";

type ErrorBoundaryProps = {
  children: ReactNode;
};

type ErrorBoundaryState = {
  hasError: boolean;
};

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="grid min-h-dvh place-items-center bg-cream px-6 text-center text-ink">
          <div>
            <h1 className="text-2xl font-semibold">Something went wrong</h1>
            <button
              className="mt-5 rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-white transition hover:bg-leaf"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </div>
        </main>
      );
    }

    return this.props.children;
  }
}