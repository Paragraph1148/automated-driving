/**
 * Rishabh Singh Kushwaha — resume, rebuilt from the existing PDF's layout.
 *
 * The template is A4, Arial (Liberation Sans in the original), 0.75in side
 * margins, a 17pt centred name, 11pt ruled section headers and 10pt body. Only
 * three projects are kept — SARATHI, BRAHMO, the chat engine — and the skills
 * list is cut back to what those three actually use.
 *
 *   cd resume && node build_resume.js
 */
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, ExternalHyperlink, AlignmentType,
  BorderStyle, TabStopType, LevelFormat, Tab, convertInchesToTwip,
} = require("docx");

const FONT = "Arial";
const INK = "000000";
const LINK = "0B4F9E";

const PAGE = {
  margin: {                       // 0.75in sides, 0.5in top and bottom
    top: convertInchesToTwip(0.5),
    bottom: convertInchesToTwip(0.5),
    left: convertInchesToTwip(0.75),
    right: convertInchesToTwip(0.75),
  },
  size: { width: 11906, height: 16838 },     // A4, DXA
};

const half = (pt) => pt * 2;      // docx sizes are half-points

// docx-js's TabStopPosition.MAX is sized for US Letter with 1in margins; on A4
// with 0.75in margins it lands half an inch short of the right edge.
const RIGHT_TAB = Math.round((11906 - 2 * convertInchesToTwip(0.75)));

function run(text, opt = {}) {
  return new TextRun({
    text, font: FONT, size: half(opt.size || 10), bold: !!opt.bold,
    italics: !!opt.italic, color: opt.color || INK,
  });
}

function link(text, url, size = 9.5) {
  return new ExternalHyperlink({
    link: url,
    children: [new TextRun({
      text, font: FONT, size: half(size), color: LINK, underline: {},
    })],
  });
}

/** A ruled section header: SUMMARY, PROJECTS, … */
function section(title) {
  return new Paragraph({
    spacing: { before: 150, after: 80 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "808080", space: 2 } },
    children: [run(title, { size: 11, bold: true })],
  });
}

/** Project heading: title on the left, a repo link pinned to the right margin. */
function projectTitle(title, url) {
  const kids = [run(title, { size: 10.5, bold: true })];
  if (url) {
    kids.push(new TextRun({ children: [new Tab()], font: FONT, size: half(9.5) }));
    kids.push(link("GitHub", url));
  }
  return new Paragraph({
    spacing: { before: 105, after: 10 },
    tabStops: [{ type: TabStopType.RIGHT, position: RIGHT_TAB }],
    children: kids,
  });
}

const stack = (text) => new Paragraph({
  spacing: { after: 50 },
  children: [run(text, { size: 9.5, italic: true, color: "3A3A3A" })],
});

const bullet = (text) => new Paragraph({
  numbering: { reference: "resume-bullets", level: 0 },
  spacing: { after: 22, line: 250, lineRule: "auto" },
  children: [run(text, { size: 10 })],
});

/** A "Label: value" row in the skills block. */
const skill = (label, value) => new Paragraph({
  spacing: { after: 30 },
  indent: { left: 0 },
  children: [run(label + "  ", { size: 10, bold: true }), run(value, { size: 10 })],
});

/** An education row: degree left, years pinned right. */
const education = (degree, years) => new Paragraph({
  spacing: { after: 40 },
  tabStops: [{ type: TabStopType.RIGHT, position: RIGHT_TAB }],
  children: [run(degree, { size: 10 }),
             new TextRun({ children: [new Tab()], font: FONT, size: half(10) }),
             run(years, { size: 10 })],
});

