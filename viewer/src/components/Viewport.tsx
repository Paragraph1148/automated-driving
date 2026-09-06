import { useEffect, useRef } from "react";
import { Renderer, ZOOM_MAX, ZOOM_MIN } from "../render/scene";
import type { Frame, Layers, Scene } from "../lib/types";

/**
 * The canvas host. React mounts it once and then stays out of the way: the
 * draw loop reads the frame ref directly, so nothing here re-renders while
 * the world runs.
 */
export default function Viewport({
  frameRef,
  scene,
  layers,
  follow,
  onPick,
  onDrag,
  onRemove,
}: {
  frameRef: React.RefObject<Frame | null>;
  scene: Scene;
  layers: Layers;
  follow: boolean;
  onPick: (world: [number, number]) => void;
  onDrag: (id: number, world: [number, number]) => void;
  onRemove: (world: [number, number]) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<Renderer | null>(null);
  const dragging = useRef<number | null>(null);

  // Keep the renderer's copies current without re-running the draw loop.
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

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const next = r.view.zoom * Math.pow(0.999, e.deltaY);
      r.view.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, next));
      r.applyZoom();
    };
    cv.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      cv.removeEventListener("wheel", onWheel);
    };
    // Mount once. The effect above keeps scene/layers/follow fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const worldAt = (e: React.PointerEvent): [number, number] | null => {
    const r = rendererRef.current;
    const f = frameRef.current;
    if (!r || !f) return null;
    const box = (e.target as HTMLCanvasElement).getBoundingClientRect();
    return r.toWorld(f, e.clientX - box.left, e.clientY - box.top);
  };

  const hitTest = (world: [number, number]): number | null => {
    const f = frameRef.current;
    if (!f) return null;
    let best: number | null = null;
    let bestD = Infinity;
    for (const a of f.agents) {
      const d = Math.hypot(a.x - world[0], a.y - world[1]);
      const reach = Math.max(1.2, a.l * 0.6);
      if (d < reach && d < bestD) {
        bestD = d;
        best = a.id;
      }
    }
    return best;
  };

  return (
    <div className="viewport">
      <canvas
        ref={canvasRef}
        onPointerDown={(e) => {
          const world = worldAt(e);
          if (!world) return;
          const hit = hitTest(world);
          if (e.shiftKey) {
            onRemove(world);
            return;
          }
          if (hit !== null) {
            dragging.current = hit;
            (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
          } else {
            onPick(world);
          }
        }}
        onPointerMove={(e) => {
          if (dragging.current === null) return;
          const world = worldAt(e);
          if (world) onDrag(dragging.current, world);
        }}
        onPointerUp={(e) => {
          if (dragging.current !== null) {
            (e.target as HTMLCanvasElement).releasePointerCapture(e.pointerId);
            dragging.current = null;
          }
        }}
      />
    </div>
  );
}
