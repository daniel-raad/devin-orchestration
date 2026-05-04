const NODES = [
  { label: "GitHub Issue Comment", role: "github" },
  { label: "Orchestrator", role: "orchestrator" },
  { label: "Devin Session", role: "devin" },
  { label: "Pull Request", role: "github" },
  { label: "Dashboard", role: "dashboard" },
];

export function ArchitectureStrip() {
  return (
    <section className="arch-strip" aria-label="System architecture overview">
      {NODES.map((n, i) => (
        <span key={n.label} className="arch-node-wrap">
          <span className={`arch-node role-${n.role}`}>{n.label}</span>
          {i < NODES.length - 1 && <span className="arch-arrow">→</span>}
        </span>
      ))}
    </section>
  );
}
