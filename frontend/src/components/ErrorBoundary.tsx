import { Component, type ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 text-center">
          <p className="text-lg font-semibold text-gray-800">Something went wrong</p>
          <p className="max-w-sm text-sm text-gray-500">{this.state.error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm text-white hover:bg-brand-700"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
