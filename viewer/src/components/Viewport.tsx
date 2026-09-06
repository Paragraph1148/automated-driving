import { useEffect, useRef } from "react";
import { Renderer, ZOOM_MAX, ZOOM_MIN } from "../render/scene";
import type { Frame, Layers, Scene } from "../lib/types";

/**
 * The canvas host, and every pointer gesture that acts on the world.
 *
 * Ported from the pre-rewrite viewer rather than reinvented. The model is one
 * Map keyed by pointerId, because a touch screen sends several at once and a
 * mouse never does — treating them uniformly is what lets one set of handlers
 * serve both. My first rebuild dropped all of it and kept only a naive drag,
 * which is why dragging stopped working: the server wants grab → drag → drop,
 * and only `drag` was ever sent.
 */

/** How far a press may wander and still count as a tap, CSS px. Generous,
 *  because a thumb is not a mouse. */
const TAP_SLOP = 12;
/** How long a press must be held on a road user to remove it, ms. */
const HOLD_MS = 550;

type Pt = { start: [number, number]; at: [number, number]; last?: [number, number] };

export default function Viewport({
  frameRef,
  scene,
  layers,
  follow,
  erase,
  brush,
  overlay,
  onCommand,
  onViewChange,
}: {
  frameRef: React.RefObject<Frame | null>;
  scene: Scene;
  layers: Layers;
  follow: boolean;
  erase: boolean;
  brush: { cls: string; policy: string; speed: number };
  overlay?: React.ReactNode;
  onCommand: (msg: Record<string, unknown>) => void;
  onViewChange: (zoom: number, moved: boolean) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<Renderer | null>(null);

  // Mutable gesture state. None of this belongs in React state — it changes
  // many times per gesture and nothing in the tree renders from it.
  const pointers = useRef(new Map<number, Pt>());
  const dragId = useRef<number | null>(null);
  const dragMoved = useRef(false);
  const panning = useRef(false);
  const pinchDist = useRef(0);
  const pinchMid = useRef<[number, number]>([0, 0]);
  const holdTimer = useRef(0);
  const eraseRef = useRef(erase);
  const brushRef = useRef(brush);

  useEffect(() => {
    eraseRef.current = erase;
    brushRef.current = brush;
  }, [erase, brush]);

  useEffect(() => {
    const r = rendererRef.current;
    if (r) {
      r.scene = scene;
      r.layers = layers;
      r.view.follow = follow;
    }
  }, [scene, layers, follow]);

  useEffect(() => {
    const cv = canvasRef.current!;
    const r = new Renderer(cv);
    r.scene = scene;
    r.layers = layers;
    r.view.follow = follow;
    rendererRef.current = r;
    r.resize();

    let raf = 0;
    const loop = () => {
      const f = frameRef.current;
      if (f) {
        r.held = f.held ?? [];
        r.draw(f);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);

    const onResize = () => r.resize();
    window.addEventListener("resize", onResize);

    const screenAt = (e: PointerEvent | WheelEvent): [number, number] => {
      const box = cv.getBoundingClientRect();
      return [e.clientX - box.left, e.clientY - box.top];
    };
    const worldAt = (e: PointerEvent): [number, number] | null => {
      const f = frameRef.current;
      if (!f) return null;
      const [px, py] = screenAt(e);
      return r.toWorld(f, px, py);
    };
    const agentAt = (world: [number, number]) => {
      const f = frameRef.current;
      if (!f) return null;
      let best: { id: number } | null = null;
      let bestD = Infinity;
      for (const a of f.agents) {
        const d = Math.hypot(a.x - world[0], a.y - world[1]);
        // Generous on a small scale: a pedestrian is 0.5 m and a thumb is not.
        const reach = Math.max(1.4, a.l * 0.6);
        if (d < reach && d < bestD) {
          bestD = d;
          best = { id: a.id };
        }
      }
      return best;
    };
    const sync = () => onViewChange(r.view.zoom, r.moved);
    const cancelHold = () => {
      clearTimeout(holdTimer.current);
      holdTimer.current = 0;
    };
    const drop = () => {
      if (dragId.current !== null) {
        onCommand({ cmd: "drop", id: dragId.current });
        dragId.current = null;
      }
    };
    const cursor = () => {
      cv.style.cursor = eraseRef.current ? "cell" : "crosshair";
    };

    const onDown = (e: PointerEvent) => {
      cv.setPointerCapture(e.pointerId);
      const at = screenAt(e);
      pointers.current.set(e.pointerId, { start: at, at });

      if (pointers.current.size === 2) {
        // Second finger down: whatever the first was doing, this is a pinch.
        cancelHold();
        drop();
        panning.current = false;
        const [a, b] = [...pointers.current.values()];
        pinchDist.current = Math.hypot(a.at[0] - b.at[0], a.at[1] - b.at[1]) || 1;
        pinchMid.current = [(a.at[0] + b.at[0]) / 2, (a.at[1] + b.at[1]) / 2];
        return;
      }
      if (pointers.current.size > 2) return;

      const pt = worldAt(e);
      if (!pt) return;
      const hit = agentAt(pt);
      dragMoved.current = false;

      if (hit && !e.shiftKey && !eraseRef.current) {
        dragId.current = hit.id;
        cv.style.cursor = "grabbing";
        // grab FIRST: the session only considers a body held once this lands,
        // and `drag` on an unheld body does nothing.
        onCommand({ cmd: "grab", id: hit.id });
        // Hold still on a road user and it is removed. This is the only way
        // to delete one on a touch screen — the alternative was shift-click,
        // and no phone has a shift key.
        holdTimer.current = window.setTimeout(() => {
          holdTimer.current = 0;
          if (dragId.current === null) return;
          onCommand({ cmd: "drop", id: dragId.current });
          onCommand({ cmd: "remove", x: pt[0], y: pt[1] });
          dragId.current = null;
          dragMoved.current = true; // consumed: no placement on release
          cursor();
        }, HOLD_MS);
      } else if (!hit) {
        panning.current = true; // empty road: drag to pan
      }
    };

    const onMove = (e: PointerEvent) => {
      const p = pointers.current.get(e.pointerId);
      if (p) p.at = screenAt(e);

      if (pointers.current.size === 2) {
        const [a, b] = [...pointers.current.values()];
        const dist = Math.hypot(a.at[0] - b.at[0], a.at[1] - b.at[1]) || 1;
        const mid: [number, number] = [
          (a.at[0] + b.at[0]) / 2,
          (a.at[1] + b.at[1]) / 2,
        ];
        r.view.panX += mid[0] - pinchMid.current[0];
        r.view.panY += mid[1] - pinchMid.current[1];
        pinchMid.current = mid;
        r.anchorZoom(
          frameRef.current,
          mid[0],
          mid[1],
          r.view.zoom * (dist / pinchDist.current)
        );
        pinchDist.current = dist;
        sync();
        return;
      }

      if (p && Math.hypot(p.at[0] - p.start[0], p.at[1] - p.start[1]) > TAP_SLOP) {
        cancelHold();
        dragMoved.current = true;
      }

      const pt = worldAt(e);
      if (!pt) return;

      if (dragId.current !== null) {
        onCommand({ cmd: "drag", id: dragId.current, x: pt[0], y: pt[1] });
      } else if (panning.current && p) {
        const from = p.last ?? p.start;
        r.view.panX += p.at[0] - from[0];
        r.view.panY += p.at[1] - from[1];
        p.last = [...p.at];
        sync();
      } else if (!p) {
        cv.style.cursor = eraseRef.current ? "cell" : agentAt(pt) ? "grab" : "crosshair";
      }
    };

    const endPointer = (e: PointerEvent) => {
      const p = pointers.current.get(e.pointerId);
      pointers.current.delete(e.pointerId);
      cancelHold();

      if (pointers.current.size >= 1) {
        // a pinch losing one finger
        panning.current = false;
        drop();
        return;
      }

      const moved =
        !!p && Math.hypot(p.at[0] - p.start[0], p.at[1] - p.start[1]) > TAP_SLOP;

      if (dragId.current !== null) {
        drop();
        cursor();
        // A grab that never moved and was not held long enough is a tap on a
        // road user: in erase mode that removes it, otherwise nothing.
        if (!moved && eraseRef.current && p) {
          const f = frameRef.current;
          if (f) {
            const pt = r.toWorld(f, p.at[0], p.at[1]);
            onCommand({ cmd: "remove", x: pt[0], y: pt[1] });
          }
        }
        return;
      }

      if (panning.current) {
        panning.current = false;
        if (moved) return;
      }
      if (!p || moved || dragMoved.current) return;

      const f = frameRef.current;
      if (!f) return;
      const pt = r.toWorld(f, p.at[0], p.at[1]);
      if (e.shiftKey || eraseRef.current) {
        onCommand({ cmd: "remove", x: pt[0], y: pt[1] });
      } else {
        const b = brushRef.current;
        // policy and speed travel with the class: a wrong-way rider is a
        // two-wheeler with a different policy, not a different class.
        onCommand({
          cmd: "place",
          cls: b.cls,
          x: pt[0],
          y: pt[1],
          policy: b.policy,
          speed: b.speed,
        });
      }
    };

    const onCancel = (e: PointerEvent) => {
      pointers.current.delete(e.pointerId);
      cancelHold();
      panning.current = false;
      drop();
      cursor();
    };

    // Wheel and trackpad pinch both arrive here; ctrlKey marks the latter.
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [px, py] = screenAt(e);
      r.anchorZoom(
        frameRef.current,
        px,
        py,
        r.view.zoom * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016))
      );
      sync();
    };

    cv.style.cursor = "crosshair";
    cv.addEventListener("pointerdown", onDown);
    cv.addEventListener("pointermove", onMove);
    cv.addEventListener("pointerup", endPointer);
    cv.addEventListener("pointercancel", onCancel);
    cv.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      cancelHold();
      window.removeEventListener("resize", onResize);
      cv.removeEventListener("pointerdown", onDown);
      cv.removeEventListener("pointermove", onMove);
      cv.removeEventListener("pointerup", endPointer);
      cv.removeEventListener("pointercancel", onCancel);
      cv.removeEventListener("wheel", onWheel);
    };
    // Mount once; the effect above keeps scene/layers/follow fresh, and the
    // erase/brush refs keep the handlers reading current values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Exposed so the zoom cluster can drive the same anchored zoom. */
  const zoomBy = (factor: number) => {
    const r = rendererRef.current;
    const cv = canvasRef.current;
    if (!r || !cv) return;
    const box = cv.getBoundingClientRect();
    r.anchorZoom(frameRef.current, box.width / 2, box.height / 2, r.view.zoom * factor);
    onViewChange(r.view.zoom, r.moved);
  };
  const resetView = () => {
    const r = rendererRef.current;
    if (!r) return;
    r.resetView();
    onViewChange(r.view.zoom, r.moved);
  };

  return (
    <div className="viewport">
      <canvas ref={canvasRef} />
      {overlay}
      <div className="zoomers">
        <button className="key zoomers__btn" onClick={() => zoomBy(1.35)} aria-label="Zoom in">
          +
        </button>
        <button
          className="key zoomers__btn"
          onClick={() => zoomBy(1 / 1.35)}
          aria-label="Zoom out"
        >
          −
        </button>
        <button
          className="key zoomers__btn"
          onClick={resetView}
          aria-label="Reset the view"
          title="Reset the view"
        >
          ⊕
        </button>
      </div>
    </div>
  );
}

export { ZOOM_MIN, ZOOM_MAX };
