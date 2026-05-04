export type TaskStatus =
  | "pending"
  | "planning"
  | "plan_posted"
  | "remediating"
  | "awaiting_user"
  | "pr_opened"
  | "done"
  | "closed_unmerged"
  | "closed_unfixed"
  | "failed";

export interface Task {
  id: number;
  repo_full_name: string;
  issue_number: number;
  issue_title: string;
  issue_url: string;
  status: TaskStatus;
  devin_session_id: string | null;
  devin_session_url: string | null;
  pr_url: string | null;
  requested_by: string | null;
  created_at: string;
  updated_at: string;
  last_github_comment_id: number | null;
  last_devin_update_at: string | null;
  time_to_pr_seconds: number | null;
  time_to_completion_seconds: number | null;
  error: string | null;
  trigger_source?: string | null;
  last_event_type?: string | null;
  last_event_source?: string | null;
  last_event_at?: string | null;
  previous_pr_urls?: string[];
}

export type EventSource = "github" | "devin" | "orchestrator";

export interface InteractionEvent {
  id: number;
  task_id: number;
  source: EventSource;
  event_type: string;
  github_comment_id: number | null;
  github_comment_url: string | null;
  body: string | null;
  created_at: string;
}

export interface TaskDetail {
  task: Task;
  events: InteractionEvent[];
}

export interface Metrics {
  total_devin_mentions: number;
  total_sessions: number;
  active_sessions: number;
  awaiting_user: number;
  awaiting_review?: number;
  prs_opened: number;
  completed_tasks: number;
  done_no_change?: number;
  failed_tasks: number;
  closed_without_fix?: number;
  followups_forwarded?: number;
  unique_issues?: number;
  unique_requesters?: number;
  average_time_to_pr_minutes: number | null;
  average_time_to_completion_minutes: number | null;
}

export interface Health {
  webhook_ready: boolean;
  devin_configured: boolean;
  github_configured: boolean;
  database_ok: boolean;
}

export interface SimulatePayload {
  repo_full_name: string;
  issue_number: number;
  issue_title: string;
  issue_url: string;
  issue_body: string;
  comment_body: string;
  comment_author: string;
}

export interface SimulateResult {
  action: string;
  task_id?: number;
  reason?: string;
  session_url?: string;
  error?: string;
}
