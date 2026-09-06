import { useCallback, useEffect, useRef, useState } from "react";
import type { Frame, Meta } from "./types";

/**
 * The telemetry transport.
 *
 * The newest frame lands in a ref, never in React state. At 20 Hz, setState
 * per frame would re-render the whole chrome twenty times a second to update
 * numbers a human reads a few times a second at most. The canvas reads the ref
 * in its own loop; the readout below samples it at 5 Hz, which is faster than
 * anyone can read and 4x cheaper than the stream.
 *
 * Only genuinely discrete things — connection state, the scenario list, the
 * tunable schema — go through state, because those actually change the tree.
 */

export type Status = "connecting" | "live" | "closed";

/** The handful of numbers the panel shows, sampled rather than streamed. */
export interface Readout {
  t: number;
  speed: number;
  behaviour: string;
  reason: string;
  target: number | null;
  clearance: number | null;
  risk: number | null;
  tracks: number;
  feasible: number;
  candidates: number;
  replan: number | null;
  marginRelief: number | null;
  safetyCap: number | null;
  paused: boolean;
  held: number[];
}

const EMPTY: Readout = {
  t: 0,
  speed: 0,
  behaviour: "—",
  reason: "",
  target: null,
  clearance: null,
  risk: null,
  tracks: 0,
  feasible: 0,
  candidates: 0,
  replan: null,
  marginRelief: null,
  safetyCap: null,
  paused: false,
  held: [],
};

const n = (v: unknown): number | null =>
  typeof v === "number" && isFinite(v) ? v : null;

export function useLive() {
  const frameRef = useRef<Frame | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [readout, setReadout] = useState<Readout>(EMPTY);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    // The page carries no host or port — it derives the socket from its own
    // origin, so it is byte-identical on :8420 and behind TLS on a domain.
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    socketRef.current = ws;

    ws.onopen = () => setStatus("live");
    ws.onclose = () => setStatus("closed");
    ws.onerror = () => setStatus("closed");
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data as string);
      if (msg.meta) {
        setMeta(msg.meta as Meta);
        return;
      }
      if (msg.grabbed !== undefined) return;
      frameRef.current = msg as Frame;
    };

    return () => ws.close();
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      const f = frameRef.current;
      if (!f) return;
      const d = f.debug || {};
      setReadout({
        t: f.t,
        speed: f.ego?.v ?? 0,
        behaviour: (d.behaviour as string) || "—",
        reason: (d.behaviour_reason as string) || "",
        target: n(d.target_speed),
        clearance: n(d.path_clearance),
        risk: n(d.plan_risk),
        tracks: (d.n_tracks as number) ?? 0,
        feasible: (d.n_feasible as number) ?? 0,
        candidates: (d.n_candidates as number) ?? 0,
        replan: n(d.replan_ms),
        marginRelief: n(d.margin_relief),
        safetyCap: n(d.safety_cap),
        paused: !!f.paused,
        held: f.held ?? [],
      });
    }, 200);
    return () => window.clearInterval(id);
  }, []);

  const send = useCallback((msg: Record<string, unknown>) => {
    const ws = socketRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }, []);

  return { frameRef, meta, status, readout, send };
}
