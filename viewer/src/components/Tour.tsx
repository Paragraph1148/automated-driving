import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/**
 * The guided tour.
 *
 * Everything this page can do is invisible at rest: the scenario list is a
 * select in a corner, the palette reads as a colour legend rather than as
 * something you click, and nothing on screen says the world is being computed
 * rather than replayed. Shown the demo cold, a viewer reported back that it
 * was "one scene with pre-set traffic" — having found none of it. Seven cards,
 * once, remembered, re-openable from the header.
 *
 * One change from the original: it used to drop any step whose target was
 * hidden, which on a phone meant the palette and ablations steps silently
 * vanished — the viewer who most needs the tour got the least of it. This
 * version opens whatever the step points at first.
 */

const TOUR_KEY = "sarathi.tour.v1";

export type TourTab = "status" | "drop" | "layers";

type Step = {
  t: string;
  body: ReactNode;
  sel: string;
  /** A different target where the first one is off-screen on a phone. */
  selNarrow?: string;
  /** Bring this rail group up before pointing at it. */
  tab?: TourTab;
  /** Open the thresholds sheet before pointing at it. */
  opensPanel?: boolean;
};

const TOUR: Step[] = [
  {
    t: "You are watching this being computed",
    // On a phone the status pill is tiny and easy to miss; point at the world
    // instead, which is the claim.
    sel: ".status",
    selNarrow: ".viewport",
    body: (
      <>
        Nothing here is a recording. The full stack — perception, multi-hypothesis
        prediction, the risk field, the trajectory search — is running in Python on
        the server and streaming to you. <b>Replan</b> in the telemetry is how many
        milliseconds the frame you are looking at took to decide.
      </>
    ),
  },
  {
    t: "Ten roads, not one scene",
    sel: "[data-tour='scenario']",
    body: (
      <>
        An unmarked village road, a narrow bridge with oncoming traffic, an
        unsignalled junction, a night highway of wrong-way riders, a school zone, a
        market street. Pick one and the whole world reloads around the same planner.
      </>
    ),
  },
  {
    t: "Put something in its way",
    sel: ".palette",
    tab: "drop",
    body: (
      <>
        Pick a road user, then click or tap anywhere on the road to drop it in. A
        cow, a rider coming at you the wrong way, a barricade. The planner has{" "}
        <b>no foreknowledge</b> of anything you place — it has to see it, guess what
        it will do, and get around it, live.
      </>
    ),
  },
  {
    t: "Drag anything, including the car",
    sel: ".viewport canvas",
    body: (
      <>
        Grab any vehicle and move it while the world keeps running. Drop a bus
        across the lane and watch the trajectory search go red. <b>Hold</b> a road
        user to remove it, or use <b>Erase</b>.
      </>
    ),
  },
  {
    t: "Get closer",
    sel: ".zoomers",
    body: (
      <>
        Pinch or scroll to zoom, drag the empty road to pan. Worth doing: at this
        range the risk field and the candidate fan are a smear, and up close they
        are the actual reasoning.
      </>
    ),
  },
  {
    t: "Change how it thinks, mid-drive",
    sel: "[data-tour='thresholds']",
    body: (
      <>
        Every threshold that shapes the behaviour is a live control. Shorten the
        follow headway, widen the gap it needs before it will overtake, raise how
        much clearance counts as squeezing past. It re-plans on the next tick.
      </>
    ),
  },
  {
    t: "Break it on purpose",
    sel: "[data-section='Ablations']",
    opensPanel: true,
    body: (
      <>
        Switch the risk field, the multi-hypothesis prediction or the safety
        envelope off and watch what the vehicle stops being able to do. That is the
        argument for the architecture, and you can run it yourself.
      </>
    ),
  },
];

/** Private windows and blocked site data throw on access, not on read. */
const store = {
  seen(): boolean {
    try {
      return localStorage.getItem(TOUR_KEY) === "seen";
    } catch {
      return false;
    }
  },
  markSeen(): void {
    try {
      localStorage.setItem(TOUR_KEY, "seen");
    } catch {
      /* nothing to do */
    }
  },
};

