/**
 * The internal briefing deck - what the six of us need in our heads before we
 * stand in front of a judge. Not the submission deck: this one is allowed to be
 * long, blunt about what does not work yet, and full of the exact words to say.
 *
 *   cd ppt && node build_internal.js
 *
 * Numbers come from ../artifacts/benchmark.json (scripts/benchmark.py), so this
 * deck and the official one can never quote different figures.
 */
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const ART = path.join(__dirname, "..", "artifacts");
const BENCH = path.join(ART, "benchmark.json");
if (!fs.existsSync(BENCH)) {
  console.error(`missing ${BENCH} - run: uv run python scripts/benchmark.py --seeds 3`);
  process.exit(1);
}
const bench = JSON.parse(fs.readFileSync(BENCH, "utf8"));
const OURS = bench.summary.sarathi;
const BASE = bench.summary.baseline;
const SEEDS = bench.seeds.length;
const pct = (x) => `${(x * 100).toFixed(0)}%`;

// --- palette: the console's own colours, so the deck and the demo agree ------
const ASPHALT = "12151B";
const ASPHALT2 = "1C212B";
const PAPER = "FFFFFF";
const WASH = "F2F4F7";
const INK = "1A1D23";
const MUTED = "6B7280";
const AMBER = "E8A33D";   // the chosen path
const RED = "D2503C";     // risk
const GREEN = "2E9E6B";   // predictions
const RULE = "E2E5EA";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";          // 13.33 x 7.5in - set before any slide
pres.author = "SARATHI team";
pres.title = "SARATHI internal briefing";

const M = 0.55;                        // page margin
const COLW = 5.9;                      // a comfortable single column
let stageNo = 0;

// ----------------------------------------------------------------- helpers
function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: ASPHALT };
  return s;
}

function lightSlide(title, kicker, badge) {
  const s = pres.addSlide();
  s.background = { color: PAPER };
  if (badge !== undefined) {
    s.addShape(pres.ShapeType.ellipse, {
      x: M, y: 0.42, w: 0.46, h: 0.46, fill: { color: AMBER },
    });
    s.addText(String(badge), {
      x: M, y: 0.42, w: 0.46, h: 0.46, align: "center", valign: "middle",
      fontFace: "Arial", fontSize: 15, bold: true, color: ASPHALT, isTextBox: true,
      margin: 0,
    });
  }
  const tx = badge === undefined ? M : M + 0.62;
  s.addText(title, {
    x: tx, y: 0.38, w: 13.33 - tx - M, h: 0.55, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 27, bold: true, color: INK, valign: "middle",
  });
  if (kicker) {
    s.addText(kicker, {
      x: tx, y: 0.95, w: 13.33 - tx - M, h: 0.36, isTextBox: true, margin: 0,
      fontFace: "Arial", fontSize: 13, color: MUTED, valign: "top",
    });
  }
  return s;
}


/**
 * Rough wrapped height, in inches, for a block of Arial text. Arial averages
 * close to 0.5 em per character, which is near enough to place a band under a
 * column without leaving a hole or overlapping it.
 */
function estHeight(items, w, fontSize, opt) {
  const o = Object.assign({ gap: 0.06, lead: 1.34, titleSize: 0 }, opt);
  const cpl = Math.max(12, (w * 72) / (fontSize * 0.5));
  let lines = 0;
  items.forEach((it) => {
    const text = typeof it === "string" ? it : (it.t || "") + " " + (it.b || it.text || "");
    lines += Math.max(1, Math.ceil(text.length / cpl));
  });
  const titleH = o.titleSize ? (o.titleSize * o.lead) / 72 + 0.06 : 0;
  return titleH + (lines * fontSize * o.lead) / 72 + items.length * o.gap;
}

/** A list of bullets. `items` are strings, or {t, b} for a bold lead-in. */
function bullets(s, items, opt) {
  const o = Object.assign({ x: M, y: 1.5, w: COLW, h: 4.8, fontSize: 13 }, opt);
  const runs = [];
  items.forEach((it, i) => {
    const last = i === items.length - 1;
    if (typeof it === "string") {
      runs.push({
        text: it,
        options: { bullet: true, breakLine: !last, color: INK,
                   paraSpaceAfter: o.gap === undefined ? 7 : o.gap },
      });
    } else {
      runs.push({
        text: it.t + " ",
        options: { bullet: true, bold: true, color: o.leadColor || ASPHALT },
      });
      runs.push({
        text: it.b,
        options: { breakLine: !last, color: INK,
                   paraSpaceAfter: o.gap === undefined ? 7 : o.gap },
      });
    }
  });
  s.addText(runs, {
    x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: o.fontSize, lineSpacingMultiple: 1.08,
    valign: "top",
  });
}

/** A tinted card with a heading and body text. */
function card(s, o) {
  if (o.autoH) {
    const est = estHeight(o.lines || [], o.w - 0.4, o.fontSize || 11.5,
                          { titleSize: o.title ? (o.titleSize || 13) : 0 });
    o.h = Math.max(o.minH || 0.7, Math.min(o.maxH || 6.0, est + 0.3));
  }
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.06,
    fill: { color: o.fill || WASH }, line: { color: o.fill || WASH, width: 0 },
  });
  const runs = [];
  if (o.title) {
    runs.push({
      text: o.title,
      options: { bold: true, fontSize: o.titleSize || 13,
                 color: o.titleColor || ASPHALT, breakLine: true,
                 paraSpaceAfter: 4 },
    });
  }
  (o.lines || []).forEach((l, i) => runs.push({
    text: typeof l === "string" ? l : l.t,
    options: {
      fontSize: o.fontSize || 11.5, color: o.color || INK,
      bullet: typeof l !== "string" && l.bullet ? true : false,
      breakLine: i !== (o.lines.length - 1), paraSpaceAfter: 4,
    },
  }));
  s.addText(runs, {
    x: o.x + 0.18, y: o.y + 0.13, w: o.w - 0.36, h: o.h - 0.26,
    isTextBox: true, margin: 0, fontFace: "Arial", valign: o.valign || "top",
    lineSpacingMultiple: 1.06,
  });
}

/** One big number with a label - use where a number is the point. */
function stat(s, o) {
  s.addText(o.value, {
    x: o.x, y: o.y, w: o.w, h: o.h || 0.72, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: o.size || 34, bold: true,
    color: o.color || ASPHALT, align: o.align || "left", valign: "bottom",
  });
  s.addText(o.label, {
    x: o.x, y: o.y + (o.h || 0.72), w: o.w, h: 0.5, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 10.5, color: MUTED, align: o.align || "left",
  });
}

