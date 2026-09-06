import type { Frame, Layers, Scene, XY } from "../lib/types";
import { classVar } from "../lib/types";

/**
 * The viewport renderer. Deliberately outside React.
 *
 * Telemetry lands at 20 Hz and a frame carries the risk grid, a fan of ~130
 * candidates, prediction envelopes and every body in the scene. Driving that
 * through React state would rebuild the tree twenty times a second to paint
 * pixels React cannot see anyway. So the chrome is React and this is not:
 * it owns a canvas, reads the newest frame from a ref, and draws.
 *
 * The geometry here is ported from the original Mission Control rather than
 * rewritten. It carries fixes that were expensive to find — the risk-grid
 * rotation, the both-axes fit — and the comments that explain them are the
 * reason to keep it intact.
 */

const VIEW_ACROSS = 26; // m of road width plus verge, vertically
const VIEW_AHEAD_MAX = 95; // m of road ahead on a wide screen
const VIEW_AHEAD_MIN = 46; // ...and on a narrow one
export const ZOOM_MIN = 0.55;
export const ZOOM_MAX = 5.0;

export interface View {
  follow: boolean;
  zoom: number;
  panX: number;
  panY: number;
}

type Transform = (x: number, y: number) => [number, number];

export class Renderer {
  private cv: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private riskCanvas = document.createElement("canvas");
  private riskCtx: CanvasRenderingContext2D;
  private dpr = 1;
  private baseScale = 7;

  scale = 7;
  view: View = { follow: true, zoom: 1, panX: 0, panY: 0 };
  scene: Scene = {};
  layers!: Layers;
  held: number[] = [];

  constructor(canvas: HTMLCanvasElement) {
    this.cv = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.riskCtx = this.riskCanvas.getContext("2d")!;
  }

