import type { LifecycleStep } from "../lib/lifecycle";

interface Props {
  steps: LifecycleStep[];
  awaitingUser?: boolean;
  size?: "sm" | "md";
}

const SYMBOL: Record<LifecycleStep["state"], string> = {
  done: "✓",
  active: "●",
  pending: "○",
  failed: "✕",
};

export function LifecycleIndicator({ steps, awaitingUser, size = "sm" }: Props) {
  return (
    <div className={`lifecycle ${size}`} aria-label="lifecycle">
      {steps.map((step, i) => (
        <span key={step.key} className="lifecycle-step-wrap">
          <span className={`lifecycle-step state-${step.state}`} title={`${step.label}: ${step.state}`}>
            <span className="lifecycle-symbol">{SYMBOL[step.state]}</span>
            <span className="lifecycle-label">{step.label}</span>
          </span>
          {i < steps.length - 1 && <span className="lifecycle-arrow">→</span>}
        </span>
      ))}
      {awaitingUser && <span className="lifecycle-flag">Awaiting user</span>}
    </div>
  );
}