const doc = new Document({
  numbering: {
    config: [{
      reference: "resume-bullets",
      levels: [{
        level: 0, format: LevelFormat.BULLET, text: "•",
        alignment: AlignmentType.LEFT,
        style: {
          paragraph: { indent: { left: 200, hanging: 200 } },
          run: { font: FONT, size: half(10) },
        },
      }],
    }],
  },
  styles: {
    default: { document: { run: { font: FONT, size: half(10) } } },
  },
  sections: [{
    properties: { page: PAGE },
    children: [
      // ------------------------------------------------------------ header
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 40 },
        children: [run("Rishabh Singh Kushwaha", { size: 17, bold: true })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 30 },
        children: [run(
          "Raipur, Chhattisgarh, India   |   Phone: 7587199636   |   "
          + "Email: rishabh311002@gmail.com", { size: 9.5 })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 40 },
        children: [
          link("GitHub", "https://github.com/paragraph1148"),
          run("   |   ", { size: 9.5 }),
          link("LinkedIn",
               "https://linkedin.com/in/rishabh-singh-kushwaha-38266a133"),
          run("   |   ", { size: 9.5 }),
          link("LeetCode", "https://leetcode.com/u/importer"),
        ],
      }),

      // ----------------------------------------------------------- summary
      section("SUMMARY"),
      new Paragraph({
        spacing: { after: 40, line: 250, lineRule: "auto" },
        children: [run(
          "Backend engineer who takes a system end to end — the algorithm, the API "
          + "around it, and the measurement harness that proves it works. Recent work: "
          + "a real-time motion-planning stack for unstructured roads, a clinical "
          + "decision-support product whose safety-critical path never depends on "
          + "model output, and a scalable real-time chat backend.", { size: 10 })],
      }),

      // ---------------------------------------------------------- projects
      section("PROJECTS"),

      projectTitle("SARATHI — Adaptive Path Planning for Unstructured Indian Roads",
                   "https://github.com/Paragraph1148/automated-driving"),
      stack("Python, NumPy, SciPy, WebSockets, HTML5 Canvas, pytest, Playwright, uv"),
      bullet("Built a closed-loop autonomous-driving stack — simulated camera, LiDAR "
        + "and radar with occlusion and class confusion, Kalman tracking, multi-modal "
        + "intent prediction, and a jerk-minimal Frenet trajectory lattice — running "
        + "at 20 Hz on a laptop CPU, median 95th-percentile replan 46 ms."),
      bullet("Replaced the lane centreline with a drivable corridor solved by dynamic "
        + "programming over free space, and the occupancy grid with a class- and "
        + "harm-weighted risk field; lane markings are never read."),
      bullet("Diagnosed the planner's failure to pull away from a stopped obstruction "
        + "by counting rejected candidates rather than tuning thresholds — 1 of 131 was "
        + "usable — then sampled inside the reachable set and re-parameterised the "
        + "lateral profile by distance travelled, taking the usable fan to 35–50."),
      bullet("Wrote the benchmark harness behind every number claimed: 10 scenarios "
        + "× 3 seeds × 2 controllers on identical seeds and identical sensor noise, "
        + "giving 43% mean route progress against a lane-following baseline's 36%, "
        + "ahead on 8 of the 10 scenarios."),
      bullet("Shipped a live browser console where a reviewer places, drags and "
        + "removes road users with the mouse while the planner runs, with all 31 "
        + "thresholds adjustable mid-run and component ablations behind switches."),

      projectTitle("BRAHMO — Clinical Decision Support System",
                   "https://github.com/Paragraph1148/brahmo-india-clinical-assessment-astoum"),
      stack("Next.js, TypeScript, Supabase/PostgreSQL, Claude API, Groq Llama 70B"),
      bullet("Designed a hybrid architecture pairing a deterministic clinical safety "
        + "engine with LLM reasoning, so that dosing and contraindication checks "
        + "never depend on model output."),
      bullet("Implemented Indian clinical guideline logic (RSSDI/CSI) including "
        + "CKD-EPI eGFR and CHA2DS2-VASc scoring against structured patient data."),
      bullet("Composed context-aware recommendations from patient labs, "
        + "comorbidities, and locally available Indian brands, surfacing concrete "
        + "cost trade-offs (e.g., Streptokinase at INR 1,920 vs Tenecteplase at "
        + "INR 29,870)."),
      bullet("Shipped as an independent full-stack product with authentication and "
        + "a Postgres-backed clinical data layer."),

      projectTitle("Distributed Real-Time Chat Engine",
                   "https://github.com/Paragraph1148/winter-chat"),
      stack("Fastify, Socket.IO, Redis Streams, PostgreSQL, Docker"),
      bullet("Built a horizontally scalable chat backend using Redis Streams for "
        + "cross-instance message fan-out and durable delivery."),
      bullet("Designed the persistence layer in PostgreSQL for message history and "
        + "room membership."),
      bullet("Containerized with Docker for reproducible local and deployed runs."),

      // ----------------------------------------------------------- skills
      section("TECHNICAL SKILLS"),
      skill("Languages:", "Python, TypeScript, JavaScript, SQL"),
      skill("Backend & Real-time:",
            "Node.js, Fastify, Socket.IO, Redis Streams, WebSockets, REST API design"),
      skill("Numerics & Planning:",
            "NumPy, SciPy, Kalman filtering, dynamic programming, Frenet trajectory "
            + "generation, RSS safety envelopes, control barrier functions"),
      skill("Databases:", "PostgreSQL, Supabase, Redis"),
      skill("Frontend:", "Next.js, React, HTML5 Canvas"),
      skill("AI Integration:", "Claude API, Groq (Llama 70B), LLM application design"),
      skill("Tooling & Testing:", "Docker, Git, uv, pytest, Jest, Playwright"),

      // --------------------------------------------------------- education
      section("EDUCATION"),
      education("Master of Computer Applications (MCA) — SSTC, Bhilai   (CPI: 8.12)",
                "2025 – 2027"),
      education("B.Sc. (Hons) Physics — ICFAI University, Raipur   (CGPA: 8.54)",
                "2021 – 2024"),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("Rishabh_Singh_Kushwaha_Resume.docx", buf);
  console.log("wrote Rishabh_Singh_Kushwaha_Resume.docx");
});