  private css(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  resize(): void {
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    const host = this.cv.parentElement;
    if (!host) return;
    const r = host.getBoundingClientRect();
    this.cv.width = Math.max(1, r.width * this.dpr);
    this.cv.height = Math.max(1, r.height * this.dpr);
    this.cv.style.width = r.width + "px";
    this.cv.style.height = r.height + "px";
    // Scale from *both* axes. Deriving it from the width alone asked a phone
    // to hold 95 m of road across 390 px, which drew a 4 m carriageway 17 px
    // tall — too small to read and far too small to hit with a thumb. A narrow
    // screen shows less road ahead instead of shrinking everything on it.
    const ahead = Math.max(VIEW_AHEAD_MIN, Math.min(VIEW_AHEAD_MAX, r.width / 8));
    // Ceiling raised from 11: moving the panels into a rail and a sheet gave
    // the viewport noticeably more width than the old sidebar layout left it,
    // and the old cap threw that room away.
    this.baseScale = Math.max(
      4,
      Math.min(13, Math.min(r.width / ahead, r.height / VIEW_ACROSS))
    );
    this.scale = this.baseScale * this.view.zoom;
  }

  applyZoom(): void {
    this.scale = this.baseScale * this.view.zoom;
  }

  /**
   * Zoom about a screen point: find the world point under it, apply the new
   * zoom, re-project, and pan by however far it moved. Without this the view
   * zooms about its own centre and whatever you were looking at slides away.
   * Used by the wheel, the pinch midpoint and the zoom buttons alike.
   */
  anchorZoom(f: Frame | null, px: number, py: number, nextZoom: number): void {
    const before = f ? this.toWorld(f, px, py) : null;
    this.view.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, nextZoom));
    this.applyZoom();
    if (before && f) {
      const after = this.transform(f)(before[0], before[1]);
      this.view.panX += px - after[0];
      this.view.panY += py - after[1];
    }
  }

  resetView(): void {
    this.view.zoom = 1;
    this.view.panX = 0;
    this.view.panY = 0;
    this.applyZoom();
  }

  /** Has the operator moved the view off the one the page chose? */
  get moved(): boolean {
    return (
      Math.abs(this.view.zoom - 1) > 1e-3 || !!this.view.panX || !!this.view.panY
    );
  }

  /**
   * One description of the view, so the forward transform and its inverse
   * cannot drift apart — they used to carry duplicate copies of the same six
   * lines, and adding zoom and pan to only one of them put every drop and
   * every drag a little way from where it was aimed.
   */
  private viewParams(f: Frame) {
    const w = this.cv.width / this.dpr;
    const h = this.cv.height / this.dpr;
    const th = this.view.follow ? -f.ego.h : 0;
    return {
      cx: (this.view.follow ? w * 0.3 : w * 0.5) + this.view.panX,
      cy: h * 0.5 + this.view.panY,
      ox: this.view.follow ? f.ego.x : (this.scene.length || 100) / 2,
      oy: this.view.follow ? f.ego.y : 0,
      c: Math.cos(th),
      s: Math.sin(th),
    };
  }

  transform(f: Frame): Transform {
    const v = this.viewParams(f);
    const k = this.scale;
    return (x, y) => {
      const dx = x - v.ox;
      const dy = y - v.oy;
      return [v.cx + (dx * v.c - dy * v.s) * k, v.cy - (dx * v.s + dy * v.c) * k];
    };
  }

  /** Screen -> world, the exact inverse, so a drop lands where it was aimed. */
  toWorld(f: Frame, sx: number, sy: number): XY {
    const v = this.viewParams(f);
    const k = this.scale;
    const ux = (sx - v.cx) / k;
    const uy = -(sy - v.cy) / k;
    return [v.ox + ux * v.c + uy * v.s, v.oy - ux * v.s + uy * v.c];
  }

  private polyline(
    T: Transform,
    pts: XY[] | undefined,
    stroke: string,
    width: number,
    dash?: number[]
  ): void {
    if (!pts || pts.length < 2) return;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    const [x0, y0] = T(pts[0][0], pts[0][1]);
    ctx.moveTo(x0, y0);
    for (let i = 1; i < pts.length; i++) {
      const p = T(pts[i][0], pts[i][1]);
      ctx.lineTo(p[0], p[1]);
    }
    ctx.stroke();
    ctx.restore();
  }

  private drawRisk(f: Frame, T: Transform): void {
    const g = f.risk;
    if (!g) return;
    const bin = atob(g.data);
    const n = g.nx * g.ny;
    const img = this.riskCtx.createImageData(g.nx, g.ny);
    const hue = this.css("--risk-hue");
    // The gamma exists so the low end recedes toward the surface instead of
    // hazing the whole road. On the pale deck it has to work harder: the same
    // 1.35 that reads as discrete blobs on black showed the grid's rectangular
    // extent as a tint over the carriageway. Both live in the theme.
    const gamma = parseFloat(this.css("--risk-gamma")) || 1.35;
    const peak = parseFloat(this.css("--risk-alpha")) || 205;
    const r = parseInt(hue.slice(1, 3), 16);
    const gg = parseInt(hue.slice(3, 5), 16);
    const b = parseInt(hue.slice(5, 7), 16);
    for (let i = 0; i < n; i++) {
      const v = bin.charCodeAt(i) / 255;
      // Single-hue sequential: alpha carries magnitude, with a gentle gamma so
      // the low end recedes toward the surface instead of hazing the road.
      img.data[i * 4] = r;
      img.data[i * 4 + 1] = gg;
      img.data[i * 4 + 2] = b;
      img.data[i * 4 + 3] = Math.round(Math.pow(v, gamma) * peak);
    }
    this.riskCanvas.width = g.nx;
    this.riskCanvas.height = g.ny;
    this.riskCtx.putImageData(img, 0, 0);

    const ctx = this.ctx;
    const p0 = T(f.ego.x, f.ego.y);
    ctx.save();
    ctx.translate(p0[0], p0[1]);
    // The grid is in the ego's own frame. Follow mode already turns the world
    // so the ego points right, so no rotation is wanted there. With follow off
    // the screen is world-aligned and the grid has to be turned to match — by
    // MINUS the heading, not plus: the scale(1,-1) below is part of the
    // composition, and translate . rotate(h) . flipY sends a point 10 m ahead
    // and 4 m left of a vehicle at 34 degrees to (10.5, 2.3) when the world
    // puts it at (6.0, -8.9). The risk field was drawn skewed across the road
    // whenever anyone turned Follow off.
    ctx.rotate(this.view.follow ? 0 : -f.ego.h);
    ctx.scale(1, -1);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(
      this.riskCanvas,
      g.x0 * this.scale,
      g.y0 * this.scale,
      g.nx * g.res * this.scale,
      g.ny * g.res * this.scale
    );
    ctx.restore();
  }

  private drawBody(
    T: Transform,
    x: number,
    y: number,
    h: number,
    len: number,
    wid: number,
    fill: string,
    ring?: string,
    ringWidth?: number,
    resting?: boolean
  ): void {
    const ctx = this.ctx;
    const hl = len / 2;
    const hw = wid / 2;
    const c = Math.cos(h);
    const s = Math.sin(h);
    const mid = T(x, y);
    const nose = T(x + hl * c, y + hl * s);

    if (resting) {
      // An animal that has settled in the road is drawn as a rounded body with
      // no heading tick: it is not going anywhere, and a cow lying in the
      // carriageway is a different hazard from one walking across it. T is a
      // rigid transform with a uniform scale, so the screen angle comes
      // straight from where the nose landed.
      const ang = Math.atan2(nose[1] - mid[1], nose[0] - mid[0]);
      ctx.beginPath();
      ctx.ellipse(mid[0], mid[1], hl * this.scale, hw * this.scale, ang, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.fill();
      if (ring) {
        ctx.strokeStyle = ring;
        ctx.lineWidth = ringWidth || 1.25;
        ctx.stroke();
      }
      return;
    }

    const pts: [number, number][] = (
      [
        [hl, hw],
        [hl, -hw],
        [-hl, -hw],
        [-hl, hw],
      ] as [number, number][]
    ).map(([a, b]) => T(x + a * c - b * s, y + a * s + b * c));
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    if (ring) {
      ctx.strokeStyle = ring;
      ctx.lineWidth = ringWidth || 1.25;
      ctx.stroke();
    }
    // Heading tick, so direction of travel reads without animation. It is also
    // the secondary encoding that keeps class off hue alone.
    ctx.beginPath();
    ctx.moveTo(mid[0], mid[1]);
    ctx.lineTo(nose[0], nose[1]);
    ctx.strokeStyle = ring || fill;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  draw(f: Frame | null): void {
    if (!f) return;
    const ctx = this.ctx;
    const css = (n: string) => this.css(n);
    const T = this.transform(f);
    const on = this.layers;
    const w = this.cv.width / this.dpr;
    const h = this.cv.height / this.dpr;

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = css("--surface-1");
    ctx.fillRect(0, 0, w, h);

    const scene = this.scene;

    // Carriageway
    if (scene.left_edge && scene.right_edge) {
      ctx.save();
      ctx.beginPath();
      let p = T(scene.left_edge[0][0], scene.left_edge[0][1]);
      ctx.moveTo(p[0], p[1]);
      for (const q of scene.left_edge) {
        p = T(q[0], q[1]);
        ctx.lineTo(p[0], p[1]);
      }
      for (let i = scene.right_edge.length - 1; i >= 0; i--) {
        p = T(scene.right_edge[i][0], scene.right_edge[i][1]);
        ctx.lineTo(p[0], p[1]);
      }
      ctx.closePath();
      ctx.fillStyle = css("--road");
      ctx.fill();
      ctx.restore();
      this.polyline(T, scene.left_edge, css("--road-edge"), 1.5);
      this.polyline(T, scene.right_edge, css("--road-edge"), 1.5);
    }

    // Lane markings only when the scenario actually has them.
    if ((scene.lane_marking_quality ?? 0) > 0.05) {
      ctx.save();
      ctx.globalAlpha = scene.lane_marking_quality!;
      this.polyline(T, scene.centreline, css("--road-edge"), 1.2, [10, 12]);
      ctx.restore();
    }

    for (const d of scene.defects || []) {
      const c = T(d.x, d.y);
      ctx.beginPath();
      ctx.arc(c[0], c[1], d.r * this.scale, 0, Math.PI * 2);
      ctx.fillStyle = css("--road-edge");
      ctx.globalAlpha = 0.25 + 0.5 * d.severity;
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    if (on.risk) this.drawRisk(f, T);
    if (on.ref) this.polyline(T, f.reference, css("--ink-3"), 1.2, [3, 5]);

    if (on.fan && f.fan) {
      for (const c of f.fan) {
        ctx.globalAlpha = c.ok ? 0.34 : 0.13;
        this.polyline(T, c.p, c.ok ? css("--ink-2") : css("--crit"), 1);
      }
      ctx.globalAlpha = 1;
    }

    if (on.cones && f.cones) {
      for (const t of f.cones) {
        for (const m of t.modes) {
          ctx.globalAlpha = 0.16 + 0.5 * m.p;
          // Uncertainty envelope: centre line plus the lateral sigma either side.
          const up: XY[] = [];
          const dn: XY[] = [];
          for (let i = 0; i < m.xy.length; i++) {
            const [x, y] = m.xy[i];
            const nx = i < m.xy.length - 1 ? m.xy[i + 1][0] - x : x - m.xy[i - 1][0];
            const ny = i < m.xy.length - 1 ? m.xy[i + 1][1] - y : y - m.xy[i - 1][1];
            const L = Math.hypot(nx, ny) || 1;
            const sx = (-ny / L) * m.s[i];
            const sy = (nx / L) * m.s[i];
            up.push([x + sx, y + sy]);
            dn.push([x - sx, y - sy]);
          }
          ctx.beginPath();
          let p = T(up[0][0], up[0][1]);
          ctx.moveTo(p[0], p[1]);
          for (const q of up) {
            p = T(q[0], q[1]);
            ctx.lineTo(p[0], p[1]);
          }
          for (let i = dn.length - 1; i >= 0; i--) {
            p = T(dn[i][0], dn[i][1]);
            ctx.lineTo(p[0], p[1]);
          }
          ctx.closePath();
          ctx.fillStyle = css(classVar(t.cls));
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
    }

    // The road user the supervisor named as the one with nowhere past it,
    // marked where the *tracker* believes it is — which is what the vehicle is
    // reacting to. Drawn before the bodies so the ring sits under them.
    const blockedAt = f.debug?.blocked_at;
    if (blockedAt) {
      const c = T(blockedAt[0], blockedAt[1]);
      const r =
        Math.max(18, 1.7 * this.scale) * (1 + 0.08 * Math.sin(Date.now() / 260));
      ctx.beginPath();
      ctx.arc(c[0], c[1], r, 0, Math.PI * 2);
      ctx.strokeStyle = css("--crit");
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (on.truth) {
      for (const a of f.agents) {
        const held = this.held.includes(a.id);
        this.drawBody(
          T,
          a.x,
          a.y,
          a.h,
          a.l,
          a.w,
          css(classVar(a.cls)),
          held ? css("--accent") : css("--ego-ring"),
          held ? 2.5 : 1.25,
          !!a.rest
        );
        if (held) {
          const c = T(a.x, a.y);
          ctx.beginPath();
          ctx.arc(c[0], c[1], Math.max(14, 1.1 * this.scale), 0, Math.PI * 2);
          ctx.strokeStyle = css("--accent");
          ctx.lineWidth = 1.5;
          ctx.setLineDash([4, 4]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }

    if (on.tracks && f.tracks) {
      for (const t of f.tracks) {
        const c = T(t.x, t.y);
        ctx.beginPath();
        ctx.arc(c[0], c[1], Math.max(3, t.sig * this.scale), 0, Math.PI * 2);
        ctx.strokeStyle = css("--ink-1");
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    if (on.plan && f.plan) this.polyline(T, f.plan, css("--accent"), 2.5);

    const e = f.ego;
    this.drawBody(T, e.x, e.y, e.h, e.l, e.w, css("--ego"), css("--ego-ring"), 2);
  }
}