/** The pipeline, drawn as chevrons. Used twice, so it is a function. */
function pipeline(s, y, labels, opt) {
  const o = Object.assign({ x: M, w: 13.33 - 2 * M, h: 0.5, fontSize: 9,
                            active: -1 }, opt);
  const step = o.w / labels.length;
  labels.forEach((label, i) => {
    s.addShape(pres.ShapeType.chevron, {
      x: o.x + i * step, y, w: step * 1.05, h: o.h,
      fill: { color: i === o.active ? AMBER : ASPHALT },
      line: { color: PAPER, width: 1 },
    });
    s.addText(label, {
      x: o.x + i * step, y, w: step * 1.05, h: o.h, isTextBox: true, margin: 0,
      align: "center", valign: "middle", fontFace: "Arial",
      fontSize: o.fontSize, bold: true,
      color: i === o.active ? ASPHALT : "FFFFFF",
    });
  });
}

function caption(s, x, y, w, text) {
  s.addText(text, {
    x, y, w, h: 0.32, isTextBox: true, margin: 0, fontFace: "Arial",
    fontSize: 9.5, italic: true, color: MUTED,
  });
}

function table(s, head, rows, o) {
  const body = [[...head.map((h) => ({
    text: h,
    options: { bold: true, color: PAPER, fill: { color: ASPHALT } },
  }))]];
  rows.forEach((r, i) => body.push(r.map((c, j) => ({
    text: c,
    options: {
      color: j === 0 ? ASPHALT : INK, bold: j === 0 && o.boldFirst !== false,
      fill: { color: i % 2 ? WASH : PAPER },
    },
  }))));
  s.addTable(body, {
    x: o.x, y: o.y, w: o.w, colW: o.colW, border: { pt: 0.5, color: RULE },
    fontFace: "Arial", fontSize: o.fontSize || 10.5, valign: "middle",
    rowH: o.rowH, margin: [3, 6, 3, 6],
  });
}

const STAGES = ["SENSE", "FUSE", "PREDICT", "RISK FIELD", "CORRIDOR",
                "BEHAVIOUR", "LATTICE", "RSS + CBF"];

// ============================================================ 1. title (dark)
{
  const s = darkSlide();
  s.addText("SARATHI", {
    x: M, y: 2.15, w: 8.2, h: 1.1, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 60, bold: true, color: PAPER, charSpacing: 2,
  });
  s.addText("Internal briefing — the version with nothing hidden", {
    x: M, y: 3.25, w: 8.6, h: 0.5, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 19, color: AMBER,
  });
  s.addText(
    "Everything the six of us need to explain, demonstrate and defend our "
    + "SIH26037 entry — including the parts that do not work yet.", {
      x: M, y: 3.85, w: 7.6, h: 0.8, isTextBox: true, margin: 0,
      fontFace: "Arial", fontSize: 13, color: "AEB4BF", lineSpacingMultiple: 1.2,
    });
  s.addText("SIH26037 · MathWorks · Adaptive path planning on unstructured Indian roads", {
    x: M, y: 6.5, w: 9.5, h: 0.4, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 11, color: MUTED,
  });
  s.addImage({ path: path.join(ART, "fig-bev.png"), x: 8.6, y: 2.0, w: 4.2, h: 1.91 });
  s.addNotes("Open with: this deck is for us, not the judges. The submission deck "
    + "is the other file. Read this one end to end once, then drill slides 21-23.");
}

// ============================================================ 2. how to use it
{
  const s = lightSlide("How to use this deck",
    "Twenty minutes end to end. Then everyone drills their own section.");
  card(s, { x: M, y: 1.6, w: 4.0, h: 2.2, title: "Presenting (O4, anyone)",
    lines: [{ t: "Slides 3–6 are the pitch, in order.", bullet: true },
            { t: "Slide 4 is the only slide you must know verbatim.", bullet: true },
            { t: "Never present a number that is not on slide 20.", bullet: true }] });
  card(s, { x: M + 4.25, y: 1.6, w: 4.0, h: 2.2, title: "Driving the demo (B1, B2)",
    lines: [{ t: "Slides 18–19: the runbook, click by click.", bullet: true },
            { t: "Rehearse the failure path, not just the happy one.", bullet: true },
            { t: "One person drives, one person narrates. Never both.", bullet: true }] });
  card(s, { x: M + 8.5, y: 1.6, w: 4.0, h: 2.2, title: "Everyone, without exception",
    lines: [{ t: "Slides 21–22: the weaknesses and the Q&A drill.", bullet: true },
            { t: "If a judge asks and you do not know — say so, then say who does.", bullet: true },
            { t: "A confident wrong answer loses more marks than a gap.", bullet: true }] });
  card(s, { x: M, y: 4.1, w: 12.23, h: 2.3, fill: ASPHALT, titleColor: AMBER,
    color: "E6E8EC", titleSize: 14,
    title: "The one idea this whole project rests on",
    fontSize: 15,
    lines: ["Every production self-driving stack plans inside a lane. An Indian road "
      + "does not have lanes — it has free space that is negotiated, and that changes "
      + "every second. So we removed the lane from the planner entirely: the reference "
      + "line is computed from where the road is actually free, and the obstacle map is "
      + "a continuous risk field that knows what each road user is and what it might do next.",
      "If you can say that paragraph in your own words, you can hold a conversation with any judge."] });
  s.addNotes("Do not let anyone skip slide 21. The weakest thing we can do is be "
    + "surprised in public by a limitation we already know about.");
}

// ============================================================ 3. the 60s pitch
{
  const s = lightSlide("The 60-second pitch", "Say it in this order. It works.", 1);
  const lines = [
    { n: "1", t: "The problem", b: "Indian roads have no usable lane markings, and traffic is mixed and non-lane-based. Every production planner assumes both. That is why autonomy stalls here." },
    { n: "2", t: "The idea", b: "Replace the two primitives that break. The lane centreline becomes a drivable corridor solved from free space. The occupancy grid becomes a risk field that is class-aware, harm-weighted and predicted forward in time." },
    { n: "3", t: "The proof", b: `Ten scenarios, ${SEEDS} seeds each, against a conventional lane-following planner on identical seeds and identical sensor noise. We publish the losses as well as the wins.` },
    { n: "4", t: "The demo", b: "It is live. Drag a cow into our path with the mouse and the planner has never seen it before. Every threshold is exposed on screen and can be changed while it drives." },
  ];
  let y = 1.55;
  lines.forEach((l) => {
    s.addShape(pres.ShapeType.ellipse, { x: M, y: y + 0.06, w: 0.4, h: 0.4,
      fill: { color: AMBER } });
    s.addText(l.n, { x: M, y: y + 0.06, w: 0.4, h: 0.4, align: "center",
      valign: "middle", fontFace: "Arial", fontSize: 13, bold: true,
      color: ASPHALT, isTextBox: true, margin: 0 });
    s.addText([
      { text: l.t + "  ", options: { bold: true, fontSize: 15, color: ASPHALT } },
      { text: l.b, options: { fontSize: 13, color: INK } },
    ], { x: M + 0.6, y, w: 7.6, h: 1.25, isTextBox: true, margin: 0,
         fontFace: "Arial", valign: "top", lineSpacingMultiple: 1.1 });
    y += 1.32;
  });
  card(s, { x: 8.9, y: 1.55, w: 3.88, h: 2.5, fill: WASH,
    title: "Phrases that lose the room",
    lines: [{ t: "“basically it's like a normal self-driving car, but…”", bullet: true },
            { t: "“we used AI to detect obstacles”", bullet: true },
            { t: "“it works most of the time”", bullet: true },
            { t: "Any sentence with three clauses in it.", bullet: true }] });
  card(s, { x: 8.9, y: 4.25, w: 3.88, h: 2.55, fill: ASPHALT, color: "E6E8EC",
    titleColor: AMBER, title: "Open with the road, not the algorithm",
    lines: ["Start with the road every judge has actually driven on. The moment "
      + "they picture a market street with a cow in it, the rest of the pitch has "
      + "somewhere to land.",
      "Then, and only then, say the words 'drivable corridor' and 'risk field'."] });
  s.addNotes("Sixty seconds. Time yourself. If you run long, cut point 3 to one "
    + "sentence, never point 2.");
}

