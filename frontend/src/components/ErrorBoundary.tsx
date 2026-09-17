"use client";

import React from "react";
import { ErrorBox } from "@/components/ui";

interface Props {
  children: React.ReactNode;
  /** Remount the boundary (clearing the error) when this changes, e.g. tab switches. */
  resetKey?: string | null;
  label?: string;
}

interface State {
  error: Error | null;
}

/** Catches render crashes in a workspace section so one bad view cannot blank the whole app. */
export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error): void {
    console.error(`[ErrorBoundary${this.props.label ? `:${this.props.label}` : ""}]`, error);
  }

  componentDidUpdate(prevProps: Props): void {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render(): React.ReactNode {
    if (this.state.error) {
      return (
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 w-full">
          <div className="max-w-2xl mx-auto space-y-3">
            <ErrorBox
              message={`This view crashed: ${this.state.error.message || "unknown render error"}`}
              onRetry={() => this.setState({ error: null })}
            />
            <p className="text-[11px] text-muted-foreground">
              Your chats and local history are safe — try reloading the view, or switch tabs and come back.
            </p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
