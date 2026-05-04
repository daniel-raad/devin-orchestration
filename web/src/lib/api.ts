import type {
  Health,
  Metrics,
  SimulatePayload,
  SimulateResult,
  Task,
  TaskDetail,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

async function tryRequest<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    return await request<T>(path, init);
  } catch {
    return null;
  }
}

export const api = {
  metrics: () => request<Metrics>("/api/metrics"),
  tasks: () => request<Task[]>("/api/tasks"),
  task: (id: number) => request<TaskDetail>(`/api/tasks/${id}`),
  health: () => tryRequest<Health>("/api/health"),
  refresh: (id: number) =>
    request<TaskDetail>(`/api/tasks/${id}/refresh`, { method: "POST" }),
  send: (id: number, message: string) =>
    request<{ ok: boolean }>(`/api/tasks/${id}/send`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  simulate: (payload: SimulatePayload) =>
    request<SimulateResult>("/api/simulate-comment", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