// ============================================================ 4. what PS asks
{
  const s = lightSlide("What the problem statement actually asks for",
    "MathWorks wrote this. Every phrase in it is a scoring line.", 2);
  bullets(s, [
    { t: "Adaptive path planning", b: "— a path that changes as the scene changes, not a fixed route" },
    { t: "Collision avoidance", b: "— with dynamic, unpredictable obstacles" },
    { t: "Unstructured roads", b: "— missing or unreliable lane markings, mixed traffic, encroachment" },
    { t: "Replanning latency", b: "— they will ask how fast the loop is, and want a number" },
    { t: "Path smoothness", b: "— jerk and curvature, not just 'it avoided the thing'" },
    { t: "MathWorks toolchain", b: "— MATLAB, Simulink, Stateflow, RoadRunner" },
  ], { x: M, y: 1.6, w: 6.1, fontSize: 13 });
  card(s, { x: 7.0, y: 1.6, w: 5.78, h: 2.6, fill: WASH,
    title: "What they will actually test on the day",
    lines: [{ t: "Can you break it in front of them? (so we let them try)", bullet: true },
            { t: "Do your numbers survive a follow-up question?", bullet: true },
            { t: "Is anything on screen scripted or pre-rendered?", bullet: true },
            { t: "Do you understand your own algorithm, or did you paste it?", bullet: true }] });
  card(s, { x: 7.0, y: 4.35, w: 5.78, h: 2.2, fill: ASPHALT, color: "E6E8EC",
    titleColor: AMBER, title: "The trap to avoid",
    lines: ["A judge from MathWorks knows the Frenet planning example in the "
      + "Automated Driving Toolbox by heart. If our answer sounds like that example, "
      + "we are one of forty teams. Our answer has to start at the corridor and the "
      + "risk field — the parts that example does not have."] });
}

// ============================================================ 5. divider dark
{
  const s = darkSlide();
  s.addText("Part 1", { x: M, y: 2.6, w: 6, h: 0.6, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 15, color: AMBER, charSpacing: 3 });
  s.addText("How the thing works", { x: M, y: 3.1, w: 11, h: 1.0, isTextBox: true,
    margin: 0, fontFace: "Arial", fontSize: 44, bold: true, color: PAPER });
  s.addText("Eight stages, twenty times a second. Learn the order — most judge "
    + "questions are really 'which stage handles that?'", {
      x: M, y: 4.2, w: 9.5, h: 0.7, isTextBox: true, margin: 0, fontFace: "Arial",
      fontSize: 13, color: "AEB4BF" });
  pipeline(s, 5.4, STAGES, { fontSize: 9.5 });
}

// ============================================================ 6. lanes are wrong
{
  const s = lightSlide("Why the lane is the wrong primitive",
    "This is the slide that makes us different from the other teams.", 3);
  table(s, ["A conventional stack", "What breaks on an Indian road", "SARATHI instead"], [
    ["Lane centreline as reference",
     "No markings, or markings nobody obeys",
     "Corridor: a dynamic program over free space, re-solved every tick"],
    ["Occupancy grid of obstacles",
     "A pothole and a child become the same wall",
     "Risk field: continuous, class-conditioned, harm-weighted"],
    ["One predicted path per object",
     "A rider filters or cuts in without warning",
     "Multi-modal intent, with covariance that grows with time"],
    ["Lane-change state machine",
     "Nothing to change lanes between",
     "Eight behaviours, including wrong-way evade"],
    ["Fixed safety envelope",
     "Nobody keeps a Western following distance",
     "RSS recalibrated to Indian gap acceptance, applied as a speed cap"],
  ], { x: M, y: 1.65, w: 12.23, colW: [3.1, 4.0, 5.13], fontSize: 11, rowH: 0.62 });
  card(s, { x: M, y: 5.6, w: 12.23, h: 1.1, fill: WASH,
    title: "If you remember one sentence from this slide",
    lines: ["We did not add Indian conditions to a Western planner. We removed the "
      + "assumption the Western planner is built on."] });
}

// ============================================================ 7. system map
{
  const s = lightSlide("The system map", "Every tick, in this order, at 20 Hz.", 4);
  pipeline(s, 1.55, STAGES, { fontSize: 9.5 });
  const notes = [
    ["Sense", "Camera, LiDAR, radar. Range noise, dropout, class confusion, occlusion by angular intervals. We never plan on ground truth."],
    ["Fuse", "Constant-velocity Kalman tracks. Class fused as log-odds so two sightings can disagree without flipping."],
    ["Predict", "Per agent, a distribution over manoeuvres — continue, cut in, filter, stop, dart, wrong-way — conditioned on its class."],
    ["Risk field", "Each hypothesis becomes an anisotropic kernel: a flat core the size of the body, a skirt sized by uncertainty, weighted by how badly hitting it would hurt."],
    ["Corridor", "A dynamic program over (distance, offset) finds the cheapest ribbon of free space ahead. This replaces the lane."],
    ["Behaviour", "Eight states decide the intent — follow, nudge, overtake, yield, creep, evade, stop. Maps 1:1 onto Stateflow."],
    ["Lattice", "Jerk-minimal quintic/quartic polynomials in the Frenet frame, sampled and scored against the risk field in space and time."],
    ["RSS + CBF", "The supervisor. Inverts the RSS condition in closed form to a safe speed, then a control-barrier filter caps it. Monotone: it can only slow us down."],
  ];
  notes.forEach((n, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, {
      x: M + col * 6.3, y: 2.3 + row * 1.16, w: 5.95, h: 1.06,
      title: `${i + 1}. ${n[0]}`, titleSize: 12.5, lines: [n[1]], fontSize: 11,
      fill: WASH,
    });
  });
  s.addNotes("Drill: someone names a hazard, you name the stage that handles it.");
}