export default function Tour({
  open,
  onOpen,
  onClose,
  onTab,
  onPanel,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onTab: (t: TourTab) => void;
  onPanel: (open: boolean) => void;
}) {
  const [at, setAt] = useState(0);
  const ringRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  /* The parent passes these as inline arrows, and it re-renders every 200ms
     from the telemetry sampler — so a new identity arrives twenty times a
     second. Depending on them directly restarted the first-visit timer on
     every render and it never fired once. Hold them still. */
  const cb = useRef({ onOpen, onClose, onTab, onPanel });
  useEffect(() => {
    cb.current = { onOpen, onClose, onTab, onPanel };
  });

  const narrow = () => window.matchMedia("(max-width: 860px)").matches;

  const place = useCallback(() => {
    const step = TOUR[at];
    if (!step) return;
    const sel = (narrow() && step.selNarrow) || step.sel;
    const el = document.querySelector(sel) as HTMLElement | null;
    if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });

    // Measure on the next frame, after the scroll has been asked for, so the
    // ring lands on where the target actually ended up.
    requestAnimationFrame(() => {
      const ring = ringRef.current;
      const card = cardRef.current;
      if (!ring || !card) return;
      const r = el?.getBoundingClientRect();

      if (r && r.width) {
        ring.style.left = r.left - 4 + "px";
        ring.style.top = r.top - 4 + "px";
        ring.style.width = r.width + 8 + "px";
        ring.style.height = r.height + 8 + "px";
        ring.style.borderWidth = "2px";
      } else {
        // No target: collapse the ring so its huge shadow just dims everything.
        ring.style.left = "50%";
        ring.style.top = "50%";
        ring.style.width = "0px";
        ring.style.height = "0px";
        ring.style.borderWidth = "0px";
      }

      // The phone breakpoint pins the card in CSS and ignores all of this.
      if (narrow()) return;
      const box = card.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const pad = 12;
      if (!r || !r.width) {
        card.style.left = Math.round((vw - box.width) / 2) + "px";
        card.style.top = Math.round((vh - box.height) / 2) + "px";
        return;
      }
      // Below the target if it fits, above if it does not.
      let top = r.bottom + pad;
      if (top + box.height > vh - pad) top = r.top - box.height - pad;
      top = Math.max(pad, Math.min(top, vh - box.height - pad));
      let left = r.left + r.width / 2 - box.width / 2;
      left = Math.max(pad, Math.min(left, vw - box.width - pad));
      card.style.left = Math.round(left) + "px";
      card.style.top = Math.round(top) + "px";
    });
  }, [at]);

  // Open whatever this step points at, then position on it.
  useEffect(() => {
    if (!open) return;
    const step = TOUR[at];
    if (step?.tab) cb.current.onTab(step.tab);
    cb.current.onPanel(!!step?.opensPanel);
    const id = window.setTimeout(place, step?.opensPanel || step?.tab ? 90 : 0);
    return () => window.clearTimeout(id);
  }, [open, at, place]);

  useEffect(() => {
    if (!open) return;
    const onResize = () => place();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open, place]);

  const finish = useCallback(() => {
    store.markSeen();
    cb.current.onPanel(false);
    cb.current.onClose();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setAt((i) => (i < TOUR.length - 1 ? i + 1 : i));
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setAt((i) => Math.max(0, i - 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, finish]);

  // First visit only, deferred a beat so the tuning panel has been built from
  // the server's schema and the ablations step has something to point at.
  useEffect(() => {
    if (store.seen()) return;
    const id = window.setTimeout(() => {
      setAt(0);
      cb.current.onOpen();
    }, 1400);
    return () => window.clearTimeout(id);
  }, []);

  if (!open) return null;
  const step = TOUR[at];
  const last = at === TOUR.length - 1;

  return (
    <div className="tour">
      <div className="tour__ring" ref={ringRef} />
      <div
        className="tour__card"
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
      >
        <span className="tour__count">
          Step {at + 1} of {TOUR.length}
        </span>
        <h3 id="tour-title">{step.t}</h3>
        <p>{step.body}</p>
        <div className="tour__nav">
          <button className="key btn" onClick={finish}>
            Skip
          </button>
          <span className="tour__spacer" />
          <span className="tour__dots" aria-hidden="true">
            {TOUR.map((_, i) => (
              <i key={i} className={i === at ? "on" : ""} />
            ))}
          </span>
          {at > 0 && (
            <button className="key btn" onClick={() => setAt((i) => i - 1)}>
              Back
            </button>
          )}
          <button
            className="key btn is-accent"
            autoFocus
            onClick={() => (last ? finish() : setAt((i) => i + 1))}
          >
            {last ? "Done" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
