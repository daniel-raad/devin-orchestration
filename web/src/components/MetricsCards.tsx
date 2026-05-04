import type { Metrics } from "../types";
import { fmtMinutes } from "../lib/format";

interface Props {
  metrics: Metrics | null;
}

type Field = {
  key: keyof Metrics;
  label: string;
  kind: "count" | "minutes";
  hint?: string;
};

const FIELDS: Field[] = [
  { key: "total_devin_mentions", label: "Total @devin mentions", kind: "count" },
  { key: "unique_issues", label: "Unique issues", kind: "count", hint: "Distinct (repo, issue) pairs" },
  { key: "unique_requesters", label: "Unique requesters", kind: "count", hint: "Distinct GitHub authors" },
  { key: "active_sessions", label: "Active sessions", kind: "count" },
  { key: "awaiting_review", label: "Awaiting review", kind: "count", hint: "PRs opened, pending human review" },
  { key: "prs_opened", label: "PRs opened", kind: "count", hint: "Total PRs ever opened (including merged or closed)" },
  { key: "completed_tasks", label: "PRs merged", kind: "count", hint: "Closed and merged on GitHub" },
  { key: "done_no_change", label: "Done — no PR", kind: "count", hint: "Devin investigated and concluded no code change was needed" },
  { key: "closed_without_fix", label: "Closed without fix", kind: "count", hint: "Issue closed without a merged PR — needs a human" },
  { key: "failed_tasks", label: "Errors", kind: "count", hint: "Devin or orchestrator failures (not closed PRs)" },
  { key: "followups_forwarded", label: "Follow-ups forwarded", kind: "count" },
  { key: "average_time_to_pr_minutes", label: "Avg time to PR", kind: "minutes" },
  { key: "average_time_to_completion_minutes", label: "Avg time to completion", kind: "minutes" },
];

function display(metrics: Metrics | null, field: Field): string {
  if (!metrics) return "—";
  const v = metrics[field.key];
  if (field.kind === "minutes") {
    return fmtMinutes(v as number | null | undefined);
  }
  if (v === null || v === undefined) return "—";
  return String(v);
}

export function MetricsCards({ metrics }: Props) {
  return (
    <section className="cards" aria-label="Metrics">
      {FIELDS.map((f) => (
        <div className="card" key={f.key as string}>
          <div className="label">{f.label}</div>
          <div className="value">{display(metrics, f)}</div>
          {f.hint && <div className="hint">{f.hint}</div>}
        </div>
      ))}
    </section>
  );
}