// ============================================================ 8-15 stage slides
const STAGE_SLIDES = [
  {
    code: "sarathi/perception/sensors.py", owner: "B2",
    idx: 0, title: "Sense — and why we refuse ground truth",
    kicker: "The single most common way a hackathon demo lies to itself.",
    left: [
      { t: "Three sensors, three failure modes.", b: "Camera: good bearing, poor depth, class confusion. LiDAR: good geometry, no class. Radar: good speed, sparse." },
      { t: "Noise is anisotropic.", b: "Depth error and lateral error are not the same size — modelling them as one circle is the mistake that makes a demo look better than it is." },
      { t: "Occlusion is geometric.", b: "Each obstacle claims an angular interval; anything behind it is simply not seen. A bus hides three two-wheelers, exactly as it does in life." },
      { t: "Dropout and false classes are on.", b: "A cow is sometimes seen as a pedestrian for a few frames. The planner has to survive that." },
    ],
    right: { kind: "card", title: "What to say if a judge pushes",
      lines: ["“The planner has no access to the simulator's state. It sees tracks, "
        + "with the errors a real detector makes. That is why the risk field has an "
        + "uncertainty skirt at all — if we planned on ground truth, the skirt would "
        + "be decoration.”"] },
  },
  {
    code: "sarathi/perception/fusion.py", owner: "B2",
    idx: 1, title: "Fuse — tracks, not detections",
    kicker: "Where most of our early bugs lived.",
    left: [
      { t: "Constant-velocity Kalman filter", b: "per track; association by gated nearest neighbour on predicted position." },
      { t: "Class is fused in log-odds,", b: "not by averaging probabilities. Two confident, disagreeing sightings must not average to 'no idea'." },
      { t: "Merging is size-aware.", b: "An early bug let a bus's gate swallow two two-wheelers riding beside it. Merge now needs class agreement and a length check." },
      { t: "Heading is only trusted above 1 m/s.", b: "Below that, heading from motion is noise — the bug that made our risk kernels spin. Slide 21 tells that story; it is a good one." },
    ],
    right: { kind: "card", title: "Numbers to have ready",
      lines: [{ t: "median position error ≈ 1.2 m at typical range", bullet: true },
              { t: "≈ 92% class accuracy after fusion", bullet: true },
              { t: "no duplicate tracks in the regression suite", bullet: true }] },
  },
  {
    code: "sarathi/prediction/intent.py", owner: "B2",
    idx: 2, title: "Predict — one object, several futures",
    kicker: "A single predicted path is a lie in mixed traffic.",
    left: [
      { t: "Six manoeuvres:", b: "continue, cut in, filter between vehicles, stop, dart across, ride the wrong way." },
      { t: "Priors are class-conditioned.", b: "A two-wheeler filters; a bus does not. A pedestrian darts; a barricade never moves." },
      { t: "Covariance grows with time", b: "and is clamped to what the object can physically reach — a stationary car cannot be 1.8 m sideways in a second." },
      { t: "Above a probability threshold", b: "a hypothesis becomes a hard constraint instead of a cost. That threshold is on screen and adjustable." },
    ],
    right: { kind: "card", title: "The honest caveat",
      lines: ["Our priors are hand-built from observed Indian driving, not learned "
        + "from a trajectory dataset. Say that before a judge asks. Then say what we "
        + "would do with data: fit the priors per class from IDD tracks."] },
  },
  {
    code: "sarathi/planning/risk.py", owner: "B1",
    idx: 3, title: "The risk field — the centre of the whole idea",
    kicker: "If you only understand one stage deeply, make it this one.",
    left: [
      { t: "Continuous, not a grid.", b: "Every point in (s, d, t) has a risk value, so the planner can trade a little risk for a lot of progress instead of hitting a wall." },
      { t: "Class-conditioned.", b: "The kernel around a cow is not the kernel around a barricade — different size, different anisotropy, different growth in time." },
      { t: "Harm-weighted.", b: "A pedestrian costs more than sheet metal. This is a deliberate ethical choice and we should say so out loud." },
      { t: "Flat-top kernels.", b: "The core is the config-space footprint — inside it, risk is saturated. The skirt is uncertainty. That shape is why clearance behaves sanely." },
      { t: "Time-indexed.", b: "The field at t+2 s is built from predictions, so the lattice is scored against where things will be, not where they were." },
    ],
    right: { kind: "image", file: "fig-bev.png",
      caption: "Red is the risk field at the current instant. The grey fan is every "
        + "trajectory scored this tick; amber is the one chosen.",
      footer: "Building the whole field costs 18 ms a tick — it was 116 ms before "
        + "we vectorised it." },
  },
  {
    code: "sarathi/planning/corridor_path.py · world/corridor.py", owner: "B1",
    idx: 4, title: "The corridor — what replaced the lane",
    kicker: "A dynamic program, not a rule.",
    left: [
      { t: "State is (distance along, lateral offset).", b: "Cost combines risk, deviation, and how much the offset has to change." },
      { t: "The verge is drivable,", b: "at a price. A shoulder of 0.9 m is part of the search space, because on a village road it is part of the road." },
      { t: "A pothole is a cost bowl,", b: "not an obstacle. You can cross it slowly; the planner decides whether that is worth it." },
      { t: "Output is a reference line", b: "plus a clearance profile and which side is free — the behaviour layer needs all three." },
      { t: "This is what makes markings irrelevant.", b: "There is no branch anywhere in the code for 'markings missing'." },
    ],
    right: { kind: "card", title: "Judge question you will get",
      lines: ["“Isn't your corridor just a lane you computed?” — Yes, and that "
        + "is the point: it is derived from free space every tick instead of read from "
        + "paint. When a bus stops in it, the corridor bends around the bus; a lane "
        + "cannot."] },
  },
  {
    code: "sarathi/planning/behaviour.py", owner: "B1",
    idx: 5, title: "Behaviour — eight states, and why each exists",
    kicker: "Maps 1:1 onto Stateflow, which is exactly what the sponsor wants to see.",
    grid: [
      ["CRUISE", "free road — hold the desired speed"],
      ["FOLLOW", "matched to a leader's speed and gap"],
      ["NUDGE", "squeezing past an obstruction, speed reduced"],
      ["OVERTAKE", "leader is slow and there is width to pass"],
      ["YIELD", "giving way at a junction or to a vulnerable road user"],
      ["CREEP", "dense traffic — walking pace, maximum alertness"],
      ["WRONG_WAY_EVADE", "a rider is coming head-on on our side"],
      ["EMERGENCY_STOP", "time-to-collision below threshold — stop now"],
    ],
    note: "Every transition has a dwell time so the vehicle cannot chatter between "
      + "states. Dwell is one of the thresholds exposed on screen.",
  },
  {
    code: "sarathi/planning/lattice.py", owner: "B1",
    idx: 6, title: "The lattice — jerk-minimal, and scored in time",
    kicker: "The part that looks like the textbook. Do not stop at the textbook.",
    left: [
      { t: "Quintic laterally, quartic longitudinally,", b: "in the Frenet frame of the corridor reference — the classical jerk-minimal form (Werling)." },
      { t: "We sample terminal states,", b: "not controls: lateral offsets across the corridor, terminal speeds, and durations." },
      { t: "Scoring is against the time-indexed risk field,", b: "plus jerk, curvature, offset and speed shortfall. Weights are on screen." },
      { t: "Batched in NumPy.", b: "Evaluating candidates one at a time cost 71 ms; batching brought the same work to 7.5 ms." },
      { t: "Infeasible is not the same as unsafe.", b: "Candidates that break curvature or acceleration limits are dropped before scoring; the count is on screen as 'feasible n/m'." },
    ],
    right: { kind: "card", title: "Where we know it is weak",
      lines: ["From a standstill the sampled terminal speeds are poorly matched to what "
        + "2 m/s² can actually deliver over the sampled durations, so the fan is "
        + "narrower than it should be exactly when we need it widest. That is a "
        + "sampling problem, not a safety one, and it is the next thing we fix."] },
  },
  {
    code: "sarathi/safety/rss.py", owner: "B1",
    idx: 7, title: "RSS + control barrier — the supervisor",
    kicker: "The answer to 'how do you know it is safe?'",
    left: [
      { t: "RSS gives a formal condition", b: "for a safe longitudinal gap given both speeds, reaction time and braking limits." },
      { t: "We invert it in closed form", b: "to get a safe speed directly, instead of proposing a speed and testing it." },
      { t: "Parameters are recalibrated for India.", b: "Western reaction time and gap acceptance would make the vehicle undriveable here — and pretending otherwise would be dishonest." },
      { t: "A control-barrier filter caps the result,", b: "so the commanded speed can only ever be reduced by the supervisor." },
      { t: "Monotone by construction.", b: "That single property is why the supervisor cannot cause a collision, and it is the sentence to say." },
    ],
    right: { kind: "card", title: "The hard question, and the answer",
      lines: ["“RSS assumes everyone else follows RSS. Nobody here does.” — "
        + "Correct. We use RSS as a lower bound on our own behaviour, not as a "
        + "prediction of theirs. Their behaviour is handled in the prediction stage, "
        + "which assumes they will do the worst plausible thing."] },
  },
];

