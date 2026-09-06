import { useEffect, useMemo, useState } from "react";
import { useLive } from "./lib/useLive";
import { LAYER_SPEC, type LayerKey, type Layers } from "./lib/types";
import Viewport from "./components/Viewport";
import Panel from "./components/Panel";
import Readout from "./components/Readout";

const PLACEABLE = [
  { cls: "car", label: "Car" },
  { cls: "bus", label: "Bus" },
  { cls: "auto_rickshaw", label: "Auto" },
  { cls: "two_wheeler", label: "Two-wheeler" },
  { cls: "pedestrian", label: "Pedestrian" },
  { cls: "cattle", label: "Cattle" },
  { cls: "barricade", label: "Barricade" },
];

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
  const [brush, setBrush] = useState("car");
  const [erase, setErase] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("status");
  const [view, setView] = useState({ zoom: 1, moved: false });

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
        </div>

        <label className="field">
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
        <button className="key btn" onClick={() => setPanelOpen((o) => !o)}>
          Thresholds
        </button>

        <span className={`status status--${status}`}>
          <i />
          {status}
        </span>
      </header>

      <main className="stage">
        <Viewport
          frameRef={frameRef}
          scene={scene}
          layers={layers}
          follow={follow}
          erase={erase}
          brush={brush}
          onCommand={send}
          onViewChange={(zoom, moved) => setView({ zoom, moved })}
        />

        <aside className="rail" data-tab={tab}>
          <section className="rail__block" data-for="status">
            <Readout r={readout} />
          </section>

          <section className="rail__block" data-for="drop">
            <h2 className="rail__title">Drop</h2>
            <ul className="palette">
              {PLACEABLE.map((p) => (
                <li key={p.cls}>
                  <button
                    className={`key palette__key ${brush === p.cls && !erase ? "is-on" : ""}`}
                    onClick={() => {
                      setBrush(p.cls);
                      setErase(false);
                    }}
                    aria-pressed={brush === p.cls && !erase}
                  >
                    {p.label}
                  </button>
                </li>
              ))}
            </ul>
            {/* No phone has a shift key, so removal needs its own control —
                and a long press on a body does it too. */}
            <button
              className={`key btn erase ${erase ? "is-accent" : ""}`}
              onClick={() => setErase((v) => !v)}
              aria-pressed={erase}
            >
              {erase ? "Erasing — tap a road user" : "Erase"}
            </button>
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
