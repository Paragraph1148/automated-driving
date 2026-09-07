# Relaunch checklist

Before SARATHI gets pointed at from a LinkedIn post, the numbers it claims have
to be numbers it can defend. Right now they are not, for a reason that is worth
stating precisely, because it is not the reason it first looked like.

## What is actually wrong

The README quotes a campaign of **10 scenarios x 3 seeds = 30 runs per arm**.
From that it reports 43.3% mean route progress against a lane-following
baseline's 36.2%, and 25/30 collision-free against the baseline's 27/30.

The progress claim is fine. The safety line is the problem, in both directions:

* Quoting only the progress row is selective. The very next row of the same
  table says the baseline had fewer contacts.
* But **that difference is not real either**. Fisher's exact test on 25/30 vs
  27/30 gives **p = 0.706**, and the Wilson intervals are [66%, 93%] against
  [74%, 97%] - almost entirely overlapping. Thirty runs cannot tell 83% from
  90%.

So the honest finding is not "the planner is less safe than a lane follower".
It is **"this campaign is too small to say"** - which also means the flattering
readings of it were never supported.

Two more things the current README does not mention, both of which turn up the
moment the campaign is analysed properly:

* **Neither controller ever reaches the goal.** 0 of 30 for both arms.
* Per kilometre travelled, SARATHI has *more* contacts (251 per 100 km vs 176),
  not fewer - on 5 events against 3, so again far too few to conclude anything.

And the latency figure is stale: the README says median p95 46 ms; the same code
now measures 25-40 ms on other hardware. The code has changed substantially
since those numbers were taken.

The fix is measurement, not spin: run a campaign large enough to resolve the
question, then report the intervals alongside the point estimates.

---

## Phase 1 - Re-measure  (this is the part on your laptop)

`scripts/analyse_campaign.py` is new in this branch. It does the statistics the
old summary skipped: Wilson intervals, a Fisher exact test on the safety
comparison, contacts per kilometre, and a fault split that separates "drove into
something" from "was struck while stopped".

**Run the campaign.** On 6 cores / 12 threads, use 10 workers and leave two
threads for the machine:

```bash
git fetch origin && git checkout rework
uv sync
mkdir -p artifacts
uv run python scripts/benchmark.py --seeds 20 --workers 10 \
  -o artifacts/benchmark-laptop.json | tee artifacts/benchmark-laptop.log
```

400 runs. Expect roughly **25-40 minutes** on that machine. It prints a line per
run so you can watch it move. If you want a quicker first look, use
`--seeds 8` (160 runs, ~10 minutes) - but the final numbers should come from the
20-seed run, because resolving the safety question is the entire point.

**Analyse it.**

```bash
uv run python scripts/analyse_campaign.py artifacts/benchmark-laptop.json
```

**Then get the paste-ready README block:**

```bash
uv run python scripts/analyse_campaign.py artifacts/benchmark-laptop.json --readme
```

### What to send back

Just these two, and I will do the rest:

1. `artifacts/benchmark-laptop.json`
2. The terminal output of the plain (non-`--readme`) analyse command

Your laptop is the right machine to quote for latency, because the claim in your
LinkedIn About is "20 Hz on a laptop CPU". Say which CPU it is when you send the
results and that goes in the README next to the number.

### Checklist

- [ ] `uv sync` completes
- [ ] Campaign finishes with **0 errors** (the analyse output prints an ERRORS
      line if any run raised; there should be none)
- [ ] `analyse_campaign.py` runs clean
- [ ] Results sent back

---

## Phase 2 - Fix the public claims  (mine, once your numbers land)

- [ ] Replace the README **Results** section with the generated block
- [ ] Add the CPU and run count next to the latency figure
- [ ] State the safety comparison with its p-value, in a sentence that does not
      claim more than the test supports
- [ ] Add the completion rate - if it is still 0/N, say so; a planner that never
      finishes a route is a real limitation and hiding it is the thing that gets
      found
- [ ] Re-run the full test suite (`uv run --extra dev pytest`, 155 tests)

## Phase 3 - Make the demo linkable  (mine)

The page currently has no Open Graph tags, so a LinkedIn link card for
`sarathi.rishabhkushwaha.com` renders as a bare grey box.

- [ ] `og:title`, `og:description`, `og:image` + Twitter equivalents
- [ ] Serve a preview image (the poster frame from the video works)
- [ ] Check the card with LinkedIn's Post Inspector before featuring the link

## Phase 4 - Video  (mine, only if needed)

The cut is finished and the footage does not change. Only the closing card
carries numbers - "collision-free in 43 of 60", "2.1 m/s against a 10.7 target",
"17 ms, p95 25 ms".

- [ ] If the new campaign moves any of those, re-render that one card
      (text only, about two minutes) and re-concatenate
- [ ] Record voiceover to picture - see `VOICEOVER-AND-FACECAM.md`
- [ ] Composite facecam into the empty lower-right area

## Phase 5 - LinkedIn  (yours; copy from me)

Ordered by how much each is costing you right now.

**1. Add an Experience section.** This is the largest gap on the profile - there
is currently none at all. Education, Projects and Volunteering cannot carry a
profile for someone applying to internships. One of your own posts references a
Go + Rod browser-automation internship assignment; that is an entry.

**2. Put URLs on both Projects.** LinkedIn's Projects entries take a link and
both of yours are bare text. SARATHI has a live demo and a repo.

**3. De-duplicate About and Projects.** The SARATHI and BRAHMO paragraphs are
currently identical in both places. About should say how you work; Projects
should carry the detail.

**4. Reorder skills.** 94 is far too many and LinkedIn surfaces only three -
yours are showing pytest and SciPy. Put Python, PostgreSQL and TypeScript first.

**5. Featured section**, in this order:
- the video post (as a Post, so clicks stay on LinkedIn and feed its engagement)
- the live demo (as a Link, once Phase 3 lands)
- a bug-stories PDF carousel (documents render inline; people swipe without
  leaving the feed)

**6. Post**, with the demo URL in the first comment rather than the body.

- [ ] Experience section added
- [ ] Project URLs added
- [ ] About rewritten so it no longer duplicates Projects
- [ ] Skills reordered
- [ ] Featured populated in order
- [ ] Posted

---

## The rule for all of it

Every number that appears in the README, the profile, the video or the post
should be reproducible by someone who clones the repo and runs one command. If
it is not, it should not be claimed. That is also the most defensible thing
about this project, and it is worth saying out loud in the post: the reason the
numbers changed is that they were measured again.