STAGE_SLIDES.forEach((sp) => {
  stageNo += 1;
  const s = lightSlide(sp.title, sp.kicker, 4 + stageNo);
  pipeline(s, 1.42, STAGES, { fontSize: 8.5, h: 0.4, active: sp.idx });
  if (sp.grid) {
    sp.grid.forEach((g, i) => {
      const col = i % 2, row = Math.floor(i / 2);
      card(s, {
        x: M + col * 6.3, y: 2.15 + row * 1.02, w: 5.95, h: 0.88,
        title: g[0], titleSize: 12, lines: [g[1]], fontSize: 11,
        fill: i === 6 || i === 7 ? "FBEEE9" : WASH,
        titleColor: i === 6 || i === 7 ? RED : ASPHALT,
      });
    });
    caption(s, M, 6.4, 12.23, sp.note + `   ·   In the code: ${sp.code} (ask ${sp.owner}).`);
  } else {
    bullets(s, sp.left, { x: M, y: 2.15, w: 6.55, h: 3.4, fontSize: 13, gap: 10 });
    if (sp.right.kind === "image") {
      s.addImage({ path: path.join(ART, sp.right.file), x: 7.4, y: 2.15, w: 5.38,
                   h: 5.38 / 2.2 });
      caption(s, 7.4, 4.68, 5.38, sp.right.caption);
      card(s, { x: 7.4, y: 5.05, w: 5.38, h: 0.7, fill: WASH, fontSize: 11,
                lines: [sp.right.footer || ""], valign: "middle" });
    } else {
      card(s, { x: 7.4, y: 2.15, w: 5.38, fill: WASH, autoH: true, minH: 1.8,
                title: sp.right.title, lines: sp.right.lines, fontSize: 12.5 });
    }
    const bandY = 5.85;   // fixed, so the band lands in the same place on all eight
    s.addShape(pres.ShapeType.roundRect, { x: M, y: bandY, w: 12.23, h: 0.85,
      rectRadius: 0.06, fill: { color: ASPHALT }, line: { width: 0 } });
    s.addText([
      { text: "In the code   ", options: { bold: true, color: AMBER, fontSize: 10.5 } },
      { text: sp.code + "        ", options: { color: "E6E8EC", fontSize: 11 } },
      { text: "Ask   ", options: { bold: true, color: AMBER, fontSize: 10.5 } },
      { text: sp.owner, options: { color: "E6E8EC", fontSize: 11 } },
    ], { x: M + 0.2, y: bandY, w: 11.83, h: 0.85, isTextBox: true, margin: 0,
         fontFace: "Arial", valign: "middle" });
  }
});

// ============================================================ traffic model
{
  const s = lightSlide("The traffic around us is the other half of the work",
    "A planner is only as honest as the traffic it is tested against.", 13);
  bullets(s, [
    { t: "Non-lane-based car following (NLB-IDM).", b: "Agents form dynamic pseudo-lanes: the interaction strength between two vehicles depends on how much their bodies overlap laterally, not on which lane they are in." },
    { t: "Class-specific lateral clearance.", b: "A two-wheeler will pass a bus with 0.4 m. A car will not." },
    { t: "Gap seeking.", b: "Riders actively hunt for gaps rather than queueing — which is why our market and bus-stop scenarios are hard." },
    { t: "Twelve road-user classes,", b: "car, bus, truck, auto-rickshaw, two-wheeler, bicycle, pedestrian, pushcart, cattle, stray dog, barricade, parked vehicle." },
    { t: "Wrong-way riders are a policy, not a script.", b: "They hug the verge, crawl, and seek gaps against the flow — and they do not politely turn around when we approach." },
  ], { x: M, y: 1.55, w: 6.9, fontSize: 12, gap: 9 });
  card(s, { x: 7.75, y: 1.55, w: 5.03, h: 2.35, fill: ASPHALT, color: "E6E8EC",
    titleColor: AMBER, title: "Why this matters for marks",
    lines: ["If our traffic were polite, our planner would look brilliant and mean "
      + "nothing. The scenarios are adversarial on purpose, and we should invite the "
      + "judge to make them worse."] });
  s.addImage({ path: path.join(ART, "live-cow.png"), x: 7.75, y: 4.1, w: 5.03,
               h: 5.03 / 1.6 });
  caption(s, 7.75, 7.05, 5.03, "Cattle: high variance, low speed, no compliance.");
}

