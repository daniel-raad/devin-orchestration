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

const STEP_KEYS = ["issue", "plan", "devin", "pr", "review", "done"] as const;
const STEP_LABELS: Record<(typeof STEP_KEYS)[number], string> = {
  issue: "Issue",
  plan: "Plan",
  devin: "Devin",
  pr: "PR",
  review: "Review",
  done: "Done",
};

function build(states: Record<(typeof STEP_KEYS)[number], StepState>): LifecycleStep[] {
  return STEP_KEYS.map((k) => ({ key: k, label: STEP_LABELS[k], state: states[k] }));
}

export function lifecycleFor(status: TaskStatus, hasPr: boolean): LifecycleView {
  switch (status) {
    case "pending":
      return {
        steps: build({
          issue: "done",
          plan: "pending",
          devin: "pending",
          pr: "pending",
          review: "pending",
          done: "pending",
        }),
        awaitingUser: false,
      };
    case "planning":
      return {
        steps: build({
          issue: "done",
          plan: "active",
          devin: "pending",
          pr: "pending",
          review: "pending",
          done: "pending",
        }),
        awaitingUser: false,
      };
    case "plan_posted":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "pending",
          pr: "pending",
          review: "pending",
          done: "pending",
        }),
        awaitingUser: true,
      };
    case "remediating":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "active",
          pr: "pending",
          review: "pending",
          done: "pending",
        }),
        awaitingUser: false,
      };
    case "awaiting_user":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "active",
          pr: hasPr ? "done" : "pending",
          review: "pending",
          done: "pending",
        }),
        awaitingUser: true,
      };
    case "pr_opened":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "done",
          pr: "done",
          review: "active",
          done: "pending",
        }),
        awaitingUser: false,
      };
    case "done":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "done",
          pr: "done",
          review: "done",
          done: "done",
        }),
        awaitingUser: false,
      };
    case "closed_unmerged":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "done",
          pr: "done",
          review: "failed",
          done: "failed",
        }),
        awaitingUser: false,
      };
    case "closed_unfixed":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "failed",
          pr: "failed",
          review: "failed",
          done: "failed",
        }),
        awaitingUser: false,
      };
    case "failed":
      return {
        steps: build({
          issue: "done",
          plan: "done",
          devin: "failed",
          pr: hasPr ? "done" : "pending",
          review: "pending",
          done: "failed",
        }),
        awaitingUser: false,
      };
    default:
      return {
        steps: build({
          issue: "done",
          plan: "pending",
          devin: "pending",
          pr: "pending",
          review: "pending",
          done: "pending",
        }),
        awaitingUser: false,
      };
  }
}

export function statusDisplayLabel(status: TaskStatus): string {
  switch (status) {
    case "pending":
      return "Pending";
    case "planning":
      return "Planning";
    case "plan_posted":
      return "Plan posted";
    case "remediating":
      return "Remediating";
    case "awaiting_user":
      return "Awaiting user";
    case "pr_opened":
      return "Awaiting review";
    case "done":
      return "Done";
    case "closed_unmerged":
      return "Closed (unmerged)";
    case "closed_unfixed":
      return "Closed without fix";
    case "failed":
      return "Failed";
    default:
      return status;
  }
}

export function latestInteractionLabel(status: TaskStatus, lastEventType?: string | null): string {
  if (lastEventType) {
    switch (lastEventType) {
      case "session_started":
        return "Session started";
      case "followup_forwarded":
        return "Follow-up forwarded";
      case "phase_transition":
        return "Phase transition";
      case "plan_posted":
        return "Plan posted";
      case "pr_opened":
        return "PR opened";
      case "done":
        return "Merged";
      case "completed":
        return "Completed";
      case "failed":
        return "Failed";
      case "closed_unmerged":
        return "PR closed unmerged";
      case "closed_unfixed":
        return "Closed without fix";
      case "clarification_requested":
        return "Awaiting user clarification";
      case "user_instruction":
        return "User instruction received";
      case "devin_response":
        return "Devin responded";
      case "status_update":
        return "Status update";
      case "error":
        return "Error";
      default:
        // Fall through and derive from status.
        break;
    }
  }
  switch (status) {
    case "pending":
      return "Issue detected";
    case "planning":
      return "Devin drafting plan";
    case "plan_posted":
      return "Plan posted, awaiting user";
    case "remediating":
      return "Devin remediating";
    case "awaiting_user":
      return "Awaiting user clarification";
    case "pr_opened":
      return "Awaiting review";
    case "done":
      return "Done";
    case "closed_unmerged":
      return "PR closed unmerged";
    case "closed_unfixed":
      return "Closed without fix";
    case "failed":
      return "Failed";
    default:
      return "—";
  }
}

export function triggerLabel(trigger?: string | null): string {
  switch (trigger) {
    case "github_comment":
      return "@devin comment";
    case "simulated":
      return "Simulated comment";
    case "manual_ui":
      return "Manual UI instruction";
    default:
      return "@devin comment";
  }
}

