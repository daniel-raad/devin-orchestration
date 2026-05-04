import { useState } from "react";
import type { SimulatePayload, SimulateResult } from "../types";
import { api } from "../lib/api";

interface Props {
  onResult: () => void;
  onViewTask?: (id: number) => void;
}

const DEFAULTS: SimulatePayload = {
  repo_full_name: "your-org/superset",
  issue_number: 1,
  issue_title: "[VULN] Demo vulnerability remediation issue",
  issue_url: "https://github.com/your-org/superset/issues/1",
  issue_body:
    "This is a demo vulnerability issue used to test the Devin orchestration workflow.",
  comment_body: "@devin please investigate and remediate this issue",
  comment_author: "demo-user",
};

export function SimulatePanel({ onResult, onViewTask }: Props) {
  const [form, setForm] = useState<SimulatePayload>(DEFAULTS);
  const [result, setResult] = useState<SimulateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = <K extends keyof SimulatePayload>(k: K, v: SimulatePayload[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.simulate(form);
      setResult(res);
      onResult();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const successMessage = (() => {
    if (!result) return null;
    switch (result.action) {
      case "session_created":
        return "Simulated comment accepted — Devin session created.";
      case "followup_forwarded":
        return "Simulated comment accepted — forwarded into the existing Devin session.";
      case "duplicate_ignored":
        return "Comment ignored as duplicate (idempotency).";
      case "previous_task_complete":
        return "A previous remediation for this issue is already complete. Add `retry` in the comment to start a new one.";
      case "ignored":
        return `Ignored: ${result.reason || "unspecified"}`;
      case "session_failed":
        return `Session failed to start: ${result.error || "unknown error"}`;
      default:
        return `Result: ${result.action}`;
    }
  })();

  return (
    <details className="simulate panel">
      <summary>
        <span>Simulate workflow</span>
        <span className="muted small">click to expand</span>
      </summary>
      <p className="muted small simulate-help">
        No webhook setup? Use this to simulate a GitHub issue comment containing{" "}
        <code>@devin</code>. Runs the same orchestration code path as a real
        webhook.
      </p>
      <form onSubmit={onSubmit} className="form-grid">
        <label>
          repo_full_name
          <input
            value={form.repo_full_name}
            onChange={(e) => set("repo_full_name", e.target.value)}
          />
        </label>
        <label>
          issue_number
          <input
            type="number"
            value={form.issue_number}
            onChange={(e) => set("issue_number", Number(e.target.value))}
          />
        </label>
        <label className="full">
          issue_title
          <input
            value={form.issue_title}
            onChange={(e) => set("issue_title", e.target.value)}
          />
        </label>
        <label className="full">
          issue_url
          <input
            value={form.issue_url}
            onChange={(e) => set("issue_url", e.target.value)}
          />
        </label>
        <label className="full">
          issue_body
          <textarea
            value={form.issue_body}
            onChange={(e) => set("issue_body", e.target.value)}
          />
        </label>
        <label className="full">
          comment_body
          <textarea
            value={form.comment_body}
            onChange={(e) => set("comment_body", e.target.value)}
          />
        </label>
        <label>
          comment_author
          <input
            value={form.comment_author}
            onChange={(e) => set("comment_author", e.target.value)}
          />
        </label>
        <div className="actions full">
          <button className="btn primary" disabled={submitting} type="submit">
            {submitting ? "Submitting…" : "Simulate @devin comment"}
          </button>
          {successMessage && (
            <span
              className={`inline-toast ${
                result?.action === "session_created" ||
                result?.action === "followup_forwarded"
                  ? "success"
                  : "info"
              }`}
            >
              {successMessage}
            </span>
          )}
          {error && (
            <span className="inline-toast error" role="alert">
              {error}
            </span>
          )}
          {result?.task_id && onViewTask && (
            <button
              type="button"
              className="btn ghost"
              onClick={() => onViewTask(result.task_id as number)}
            >
              View task details
            </button>
          )}
        </div>
      </form>
    </details>
  );
}