// ============================================================ divider: demo
{
  const s = darkSlide();
  s.addText("Part 2", { x: M, y: 2.6, w: 6, h: 0.6, isTextBox: true, margin: 0,
    fontFace: "Arial", fontSize: 15, color: AMBER, charSpacing: 3 });
  s.addText("Running it in front of a judge", { x: M, y: 3.1, w: 11.5, h: 1.0,
    isTextBox: true, margin: 0, fontFace: "Arial", fontSize: 44, bold: true,
    color: PAPER });
  s.addText("The demo is the argument. Everything else is supporting evidence.", {
    x: M, y: 4.2, w: 9.5, h: 0.5, isTextBox: true, margin: 0, fontFace: "Arial",
    fontSize: 13, color: "AEB4BF" });
}

// ============================================================ scenarios
{
  const s = lightSlide("The scenario library — what each one proves", "", 14);
  const rows = [
    ["Village road, unmarked", "required", "No markings at all; narrow, two-way"],
    ["Cattle crossing", "required", "Sudden high-variance hazard at speed"],
    ["Dense market", "required", "Encroachment, pedestrians, walking pace"],
    ["Highway merge", "required", "Large speed differential"],
    ["Unsignalled junction", "required", "Negotiation with no right of way"],
    ["Narrow bridge, oncoming", "ours", "Committed oncoming bus, no room for both"],
    ["Bus stop overtake", "ours", "Overtaking into oncoming traffic"],
    ["Night, wrong-way rider", "ours", "Degraded sensing plus a head-on rider"],
    ["Construction diversion", "ours", "Barricade slalom, corridor bent hard"],
    ["School zone", "ours", "Restraint: many VRUs, low speed, no heroics"],
  ];
  const KEYS = ["village_road_unmarked", "cattle_crossing_sudden",
    "market_dense_mixed", "highway_merge_slow", "urban_intersection_unsignalled",
    "narrow_bridge_oncoming", "bus_stop_overtake", "night_highway_wrongway",
    "construction_diversion", "school_zone_pedestrians"];
  const withProgress = rows.map((r, i) => {
    const v = OURS.by_scenario[KEYS[i]];
    return [...r, `${pct(v.mean_progress)}  ·  ${v.collision_free}/${v.runs}`];
  });
  table(s, ["Scenario", "Origin", "What it is there to prove",
            "Progress · clean"], withProgress, {
    x: M, y: 1.5, w: 8.9, colW: [2.3, 0.85, 4.15, 1.6], fontSize: 10.5, rowH: 0.42 });
  caption(s, M, 6.3, 8.9, `Mean route progress and collision-free runs out of `
    + `${SEEDS}, from the same campaign as slide 21.`);
  card(s, { x: 9.7, y: 1.5, w: 3.08, h: 2.6, fill: WASH,
    title: "Five of these are ours, not theirs",
    lines: ["The problem statement names five situations. We added five harder ones "
      + "and report them the same way. Volunteering a scenario you do not always win "
      + "is worth more than a clean sweep of easy ones."] });
  card(s, { x: 9.7, y: 4.3, w: 3.08, h: 2.4, fill: ASPHALT, color: "E6E8EC",
    titleColor: AMBER, title: "One scenario file, two runtimes",
    lines: ["The same YAML drives the Python stack and the MATLAB/Simulink port. "
      + "When a judge asks whether the MATLAB side is real, that is the answer: it "
      + "runs the same scenario definitions, not a re-implementation."] });
}

// ============================================================ console tour
{
  const s = lightSlide("Mission Control, part by part",
    "Know every panel. A judge will point at one and ask.", 15);
  s.addImage({ path: path.join(ART, "fig-console.png"), x: M, y: 1.45, w: 7.9,
               h: 7.9 / 1.684 });
  const items = [
    { t: "Behaviour card", b: "which of the eight states we are in, and the reason for it" },
    { t: "Telemetry", b: "speed, target, min TTC, path clearance, replan time, plan risk" },
    { t: "Drop a road user", b: "click to place, drag to move, shift-click to remove — live" },
    { t: "Thresholds", b: "every tunable in the system, changeable while it drives" },
    { t: "Ablations", b: "switch off the risk field, the prediction or RSS and watch it degrade" },
    { t: "Chips, bottom left", b: "tracked objects, feasible candidates, margin relief, safety cap" },
  ];
  bullets(s, items, { x: 8.7, y: 1.5, w: 4.1, fontSize: 11, gap: 8 });
  caption(s, M, 6.25, 7.9,
    "The whole interface is one HTML file served by the same process that runs the "
    + "simulation — nothing to install, nothing to build.");
}

// ============================================================ runbook
{
  const s = lightSlide("The demo runbook", "Rehearse this until it is boring.", 16);
  const steps = [
    ["0", "Before they arrive", "uv run sarathi serve. Check the browser is on the narrow bridge scenario, follow-cam on, 1× speed. Have a second terminal ready."],
    ["1", "Frame it (15 s)", "“This is running live. Nothing is pre-recorded, and the planner has no knowledge of anything we do next.”"],
    ["2", "Let it drive (20 s)", "Point at the behaviour card as it changes. Say the state name out loud when it switches — it makes the machine legible."],
    ["3", "Hand them the mouse", "“Put something in our way.” This is the moment that wins the room. Do not narrate over it."],
    ["4", "Break a threshold (30 s)", "Raise the clearance margin until the vehicle refuses to pass, then lower it. Shows the trade-off is real and ours."],
    ["5", "Ablate (30 s)", "Turn the risk field off. It degrades to a lane follower and starts failing. Turn it back on."],
    ["6", "Land it (15 s)", "Back to the numbers slide. “That behaviour is what these numbers measure, across ten scenarios and three seeds.”"],
  ];
  let y = 1.5;
  steps.forEach((st) => {
    s.addShape(pres.ShapeType.ellipse, { x: M, y: y + 0.02, w: 0.38, h: 0.38,
      fill: { color: st[0] === "0" ? MUTED : AMBER } });
    s.addText(st[0], { x: M, y: y + 0.02, w: 0.38, h: 0.38, align: "center",
      valign: "middle", fontFace: "Arial", fontSize: 12, bold: true,
      color: st[0] === "0" ? "FFFFFF" : ASPHALT, isTextBox: true, margin: 0 });
    s.addText([
      { text: st[1] + "  ", options: { bold: true, fontSize: 12.5, color: ASPHALT } },
      { text: st[2], options: { fontSize: 11.5, color: INK } },
    ], { x: M + 0.55, y, w: 7.7, h: 0.78, isTextBox: true, margin: 0,
         fontFace: "Arial", valign: "top", lineSpacingMultiple: 1.05 });
    y += 0.79;
  });
  card(s, { x: 8.95, y: 1.5, w: 3.83, h: 2.6, fill: "FBEEE9", titleColor: RED,
    title: "If it goes wrong",
    lines: [{ t: "Vehicle stuck → Reset vehicle button, keep talking.", bullet: true },
            { t: "Browser dead → reload; the session survives.", bullet: true },
            { t: "Server dead → second terminal, same command.", bullet: true },
            { t: "Everything dead → the recorded replay page, and say so.", bullet: true }] });
  card(s, { x: 8.95, y: 4.3, w: 3.83, h: 2.3, fill: WASH,
    title: "Who does what",
    lines: [{ t: "B2 drives the machine.", bullet: true },
            { t: "O4 narrates and watches the clock.", bullet: true },
            { t: "B1 answers algorithm questions only.", bullet: true },
            { t: "O3 has the numbers open on a second screen.", bullet: true }] });
}

