import { useCallback, useEffect, useState } from "react";
import { ArchitectureStrip } from "./components/ArchitectureStrip";
import { MetricsCards } from "./components/MetricsCards";
import { SystemHealth } from "./components/SystemHealth";
import { TasksTable } from "./components/TasksTable";
import { TaskDetail } from "./components/TaskDetail";
import { SimulatePanel } from "./components/SimulatePanel";
import { api } from "./lib/api";
import { fmtDate } from "./lib/format";
import type { Health, Metrics, Task, TaskDetail as TaskDetailType } from "./types";

const REFRESH_INTERVAL_MS = 15000;

export default function App() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [detail, setDetail] = useState<TaskDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [hasLoadedOnce, setHasLoadedOnce] = useState<boolean>(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [showDevTools, setShowDevTools] = useState<boolean>(false);

  const refreshAll = useCallback(async () => {
    try {
      const [m, t, h] = await Promise.all([
        api.metrics(),
        api.tasks(),
        api.health(),
      ]);
      setMetrics(m);
      setTasks(t);
      setHealth(h);
      setError(null);
      setHasLoadedOnce(true);
      setLastRefresh(new Date());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAll();
    const id = setInterval(refreshAll, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  const onView = useCallback(async (id: number) => {
    try {
      const d = await api.task(id);
      setDetail(d);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const onRowRefresh = useCallback(
    async (id: number) => {
      try {
        await api.refresh(id);
        await refreshAll();
        if (detail && detail.task.id === id) {
          const d = await api.task(id);
          setDetail(d);
        }
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [refreshAll, detail]
  );

  const onDetailChanged = useCallback(async () => {
    if (!detail) return;
    const d = await api.task(detail.task.id);
    setDetail(d);
    refreshAll();
  }, [detail, refreshAll]);

  const onSimulateView = useCallback(
    async (id: number) => {
      await onView(id);
      setShowDevTools(false);
    },
    [onView]
  );

  const showFatalError = !!error && !hasLoadedOnce;

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <h1>Devin Vulnerability Remediation Dashboard</h1>
          <p className="subtitle">
            GitHub is the collaboration layer · Orchestrator manages Devin
            sessions · Devin opens PRs · Humans review and merge.
          </p>
        </div>
        <div className="actions">
          <span className="muted small last-refresh">
            {lastRefresh ? `Updated ${fmtDate(lastRefresh.toISOString())}` : ""}
          </span>
          <button className="btn ghost" onClick={refreshAll}>
            Refresh
          </button>
          <button
            className={`btn ${showDevTools ? "primary" : "ghost"}`}
            onClick={() => setShowDevTools((v) => !v)}
            aria-expanded={showDevTools}
          >
            {showDevTools ? "Hide tools" : "Demo & diagnostics"}
          </button>
        </div>
      </header>

      <main>
        <ArchitectureStrip />

        {error && hasLoadedOnce && (
          <div className="error-banner" role="alert">
            <strong>API error:</strong> {error}
          </div>
        )}

        {loading && !hasLoadedOnce ? (
          <div className="state-block loading">
            Loading remediation dashboard…
          </div>
        ) : showFatalError ? (
          <div className="state-block error" role="alert">
            <strong>Unable to load dashboard data.</strong>
            <div className="muted small">
              Check that the backend is running on{" "}
              <code>http://localhost:8000</code>.
            </div>
            <div className="muted small fail-detail">{error}</div>
            <button className="btn" onClick={refreshAll} style={{ marginTop: 12 }}>
              Try again
            </button>
          </div>
        ) : (
          <>
            <MetricsCards metrics={metrics} />

            {showDevTools && (
              <section className="dev-tools" aria-label="Demo and diagnostics">
                <div className="dev-tools-head">
                  <h2>Demo &amp; diagnostics</h2>
                  <p className="muted small">
                    Simulate a workflow without a real GitHub webhook, and check
                    backend connectivity.
                  </p>
                </div>
                <div className="two-col">
                  <SystemHealth
                    health={health}
                    dataLoaded={hasLoadedOnce}
                    lastRefresh={lastRefresh}
                  />
                  <SimulatePanel onResult={refreshAll} onViewTask={onSimulateView} />
                </div>
              </section>
            )}

            <section className="panel">
              <div className="panel-head">
                <h2>Remediation tasks</h2>
                <span className="muted small">{tasks.length} total</span>
              </div>
              <TasksTable
                tasks={tasks}
                onRefresh={onRowRefresh}
                onView={onView}
              />
            </section>
          </>
        )}
      </main>

      {detail && (
        <TaskDetail
          detail={detail}
          onClose={() => setDetail(null)}
          onChange={onDetailChanged}
        />
      )}
    </>
  );
}
