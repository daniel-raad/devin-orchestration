import type { TaskStatus } from "../types";

export type StepState = "done" | "active" | "pending" | "failed";

export interface LifecycleStep {
  key: string;
  label: string;
  state: StepState;
}

export interface LifecycleView {
  steps: LifecycleStep[];
  awaitingUser: boolean;
}

const STEPS = [
  { key: "issue", label: "Issue" },
  { key: "plan", label: "Plan" },
  { key: "devin", label: "Devin" },
  { key: "pr", label: "PR" },
  { key: "review", label: "Review" },
  { key: "done", label: "Done" },
] as const;

const PR_INDEX = 3;
const FINAL_INDEX = STEPS.length - 1;

// Each status is described by where the task currently sits and the state of that step.
// Optional flags handle the genuine edge cases:
//   cascade        — failure propagates to every later step (else only the terminal "done" cell fails)
//   prIndependent  — "pr" step shows done if a PR exists, even when flow hasn't reached it
//   awaitingUser   — the indicator pulses to ask the human for input
interface StatusShape {
  step: number;
  state: StepState;
  cascade?: boolean;
  prIndependent?: boolean;
  awaitingUser?: boolean;
}

const SHAPES: Record<TaskStatus, StatusShape> = {
  pending:         { step: 0, state: "done" },
  planning:        { step: 1, state: "active" },
  plan_posted:     { step: 1, state: "done", awaitingUser: true },
  remediating:     { step: 2, state: "active" },
  awaiting_user:   { step: 2, state: "active", prIndependent: true, awaitingUser: true },
  pr_opened:       { step: 4, state: "active" },
  done:            { step: 5, state: "done" },
  closed_unmerged: { step: 4, state: "failed" },
  closed_unfixed:  { step: 2, state: "failed", cascade: true },
  failed:          { step: 2, state: "failed", prIndependent: true },
};

export function lifecycleFor(status: TaskStatus, hasPr: boolean): LifecycleView {
  const shape = SHAPES[status] ?? SHAPES.pending;
  const failed = shape.state === "failed";

  const states: StepState[] = STEPS.map((_, i) => {
    if (i < shape.step) return "done";
    if (i === shape.step) return shape.state;
    if (failed && (shape.cascade || i === FINAL_INDEX)) return "failed";
    return "pending";
  });

  if (shape.prIndependent && hasPr) states[PR_INDEX] = "done";

  const steps = STEPS.map((s, i) => ({ ...s, state: states[i] }));
  return { steps, awaitingUser: !!shape.awaitingUser };
}

const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "Pending",
  planning: "Planning",
  plan_posted: "Plan posted",
  remediating: "Remediating",
  awaiting_user: "Awaiting user",
  pr_opened: "Awaiting review",
  done: "Done",
  closed_unmerged: "Closed (unmerged)",
  closed_unfixed: "Closed without fix",
  failed: "Failed",
};

export function statusDisplayLabel(status: TaskStatus): string {
  return STATUS_LABELS[status] ?? status;
}

const EVENT_LABELS: Record<string, string> = {
  session_started: "Session started",
  followup_forwarded: "Follow-up forwarded",
  phase_transition: "Phase transition",
  plan_posted: "Plan posted",
  pr_opened: "PR opened",
  done: "Merged",
  completed: "Completed",
  failed: "Failed",
  closed_unmerged: "PR closed unmerged",
  closed_unfixed: "Closed without fix",
  clarification_requested: "Awaiting user clarification",
  user_instruction: "User instruction received",
  devin_response: "Devin responded",
  status_update: "Status update",
  error: "Error",
};

// Status-derived fallback when no recent event_type is known. Diverges intentionally from
// STATUS_LABELS — this describes what's *happening*, not the status name.
const STATUS_INTERACTION: Record<TaskStatus, string> = {
  pending: "Issue detected",
  planning: "Devin drafting plan",
  plan_posted: "Plan posted, awaiting user",
  remediating: "Devin remediating",
  awaiting_user: "Awaiting user clarification",
  pr_opened: "Awaiting review",
  done: "Done",
  closed_unmerged: "PR closed unmerged",
  closed_unfixed: "Closed without fix",
  failed: "Failed",
};

export function latestInteractionLabel(status: TaskStatus, lastEventType?: string | null): string {
  if (lastEventType && EVENT_LABELS[lastEventType]) return EVENT_LABELS[lastEventType];
  return STATUS_INTERACTION[status] ?? "—";
}

const TRIGGER_LABELS: Record<string, string> = {
  github_comment: "@devin comment",
  simulated: "Simulated comment",
  manual_ui: "Manual UI instruction",
};

export function triggerLabel(trigger?: string | null): string {
  return (trigger && TRIGGER_LABELS[trigger]) || "@devin comment";
}
