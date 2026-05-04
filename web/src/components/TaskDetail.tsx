import { useEffect, useMemo, useState } from "react";
import type {
  EventSource,
  InteractionEvent,
  TaskDetail as TaskDetailType,
} from "../types";
import { fmtDate, fmtSecondsAsMinutes, prShort } from "../lib/format";
import {
  latestInteractionLabel,
  lifecycleFor,
  triggerLabel,
} from "../lib/lifecycle";
import { StatusBadge } from "./StatusBadge";
import { LifecycleIndicator } from "./LifecycleIndicator";
import { api } from "../lib/api";

interface Props {
  detail: TaskDetailType;
  onClose: () => void;
  onChange: () => void;
}

interface FallbackEvent {
  id: string;
  source: EventSource;
  event_type: string;
  body: string;
  created_at: string;
}

function buildFallbackTimeline(detail: TaskDetailType): FallbackEvent[] {
  const { task } = detail;
  const prTime = task.last_devin_update_at || task.updated_at;
  const candidates: (FallbackEvent | null)[] = [
    { id: "fb-issue", source: "github", event_type: "issue_detected",
      body: `Issue #${task.issue_number} detected: ${task.issue_title || "(no title)"}`,
      created_at: task.created_at },
    (task.devin_session_id || task.devin_session_url)
      ? { id: "fb-session", source: "orchestrator", event_type: "session_started",
          body: task.devin_session_url ? `Devin session started: ${task.devin_session_url}` : "Devin session started",
          created_at: task.created_at }
      : null,
    task.pr_url
      ? { id: "fb-pr", source: "devin", event_type: "pr_opened",
          body: `PR opened: ${task.pr_url}`, created_at: prTime }
      : null,
    task.pr_url
      ? { id: "fb-review", source: "orchestrator", event_type: "awaiting_review",
          body: "Awaiting human review of the Devin-authored PR.", created_at: prTime }
      : null,
    task.status === "done"
      ? { id: "fb-done", source: "devin", event_type: "done",
          body: "Devin's PR was merged.", created_at: task.updated_at }
      : null,
    task.status === "failed"
      ? { id: "fb-failed", source: "devin", event_type: "failed",
          body: task.error ? `Devin reported a failure: ${task.error}` : "Devin reported a failure.",
          created_at: task.updated_at }
      : null,
  ];
  return candidates.filter((e): e is FallbackEvent => e !== null);
}

function sourceTag(source: EventSource): string {
  switch (source) {
    case "github":
      return "GitHub";
    case "devin":
      return "Devin";
    case "orchestrator":
      return "Orchestrator";
    default:
      return source;
  }
}

function lastEventType(events: InteractionEvent[]): string | null {
  if (!events.length) return null;
  return events[events.length - 1].event_type;
}

