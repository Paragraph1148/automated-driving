import type { Readout as R } from "../lib/useLive";

const STATE_TONE: Record<string, string> = {
  cruise: "--ok", follow: "--ok", overtake: "--ok",
  nudge: "--warn", creep: "--warn", yield: "--warn", reverse: "--warn",
  wrong_way_evade: "--crit", emergency_stop: "--crit",
};

const num = (v: unknown, d = 1) =>
  typeof v === "number" && isFinite(v) ? v.toFixed(d) : "—";

/** Severity of a tile, so the number that matters is findable at a glance. */
const band = (v: number | null, alert: number, warn: number) =>
  v == null ? "" : v < alert ? " is-alert" : v < warn ? " is-warn" : "";

export default function Readout({ r }: { r: R }) {
  const tone = STATE_TONE[r.behaviour] ?? "--ink-3";
  // Beyond ~90 s the supervisor is not tracking anything meaningful.
  const ttcText =
    r.minTtc == null || !isFinite(r.minTtc) || r.minTtc > 90 ? "∞" : num(r.minTtc, 1);

  return (
    <>
      <h2 className="rail__title">Behaviour</h2>
      <div className="cap state" style={{ ["--st" as string]: `var(${tone})` }}>
        <div className="state__name">{r.behaviour.replace(/_/g, " ")}</div>
        {r.reason && <div className="state__why">{r.reason}</div>}
      </div>

      <h2 className="rail__title rail__title--spaced">Telemetry</h2>
      <dl className="metrics">
        <div><dt>Speed</dt><dd>{num(r.speed)}<u>m/s</u></dd></div>
        <div><dt>Target</dt><dd>{num(r.target)}<u>m/s</u></dd></div>
        <div className={`tile${band(r.minTtc, 2, 4)}`}>
          <dt>Min TTC</dt><dd>{ttcText}<u>s</u></dd>
        </div>
        <div className={`tile${band(r.clearance, 0.3, 0.7)}`}>
          <dt>Path clearance</dt><dd>{num(r.clearance, 2)}<u>m</u></dd>
        </div>
        <div><dt>Replan</dt><dd>{num(r.replan, 1)}<u>ms</u></dd></div>
        <div><dt>Plan risk</dt><dd>{num(r.risk, 2)}</dd></div>
      </dl>
    </>
  );
}
