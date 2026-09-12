"use client";

import { useTranslations } from "next-intl";
import { ArrowRight, Check, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type Props = {
  assessment: string;
  currentParams: Record<string, number>;
  suggestedParams: Record<string, number>;
  confidence: number;
  reasoning: string;
  logId: number | null;
  applied: boolean;
  botRunning: boolean;
  onApply: (logId: number) => void;
};

export default function OptimizationReport({
  assessment, currentParams, suggestedParams, confidence, reasoning, logId, applied, botRunning, onApply,
}: Props) {
  const t = useTranslations("ai.optimization");
  const paramKeys = Array.from(
    new Set([...Object.keys(currentParams), ...Object.keys(suggestedParams)])
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg flex items-center gap-2">
          <Sparkles className="size-5 text-primary-foreground dark:text-primary" />
          {t("title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">{assessment}</p>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("parameter")}</TableHead>
              <TableHead className="text-right">{t("current")}</TableHead>
              <TableHead className="text-center w-10"></TableHead>
              <TableHead className="text-right">{t("suggested")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paramKeys.map((key) => {
              const current = currentParams[key];
              const suggested = suggestedParams[key];
              const changed = current !== suggested;
              return (
                <TableRow key={key}>
                  <TableCell className="font-semibold text-foreground">{key}</TableCell>
                  <TableCell className="text-right font-mono text-muted-foreground">{current}</TableCell>
                  <TableCell className="text-center">
                    {changed && <ArrowRight className="size-3.5 text-primary-foreground dark:text-primary mx-auto" />}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right font-mono",
                      changed ? "text-primary-foreground dark:text-primary font-bold" : "text-muted-foreground"
                    )}
                  >
                    {suggested}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground font-medium">{t("confidence")}</span>
            <span className="text-xs font-mono text-foreground font-bold">
              {(confidence * 100).toFixed(0)}%
            </span>
          </div>
          <Progress value={confidence * 100} className="h-2" />
        </div>

        <p className="text-xs text-muted-foreground leading-relaxed">{reasoning}</p>

        {logId && !applied && (
          <Button
            onClick={() => onApply(logId)}
            disabled={botRunning}
            className="w-full rounded-full bg-primary text-primary-foreground font-semibold hover-scale"
          >
            {botRunning ? t("stopBotToApply") : t("applySuggestions")}
          </Button>
        )}
        {applied && (
          <div className="flex items-center justify-center gap-2 py-2 text-sm text-success dark:text-green-400 font-semibold">
            <Check className="size-4" />
            <span>{t("appliedSuccessfully")}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