export function TaskDetail({ detail, onClose, onChange }: Props) {
  const { task, events } = detail;
  const [msg, setMsg] = useState("");
  const [feedback, setFeedback] = useState<{
    kind: "success" | "error";
    text: string;
  } | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    setFeedback(null);
  }, [task.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const lc = useMemo(
    () => lifecycleFor(task.status, !!task.pr_url),
    [task.status, task.pr_url]
  );

  const timeline: (InteractionEvent | FallbackEvent)[] =
    events.length > 0 ? events : buildFallbackTimeline(detail);

  const onSend = async () => {
    if (!msg.trim()) {
      setFeedback({ kind: "error", text: "Please enter a message." });
      return;
    }
    setSending(true);
    setFeedback(null);
    try {
      await api.send(task.id, msg.trim());
      setMsg("");
      setFeedback({ kind: "success", text: "Sent to Devin." });
      onChange();
    } catch (e) {
      setFeedback({ kind: "error", text: `Failed: ${(e as Error).message}` });
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <div className="detail-overlay" onClick={onClose} />
      <aside className="detail" role="dialog" aria-label="Task detail">
        <div className="detail-head">
          <div>
            <div className="detail-eyebrow muted small">
              {task.repo_full_name} • Task #{task.id}
            </div>
            <h2>
              #{task.issue_number} {task.issue_title || "(no title)"}
            </h2>
          </div>
          <button className="btn ghost" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="detail-section">
          <LifecycleIndicator
            steps={lc.steps}
            awaitingUser={lc.awaitingUser}
            size="md"
          />
          <div className="detail-status-line">
            <StatusBadge status={task.status} />
            <span className="muted small">
              · {latestInteractionLabel(task.status, lastEventType(events))}
            </span>
          </div>
        </div>

        <div className="detail-section">
          <h3 className="detail-section-title">Summary</h3>
          <dl>
            <dt>Repo</dt>
            <dd>{task.repo_full_name}</dd>
            <dt>Issue</dt>
            <dd>
              {task.issue_url ? (
                <a href={task.issue_url} target="_blank" rel="noopener noreferrer">
                  Open Issue ↗
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt>Requested by</dt>
            <dd>{task.requested_by ?? "—"}</dd>
            <dt>Trigger</dt>
            <dd>{triggerLabel(task.trigger_source)}</dd>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={task.status} />
            </dd>
            <dt>Devin session</dt>
            <dd>
              {task.devin_session_url ? (
                <a
                  href={task.devin_session_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open Devin ↗
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt>PR</dt>
            <dd>
              {task.pr_url ? (
                <a href={task.pr_url} target="_blank" rel="noopener noreferrer">
                  Open PR ↗
                </a>
              ) : (
                "—"
              )}
            </dd>
            {task.previous_pr_urls && task.previous_pr_urls.length > 0 && (
              <>
                <dt>Superseded PRs</dt>
                <dd>
                  <ul className="prev-pr-list">
                    {task.previous_pr_urls.map((u) => (
                      <li key={u}>
                        <a href={u} target="_blank" rel="noopener noreferrer">
                          PR {prShort(u)} ↗
                        </a>
                      </li>
                    ))}
                  </ul>
                  <div className="muted small">
                    Superseded by user-requested replans. Closed/merged at the
                    reviewer's discretion.
                  </div>
                </dd>
              </>
            )}
            <dt>Created at</dt>
            <dd>{fmtDate(task.created_at)}</dd>
            <dt>Updated at</dt>
            <dd>{fmtDate(task.updated_at)}</dd>
            <dt>Time to PR</dt>
            <dd>{fmtSecondsAsMinutes(task.time_to_pr_seconds)}</dd>
            <dt>Time to completion</dt>
            <dd>{fmtSecondsAsMinutes(task.time_to_completion_seconds)}</dd>
            {task.error && (
              <>
                <dt>Error</dt>
                <dd>
                  <pre className="error">{task.error}</pre>
                </dd>
              </>
            )}
          </dl>
        </div>

        <div className="detail-section timeline">
          <h3 className="detail-section-title">Interaction timeline</h3>
          {timeline.length === 0 ? (
            <div className="muted">No events yet.</div>
          ) : (
            timeline.map((e) => (
              <div key={e.id} className={`event source-${e.source}`}>
                <div className="meta">
                  <span className={`src-tag src-${e.source}`}>
                    {sourceTag(e.source)}
                  </span>
                  <span className="event-type">{e.event_type}</span>
                  <span className="muted small">{fmtDate(e.created_at)}</span>
                </div>
                {e.body && <pre className="event-body">{e.body}</pre>}
              </div>
            ))
          )}
          {events.length === 0 && (
            <div className="muted small fallback-note">
              Showing a derived timeline — no recorded events yet for this task.
            </div>
          )}
        </div>

        <div className="detail-section followup-box">
          <h3 className="detail-section-title">
            Send follow-up instruction to Devin
          </h3>
          <p className="muted small followup-help">
            The GitHub issue thread is the collaboration surface. A message sent
            here is forwarded into the same Devin session.
          </p>
          <textarea
            id="followup-msg"
            value={msg}
            onChange={(e) => setMsg(e.target.value)}
            placeholder="@devin update the PR to include tests for invalid filenames"
            disabled={sending}
          />
          <div className="actions">
            <button
              className="btn primary"
              disabled={sending || !msg.trim()}
              onClick={onSend}
            >
              {sending ? "Sending…" : "Send to Devin"}
            </button>
            {feedback && (
              <span
                className={`inline-toast ${feedback.kind}`}
                role={feedback.kind === "error" ? "alert" : "status"}
              >
                {feedback.text}
              </span>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
