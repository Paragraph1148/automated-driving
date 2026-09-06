import { useEffect, useMemo, useState } from "react";
import { useLive } from "./lib/useLive";
import { LAYER_SPEC, type LayerKey, type Layers } from "./lib/types";
import Viewport from "./components/Viewport";
import Panel from "./components/Panel";
import Readout from "./components/Readout";
import Blocked from "./components/Blocked";
import Tour from "./components/Tour";

/**
 * The drop palette, exactly as the old viewer had it: class, label, POLICY and
 * speed. The policy is the half I dropped in the rebuild, and it is the half
 * that matters — "Wrong-way" and "Rash driver" are not classes, they are a
 * two-wheeler and a car given a different policy, so without it neither could
 * be placed at all and half the palette collapsed into duplicates.
 */
const PLACEABLE: { cls: string; label: string; policy: string; speed: number }[] = [
  { cls: "two_wheeler", label: "Two-wheeler", policy: "traffic", speed: 8.0 },
  { cls: "cattle", label: "Cow", policy: "cattle", speed: 0.0 },
  { cls: "auto_rickshaw", label: "Auto", policy: "traffic", speed: 6.0 },
  { cls: "pedestrian", label: "Pedestrian", policy: "traffic", speed: 1.1 },
  { cls: "two_wheeler", label: "Wrong-way", policy: "wrong_way", speed: 9.0 },
  { cls: "car", label: "Rash driver", policy: "rash", speed: 9.0 },
  { cls: "barricade", label: "Barricade", policy: "static", speed: 0.0 },
  { cls: "parked_vehicle", label: "Parked car", policy: "static", speed: 0.0 },
];

const TOUCH =
  window.matchMedia("(hover: none)").matches || (navigator.maxTouchPoints || 0) > 0;

/** On a phone the rail is a wall of controls; show one group at a time. */
const TABS = [
  { key: "status", label: "Status" },
  { key: "drop", label: "Drop" },
  { key: "layers", label: "Layers" },
] as const;
type Tab = (typeof TABS)[number]["key"];

const initialLayers = () =>
  Object.fromEntries(LAYER_SPEC.map((l) => [l.key, l.on])) as Layers;

