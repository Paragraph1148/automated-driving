/** The telemetry contract, exactly as `sarathi/sim/snapshot.py` emits it. */

export type XY = [number, number];

export interface Body {
  id: number;
  cls: string;
  x: number;
  y: number;
  h: number;
  v: number;
  l: number;
  w: number;
  /** Only present, and only for animals that have settled in the road. */
  rest?: 1;
}

export interface Candidate {
  p: XY[];
  ok: boolean;
  c: number | null;
}

export interface PredictionMode {
  m: string;
  p: number;
  xy: XY[];
  s: number[];
}

export interface Cone {
  id: number;
  cls: string;
  modes: PredictionMode[];
}

export interface Track {
  id: number;
  cls: string;
  x: number;
  y: number;
  h: number;
  v: number;
  conf: number;
  sig: number;
}

export interface RiskGrid {
  nx: number;
  ny: number;
  res: number;
  x0: number;
  y0: number;
  /** base64 of one byte per cell */
  data: string;
}

export interface Debug {
  behaviour?: string;
  behaviour_reason?: string;
  target_speed?: number;
  path_clearance?: number;
  plan_risk?: number;
  n_tracks?: number;
  n_feasible?: number;
  n_candidates?: number;
  margin_relief?: number;
  safety_cap?: number;
  replan_ms?: number;
  min_ttc?: number;
  blocked_at?: XY;
  blocked_for?: number;
  blocked_cls?: string;
  reverse_left?: number;
  reverse_room?: number;
  [k: string]: unknown;
}

export interface Frame {
  t: number;
  ego: Body;
  agents: Body[];
  debug: Debug;
  plan?: XY[];
  plan_v?: number[];
  fan?: Candidate[];
  cones?: Cone[];
  tracks?: Track[];
  reference?: XY[];
  risk?: RiskGrid;
  /** {t, kind, detail} — simulator.py appends these; the last one is the
   *  most useful thing on screen when something has just gone wrong. */
  events?: { t: number; kind: string; detail: string }[];
  held?: number[];
  paused?: boolean;
  /** How many browsers are watching this one shared world. */
  viewers?: number;
}

export interface Scene {
  length?: number;
  left_edge?: XY[];
  right_edge?: XY[];
  centreline?: XY[];
  lane_marking_quality?: number;
  defects?: { x: number; y: number; r: number; severity: number }[];
}

/** Exactly what `sarathi/tuning.py::schema()` emits. */
export interface Tunable {
  key: string;
  label: string;
  section: string;
  kind: string;
  lo: number;
  hi: number;
  step: number;
  help?: string;
}

export interface Meta {
  mode: string;
  scenario: string;
  chaos: number;
  scene: Scene;
  scenarios: string[];
  tunables: Tunable[];
  /** Floats for the sliders, real booleans for the five ablations. */
  values: Record<string, number | boolean>;
}

export type LayerKey =
  | "risk"
  | "cones"
  | "fan"
  | "plan"
  | "ref"
  | "tracks"
  | "truth";

export type Layers = Record<LayerKey, boolean>;

export const LAYER_SPEC: {
  key: LayerKey;
  label: string;
  on: boolean;
  swatch: string;
}[] = [
  { key: "risk", label: "Risk field", on: true, swatch: "--risk-hue" },
  { key: "cones", label: "Prediction cones", on: true, swatch: "--animal" },
  { key: "fan", label: "Candidate fan", on: true, swatch: "--ink-3" },
  { key: "plan", label: "Chosen trajectory", on: true, swatch: "--accent" },
  { key: "ref", label: "Derived reference", on: true, swatch: "--ink-3" },
  { key: "tracks", label: "Perceived tracks", on: false, swatch: "--vru" },
  { key: "truth", label: "Ground truth", on: true, swatch: "--veh" },
];

const VEHICLE = new Set(["car", "bus", "truck", "auto_rickshaw", "parked_vehicle"]);
const VRU = new Set(["two_wheeler", "bicycle", "pedestrian", "pushcart"]);
const ANIMAL = new Set(["cattle", "stray_dog"]);

export function classVar(cls: string): string {
  if (VEHICLE.has(cls)) return "--veh";
  if (VRU.has(cls)) return "--vru";
  if (ANIMAL.has(cls)) return "--animal";
  return "--furniture";
}
