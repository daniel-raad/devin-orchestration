import type { Health } from "../types";
import { fmtDate } from "../lib/format";

interface Props {
  health: Health | null;
  dataLoaded: boolean;
  lastRefresh: Date | null;
}

interface Item {
  label: string;
  status: "ok" | "warn" | "unknown";
  text: string;
}

function buildItems(health: Health | null, dataLoaded: boolean): Item[] {
  const webhook: Item = {
    label: "Webhook endpoint",
    status: "ok",
    text: "Ready",
  };

  const database: Item = dataLoaded
    ? { label: "Database", status: "ok", text: "Connected" }
    : { label: "Database", status: "unknown", text: "Unknown" };

  const devin: Item = health
    ? health.devin_configured
      ? { label: "Devin API", status: "ok", text: "Configured" }
      : { label: "Devin API", status: "warn", text: "Missing API key" }
    : { label: "Devin API", status: "unknown", text: "Unknown" };

  const github: Item = health
    ? health.github_configured
      ? { label: "GitHub API", status: "ok", text: "Configured" }
      : { label: "GitHub API", status: "warn", text: "Missing token" }
    : { label: "GitHub API", status: "unknown", text: "Unknown" };

  return [webhook, devin, github, database];
}

export function SystemHealth({ health, dataLoaded, lastRefresh }: Props) {
  const items = buildItems(health, dataLoaded);
  return (
    <section className="panel system-health">
      <div className="panel-head">
        <h2>System health</h2>
        <span className="muted small">
          Last refresh: {lastRefresh ? fmtDate(lastRefresh.toISOString()) : "—"}
        </span>
      </div>
      <ul className="health-list">
        {items.map((it) => (
          <li key={it.label} className={`health-item status-${it.status}`}>
            <span className="health-dot" aria-hidden="true" />
            <span className="health-label">{it.label}</span>
            <span className="health-text">{it.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