export default function App() {
  const { frameRef, meta, status, readout, send } = useLive();
  const [layers, setLayers] = useState<Layers>(initialLayers);
  const [follow, setFollow] = useState(true);
  const [brushIdx, setBrushIdx] = useState(0);
  const [erase, setErase] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("status");
  const [view, setView] = useState({ zoom: 1, moved: false });
  const [tourOpen, setTourOpen] = useState(false);

  const scene = useMemo(() => meta?.scene ?? {}, [meta]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.code === "Space") {
        e.preventDefault();
        send({ cmd: "pause", value: !readout.paused });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [send, readout.paused]);

  return (
    <div className="app">
      <header className="bar">
        <div className="bar__id">
          <span className="bar__mark">SARATHI</span>
          <span className="bar__kana">サラティ</span>
          <button
            className="key btn btn--tiny"
            onClick={() => setTourOpen(true)}
            title="What can this page do?"
          >
            Guide
          </button>
        </div>

        <label className="field" data-tour="scenario">
          <span className="field__label">Scenario</span>
          <select
            className="key select"
            value={meta?.scenario ?? ""}
            onChange={(e) => send({ cmd: "load", scenario: e.target.value })}
          >
            {(meta?.scenarios ?? []).map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>

        <div className="bar__spacer" />

        <button
          className={`key btn ${readout.paused ? "is-accent" : ""}`}
          onClick={() => send({ cmd: "pause", value: !readout.paused })}
        >
          {readout.paused ? "Resume" : "Pause"}
        </button>
        <button
          className={`key btn ${follow ? "is-on" : ""}`}
          onClick={() => setFollow((f) => !f)}
          aria-pressed={follow}
        >
          Follow
        </button>
        <button
          className="key btn"
          data-tour="thresholds"
          onClick={() => setPanelOpen((o) => !o)}
        >
          Thresholds
        </button>

        <span className="clock">{readout.t.toFixed(1)} s</span>
        <span className={`status status--${status}`}>
          <i />
          {status === "live" && readout.viewers > 1
            ? `live · ${readout.viewers} watching`
            : status}
        </span>
      </header>

      <main className="stage">
        <Viewport
          frameRef={frameRef}
          scene={scene}
          layers={layers}
          follow={follow}
          erase={erase}
          brush={PLACEABLE[brushIdx]}
          onCommand={send}
          onViewChange={(zoom, moved) => setView({ zoom, moved })}
          overlay={<Blocked r={readout} touch={TOUCH} />}
        />

        <aside className="rail" data-tab={tab}>
          <section className="rail__block" data-for="status">
            <Readout r={readout} />
          </section>

          <section className="rail__block" data-for="drop">
            <h2 className="rail__title">Drop</h2>
            <ul className="palette">
              {PLACEABLE.map((p, i) => (
                <li key={p.label}>
                  <button
                    className={`key palette__key ${brushIdx === i && !erase ? "is-on" : ""}`}
                    onClick={() => {
                      setBrushIdx(i);
                      setErase(false);
                    }}
                    aria-pressed={brushIdx === i && !erase}
                  >
                    <i className="palette__dot" data-cls={p.cls} />
                    {p.label}
                  </button>
                </li>
              ))}
            </ul>
            {/* No phone has a shift key, so removal needs its own control —
                and a long press on a body does it too. */}
            <div className="rowbtns">
              <button
                className={`key btn ${erase ? "is-accent" : ""}`}
                onClick={() => setErase((v) => !v)}
                aria-pressed={erase}
              >
                {erase ? "Erasing" : "Erase"}
              </button>
              <button className="key btn" onClick={() => send({ cmd: "restart_ego" })}>
                Reset vehicle
              </button>
              <button
                className={`key btn ${readout.paused ? "is-accent" : ""}`}
                onClick={() => send({ cmd: "pause", value: !readout.paused })}
              >
                {readout.paused ? "Resume" : "Pause"}
              </button>
            </div>
            <p className="rail__hint">
              Tap road to drop · drag to move · hold to remove · pinch to zoom
            </p>
          </section>

          <section className="rail__block" data-for="layers">
            <h2 className="rail__title">Layers</h2>
            <ul className="layers">
              {LAYER_SPEC.map((l) => (
                <li key={l.key}>
                  <label className="layer">
                    <input
                      type="checkbox"
                      checked={layers[l.key]}
                      onChange={(e) =>
                        setLayers((p) => ({ ...p, [l.key as LayerKey]: e.target.checked }))
                      }
                    />
                    <i className="layer__swatch" style={{ background: `var(${l.swatch})` }} />
                    <span>{l.label}</span>
                  </label>
                </li>
              ))}
            </ul>
            <h2 className="rail__title rail__title--spaced">Road users</h2>
            <ul className="legend">
              <li><i style={{ background: "var(--ego)", outline: "1.5px solid var(--ego-ring)" }} />Ego vehicle</li>
              <li><i style={{ background: "var(--veh)" }} />Vehicles — car, bus, truck, auto</li>
              <li><i style={{ background: "var(--vru)" }} />Vulnerable — two-wheeler, cycle, pedestrian, cart</li>
              <li><i style={{ background: "var(--animal)" }} />Animals — cattle, stray dogs</li>
              <li><i style={{ background: "var(--furniture)" }} />Static — barricades, parked</li>
            </ul>
          </section>
        </aside>
      </main>

      <footer className="chips">
        <span className="chip">
          <b>tracked</b> {readout.tracks}
        </span>
        <span className="chip">
          <b>feasible</b> {readout.feasible}/{readout.candidates}
        </span>
        <span className="chip">
          <b>margin relief</b>{" "}
          {readout.marginRelief === null ? "—" : readout.marginRelief.toFixed(2)} m
        </span>
        <span className="chip">
          <b>safety cap</b>{" "}
          {readout.safetyCap === null ? "—" : readout.safetyCap.toFixed(1)} m/s
        </span>
        <span className="chip">
          <b>replan</b> {readout.replan === null ? "—" : readout.replan.toFixed(1)} ms
        </span>
        {readout.lastEvent && (
          <span className="chip chip--event">
            <b>last event</b> {readout.lastEvent.kind.replace(/_/g, " ")} ·{" "}
            {readout.lastEvent.detail} @ {readout.lastEvent.t}s
          </span>
        )}
        {view.moved && (
          <span className="chip chip--view">
            <b>zoom</b> {view.zoom.toFixed(1)}×
          </span>
        )}
      </footer>

      <nav className="tabs" aria-label="Panels">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={tab === t.key ? "is-on" : ""}
            aria-pressed={tab === t.key}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
        <button onClick={() => setPanelOpen(true)}>Tune</button>
      </nav>

      <Tour
        open={tourOpen}
        onOpen={() => setTourOpen(true)}
        onClose={() => setTourOpen(false)}
        onTab={setTab}
        onPanel={setPanelOpen}
      />

      {panelOpen && meta && (
        <Panel
          tunables={meta.tunables}
          values={meta.values}
          onSet={(key, value) => send({ cmd: "set", key, value })}
          onReset={() => send({ cmd: "reset_tuning" })}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </div>
  );
}
