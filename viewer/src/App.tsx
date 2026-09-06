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

const initialLayers = () =>
  Object.fromEntries(LAYER_SPEC.map((l) => [l.key, l.on])) as Layers;

export default function App() {
  const { frameRef, meta, status, readout, send } = useLive();
  const [layers, setLayers] = useState<Layers>(initialLayers);
  const [follow, setFollow] = useState(true);
  const [brush, setBrush] = useState("car");
  const [panelOpen, setPanelOpen] = useState(false);

  const scene = useMemo(() => meta?.scene ?? {}, [meta]);

  // Space toggles the world, which is the control you reach for most while
  // watching something go wrong.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" || (e.target as HTMLElement).tagName === "INPUT") return;
      e.preventDefault();
      send({ cmd: "pause", value: !readout.paused });
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

        <label className="field">
          <span className="field__label">Drop</span>
          <select
            className="key select"
            value={brush}
            onChange={(e) => setBrush(e.target.value)}
          >
            {PLACEABLE.map((p) => (
              <option key={p.cls} value={p.cls}>
                {p.label}
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
          onPick={([x, y]) => send({ cmd: "place", cls: brush, x, y })}
          onDrag={(id, [x, y]) => send({ cmd: "drag", id, x, y })}
          onRemove={([x, y]) => send({ cmd: "remove", x, y })}
        />

        <aside className="rail">
          <section className="rail__block">
            <h2 className="rail__title">Layers</h2>
            <ul className="layers">
              {LAYER_SPEC.map((l) => (
                <li key={l.key}>
                  <label className="layer">
                    <input
                      type="checkbox"
                      checked={layers[l.key]}
                      onChange={(e) =>
                        setLayers((p) => ({
                          ...p,
                          [l.key as LayerKey]: e.target.checked,
                        }))
                      }
                    />
                    <i className="layer__swatch" style={{ background: `var(${l.swatch})` }} />
                    <span>{l.label}</span>
                  </label>
                </li>
              ))}
            </ul>
          </section>

          <Readout r={readout} />
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
        <span className="chips__hint">
          Click to drop · drag to move · shift-click to remove · space to pause
        </span>
      </footer>

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
