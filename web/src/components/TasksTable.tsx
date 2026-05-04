import { useState } from "react";
import type { Task } from "../types";
import { fmtDate } from "../lib/format";
import {
  latestInteractionLabel,
  lifecycleFor,
  triggerLabel,
} from "../lib/lifecycle";
import { LifecycleIndicator } from "./LifecycleIndicator";

interface Props {
  tasks: Task[];
  onRefresh: (id: number) => Promise<void>;
  onView: (id: number) => void;
}

const COLS: string[] = [
  "Lifecycle",
  "Issue",
  "Requested by",
  "Trigger",
  "Latest interaction",
  "Devin",
  "PR",
  "Last updated",
  "Actions",
];

export function TasksTable({ tasks, onRefresh, onView }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null);

  const handleRefresh = async (id: number) => {
    setBusyId(id);
    try {
      await onRefresh(id);
    } finally {
      setBusyId(null);
    }
  };

  if (tasks.length === 0) {
    return (
      <table className="tasks">
        <thead>
          <tr>
            {COLS.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={COLS.length} className="muted empty-cell">
              No remediation tasks yet. Simulate a workflow or comment{" "}
              <code>@devin</code> on a GitHub issue.
            </td>
          </tr>
        </tbody>
      </table>
    );
  }

  return (
    <table className="tasks">
      <thead>
        <tr>
          {COLS.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {tasks.map((t) => {
          const lc = lifecycleFor(t.status, !!t.pr_url);
          return (
            <tr key={t.id} className="row">
              <td>
                <LifecycleIndicator steps={lc.steps} awaitingUser={lc.awaitingUser} />
              </td>
              <td className="cell-issue">
                <a
                  className="issue-title"
                  href={t.issue_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={t.issue_title}
                >
                  #{t.issue_number} {t.issue_title || "(no title)"}
                </a>
                <div className="sub muted">{t.repo_full_name}</div>
              </td>
              <td>{t.requested_by ?? "—"}</td>
              <td>
                <span className="trigger-tag">{triggerLabel(t.trigger_source)}</span>
              </td>
              <td>
                {latestInteractionLabel(t.status, t.last_event_type)}
                {t.last_event_at && (
                  <div className="sub muted small">
                    {fmtDate(t.last_event_at)}
                  </div>
                )}
              </td>
              <td>
                {t.devin_session_url ? (
                  <a
                    href={t.devin_session_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open Devin ↗
                  </a>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td>
                {t.pr_url ? (
                  <a href={t.pr_url} target="_blank" rel="noopener noreferrer">
                    Open PR ↗
                  </a>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td className="muted small">{fmtDate(t.updated_at)}</td>
              <td>
                <div className="row-actions">
                  <button
                    className="btn ghost"
                    disabled={busyId === t.id}
                    onClick={() => handleRefresh(t.id)}
                  >
                    {busyId === t.id ? "…" : "Refresh"}
                  </button>
                  <button className="btn" onClick={() => onView(t.id)}>
                    Details
                  </button>
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
