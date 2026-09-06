import type { Readout as R } from "../lib/useLive";

const STATE_TONE: Record<string, string> = {
  cruise: "--ok", follow: "--ok", overtake: "--ok",
  nudge: "--warn", creep: "--warn", yield: "--warn", reverse: "--warn",
  wrong_way_evade: "--crit", emergency_stop: "--crit",
};

const num = (v: unknown, d = 1) =>
  typeof v === "number" && isFinite(v) ? v.toFixed(d) : "\u2014";

export default function Readout({ r }: { r: R }) {
  const tone = STATE_TONE[r.behaviour] ?? "--ink-3";
  return (
    <section className="rail__block">
      <h2 className="rail__title">State</h2>

      <div className="cap state" style={{ ["--st" as string]: `var(${tone})` }}>
        <div className="state__name">{r.behaviour.replace(/_/g, " ")}</div>
        {r.reason && <div className="state__why">{r.reason}</div>}
      </div>

      <dl className="metrics">
        <div><dt>Speed</dt><dd>{num(r.speed)}<u>m/s</u></dd></div>
        <div><dt>Target</dt><dd>{num(r.target)}<u>m/s</u></dd></div>
        <div><dt>Clearance</dt><dd>{num(r.clearance, 2)}<u>m</u></dd></div>
        <div><dt>Plan risk</dt><dd>{num(r.risk, 2)}</dd></div>
      </dl>
    </section>
  );
}
