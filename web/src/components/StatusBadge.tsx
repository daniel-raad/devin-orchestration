import type { TaskStatus } from "../types";
import { statusDisplayLabel } from "../lib/lifecycle";

export function StatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`badge ${status}`}>{statusDisplayLabel(status)}</span>;
}
