"use client";

import React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

function ErrorBoundaryFallback({
  error,
  onReset,
}: {
  error: Error | null;
  onReset: () => void;
}) {
  const t = useTranslations("ui");
  return (
    <div className="flex flex-col items-center justify-center py-8 px-4 text-center rounded-xl border border-destructive/20 bg-destructive/5">
      <AlertTriangle className="size-8 text-destructive mb-3" />
      <p className="text-sm font-semibold text-foreground mb-1">
        {t("somethingWrong")}
      </p>
      <p className="text-xs text-muted-foreground mb-4 max-w-xs">
        {error?.message || t("unexpectedError")}
      </p>
      <Button variant="outline" size="sm" onClick={onReset}>
        <RotateCcw className="size-3.5" />
        {t("tryAgain")}
      </Button>
    </div>
  );
}

export class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <ErrorBoundaryFallback
          error={this.state.error}
          onReset={this.handleReset}
        />
      );
    }

    return this.props.children;
  }
}