// ============================================================ numbers
{
  const s = lightSlide("The numbers we quote, and where they come from",
    `Every figure below is produced by scripts/benchmark.py — ${SEEDS} seeds × `
    + `${OURS.scenarios} scenarios per controller, identical seeds for both.`, 17);
  const cats = Object.keys(OURS.by_scenario);
  const pretty = (k) => k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  s.addChart(pres.ChartType.bar, [
    { name: "Lane-following baseline",
      labels: cats.map(pretty),
      values: cats.map((k) => +(BASE.by_scenario[k].mean_progress * 100).toFixed(1) ) },
    { name: "SARATHI",
      labels: cats.map(pretty),
      values: cats.map((k) => +(OURS.by_scenario[k].mean_progress * 100).toFixed(1)) },
  ], {
    x: M, y: 1.75, w: 7.6, h: 4.6, barDir: "bar", barGrouping: "clustered",
    chartColors: ["C2C7D0", "1F3864"], showLegend: true, legendPos: "t",
    legendFontSize: 9, showValue: true, dataLabelPosition: "outEnd",
    dataLabelFontSize: 8, dataLabelColor: MUTED, dataLabelFormatCode: '0"%"',
    catAxisLabelFontSize: 9, valAxisLabelFontSize: 9, valAxisMaxVal: 100,
    valAxisMinVal: 0, catGridLine: { style: "none" },
    valGridLine: { color: "E9E9E9", size: 0.5 },
    catAxisLabelColor: INK, valAxisLabelColor: MUTED, fontFace: "Arial",
  });
  caption(s, M, 6.4, 7.6, "Mean route progress per scenario. Higher is better.");
  stat(s, { x: 8.5, y: 1.70, w: 2.1,
    value: `${OURS.collision_free}/${OURS.runs}`, label: "our runs collision-free" });
  stat(s, { x: 10.7, y: 1.70, w: 2.1,
    value: `${BASE.collision_free}/${BASE.runs}`, label: "baseline, same seeds",
    color: MUTED });
  stat(s, { x: 8.5, y: 3.15, w: 2.1, value: pct(OURS.mean_progress),
    label: "mean route progress" });
  stat(s, { x: 10.7, y: 3.15, w: 2.1, value: pct(BASE.mean_progress),
    label: "baseline, same seeds", color: MUTED });
  stat(s, { x: 8.5, y: 4.60, w: 2.1, value: `${OURS.worst_p95_ms.toFixed(0)} ms`,
    label: "worst 95th %ile replan" });
  stat(s, { x: 10.7, y: 4.60, w: 2.1, value: "20 Hz", label: "closed loop, no GPU" });
  card(s, { x: 8.5, y: 5.80, w: 4.28, h: 1.25, fill: WASH,
    title: "Rule for all of us",
    lines: ["If a number is not on this slide, do not say it. If a judge asks for "
      + "one we do not have, say we will measure it."] });
}

// ============================================================ weaknesses
{
  const s = lightSlide("What is still wrong — say it before they find it",
    "Volunteered limitations read as rigour. Discovered ones read as spin.", 18);
  bullets(s, [
    { t: "It is too cautious in dense traffic.", b: `Mean route progress across all runs is ${pct(OURS.mean_progress)}. On the hardest scenarios the vehicle spends much of the run stopped. We know why: from a standstill the sampled terminal speeds do not match what the acceleration limit can deliver over the sampled horizons, so the candidate fan is too narrow exactly when it needs to be wide.` },
    { t: `Not every run is collision-free: ${OURS.collision_free} of ${OURS.runs}.`, b: "The failures cluster in the scenarios with the most vulnerable road users. We report them rather than dropping those scenarios." },
    { t: "Prediction priors are hand-built,", b: "not fitted to data. Defensible, but it is the first thing a research judge will push on." },
    { t: "The MATLAB port is a two-tier design.", b: "Full toolchain where licensed, base products otherwise. Be straight about which tier we are demonstrating." },
  ], { x: M, y: 1.55, w: 7.5, h: 3.4, fontSize: 12.5, gap: 10 });
  card(s, { x: M, y: 5.05, w: 7.5, h: 1.75, fill: WASH,
    title: "If a judge says “so it doesn't really work yet”",
    fontSize: 12.5,
    lines: ["“It drives every scenario and it is collision-free in most runs. Where "
      + "it is weak, it is weak by being slow rather than by being unsafe — which is "
      + "the failure mode we chose. We can show you the exact cause on the screen "
      + "right now, and the fix is a sampling change, not a rewrite.”"] });
  card(s, { x: 8.35, y: 1.55, w: 4.43, h: 3.4, fill: ASPHALT, color: "E6E8EC",
    titleColor: AMBER, title: "The bug worth telling them about",
    lines: ["Our risk kernels used to rotate wildly around stationary vehicles. The "
      + "predicted heading came from the gradient of a path that was barely moving — "
      + "so it was noise. A bus sitting at 4° was being modelled at −94°, then −180°, "
      + "then +58°.",
      "Fix: trust measured heading only above 1 m/s, hold the last good estimate below "
      + "it, and fall back to the road heading for anything that has never moved.",
      "Result: bus-stop progress 41% → 56%, and two collisions went away. It is a good "
      + "story because it shows we debug by measuring, not by tuning."] });
  card(s, { x: 8.35, y: 5.15, w: 4.43, h: 1.5, fill: "FBEEE9", titleColor: RED,
    title: "Never say",
    lines: [{ t: "“It always works.”", bullet: true },
            { t: "“That's just a simulation artefact.”", bullet: true },
            { t: "“We'll fix that later.” — say when, and how.", bullet: true }] });
}

// ============================================================ Q&A drill
{
  const s = lightSlide("Q&A drill — the twelve questions we will actually get",
    "One sentence each. Learn the shape of the answer, not the words.", 19);
  const qa = [
    ["Isn't this the Frenet planner from the ADT example?",
     "That example is one of our eight stages; the corridor and the risk field are what it does not have."],
    ["Why not end-to-end learning?",
     "No Indian dataset large enough to be safe, and no way to explain a failure to a regulator."],
    ["Isn't your risk field just a blurred occupancy grid?",
     "It is class-conditioned, harm-weighted and indexed by time — a grid has none of those three."],
    ["What if perception is wrong?",
     "It is wrong, deliberately, in every run: noise, dropout, occlusion and class confusion are all on."],
    ["RSS assumes others follow RSS.",
     "We use it as a bound on our own behaviour; theirs is handled by worst-plausible-case prediction."],
    ["Why does it stop so often?",
     "It is too cautious, we can measure exactly how much, and we know the sampling cause — slide 21."],
    ["Would this run on a real vehicle?",
     "20 Hz on a laptop CPU with no GPU; the gap to a vehicle is sensors and integration, not compute."],
    ["What is Indian about it, specifically?",
     "Twelve road-user classes, non-lane-based following, wrong-way riders, cattle, and gap acceptance recalibrated."],
    ["Where is the MATLAB?",
     "The behaviour machine is Stateflow-shaped by design and the same scenario files drive both runtimes."],
    ["How do we know your numbers?",
     "One committed script produces them; both controllers see identical seeds and identical sensor noise."],
    ["What broke, and how did you find it?",
     "The rotating-kernel bug — found by measuring predicted heading against actual, not by staring at it."],
    ["What would you do with three more months?",
     "Fit prediction priors to IDD, fix the standstill sampling, and put the whole loop in Simulink."],
  ];
  let y = 1.5;
  qa.forEach((row, i) => {
    const col = i % 2, r = Math.floor(i / 2);
    s.addText([
      { text: row[0] + "\n", options: { bold: true, fontSize: 11, color: ASPHALT } },
      { text: row[1], options: { fontSize: 10.5, color: INK } },
    ], { x: M + col * 6.3, y: 1.5 + r * 0.93, w: 5.95, h: 0.88, isTextBox: true,
         margin: 0, fontFace: "Arial", valign: "top", lineSpacingMultiple: 1.03 });
  });
  void y;
  s.addNotes("Drill in pairs. One reads the question, the other answers in one "
    + "sentence with no filler. Anyone who needs two sentences has not learned it.");
}

// ============================================================ roles
{
  const s = lightSlide("Who owns what, and when it is due",
    "There are no fillers. Roughly half the marked deliverable is not planning code.", 20);
  table(s, ["Who", "Role", "Owns", "Codes?"], [
    ["B1", "Planning lead", "planning/, safety/, the lattice and the supervisor", "Yes"],
    ["B2", "Perception + platform", "perception/, prediction/, Mission Control, MATLAB port", "Yes"],
    ["O1", "Licence + RoadRunner", "MathWorks liaison, then the two RoadRunner scenes", "No"],
    ["O2", "Scenario director", "Every scenario spec — geometry, agents, realism", "YAML"],
    ["O3", "Data + evidence", "IDD taxonomy, the benchmark campaign, every table and plot", "Scripts"],
    ["O4", "Narrative", "Report, decks, video, and the pitch itself", "No"],
  ], { x: M, y: 1.55, w: 8.1, colW: [0.7, 1.9, 4.4, 1.1], fontSize: 10.5, rowH: 0.5 });
  card(s, { x: 8.9, y: 1.55, w: 3.88, h: 2.5, fill: WASH,
    title: "Critical path",
    lines: [{ t: "O2's scenarios unblock B1, O1 and O3 — they come first.", bullet: true },
            { t: "O1 starts the licence request today, and a 30-day trial in parallel.", bullet: true },
            { t: "Nobody waits on anybody else to start.", bullet: true }] });
  card(s, { x: 8.9, y: 4.25, w: 3.88, h: 2.4, fill: ASPHALT, color: "E6E8EC",
    titleColor: AMBER, title: "Rehearsal, non-negotiable",
    lines: [{ t: "Two full run-throughs with the timer, out loud.", bullet: true },
            { t: "One where someone deliberately breaks the demo.", bullet: true },
            { t: "One Q&A round where nobody is allowed to say “um, so basically”.", bullet: true }] });
}

// ============================================================ glossary
{
  const s = lightSlide("Glossary — so nobody freezes mid-sentence", "", 21);
  const g = [
    ["Frenet frame", "Coordinates along a reference line: s is distance along, d is offset sideways."],
    ["Jerk-minimal", "The polynomial that gets from one state to another with the least rate-of-change of acceleration. Comfort, mathematically."],
    ["Lattice", "The fan of candidate trajectories we generate and score each tick."],
    ["Corridor", "The ribbon of drivable free space our dynamic program finds. Our replacement for the lane."],
    ["Risk field", "Continuous cost over space and time; class-conditioned and harm-weighted."],
    ["Anisotropic kernel", "A risk blob that is not a circle — longer along the direction of travel than across it."],
    ["RSS", "Responsibility-Sensitive Safety: a formal minimum-gap condition. We invert it to a safe speed."],
    ["CBF", "Control barrier function: a filter that guarantees we stay inside a safe set."],
    ["NLB-IDM", "Non-lane-based intelligent driver model — car following without lanes."],
    ["Ablation", "Switching a component off to show what it was contributing."],
    ["TTC", "Time to collision, on current relative motion."],
    ["Chaos", "Our scenario dial: how aggressive and disorderly the surrounding traffic is."],
    ["Log-odds", "Adding evidence in log space, so two confident observations can disagree without averaging to nothing."],
    ["Configuration space", "The obstacle grown by our own footprint, so the vehicle can be planned for as a point."],
  ];
  g.forEach((row, i) => {
    const col = i % 2, r = Math.floor(i / 2);
    s.addText([
      { text: row[0] + "  ", options: { bold: true, color: ASPHALT, fontSize: 11.5 } },
      { text: row[1], options: { color: INK, fontSize: 11 } },
    ], { x: M + col * 6.3, y: 1.35 + r * 0.79, w: 5.95, h: 0.76, isTextBox: true,
         margin: 0, fontFace: "Arial", valign: "top", lineSpacingMultiple: 1.03 });
  });
}

// ============================================================ closing (dark)
{
  const s = darkSlide();
  s.addText("The one thing to remember", { x: M, y: 2.2, w: 11, h: 0.5,
    isTextBox: true, margin: 0, fontFace: "Arial", fontSize: 15, color: AMBER,
    charSpacing: 3 });
  s.addText("We are not showing a car that avoids obstacles.\n"
    + "We are showing a planner that does not need a lane.", {
      x: M, y: 2.85, w: 11.5, h: 1.6, isTextBox: true, margin: 0,
      fontFace: "Arial", fontSize: 32, bold: true, color: PAPER,
      lineSpacingMultiple: 1.15 });
  s.addText("Everything else — the risk field, the eight behaviours, RSS, the "
    + "scenarios — exists to make that one claim survive contact with a judge.", {
      x: M, y: 4.6, w: 9.8, h: 0.8, isTextBox: true, margin: 0, fontFace: "Arial",
      fontSize: 13, color: "AEB4BF", lineSpacingMultiple: 1.2 });
  s.addText("github.com/Paragraph1148/automated-driving   ·   uv run sarathi serve", {
    x: M, y: 6.4, w: 11, h: 0.4, isTextBox: true, margin: 0, fontFace: "Arial",
    fontSize: 12, color: AMBER });
}

const out = path.join(__dirname, "SARATHI_internal_briefing.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("wrote " + out));
