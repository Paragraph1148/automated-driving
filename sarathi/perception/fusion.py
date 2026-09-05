"""Multi-sensor fusion and track management.

Detections from camera, LiDAR and radar are associated to persistent tracks and
filtered with a constant-velocity Kalman filter. Class identity is fused
separately, in log-odds, because only the camera observes it and it arrives noisily
- and because the risk field downstream is class-conditioned, so *how confident we
are* about a class is itself planning-relevant.

Tracks have a lifecycle: a new detection starts a tentative track, repeated hits
confirm it, and misses coast it before deletion. Coasting is what lets the planner
keep braking for a cow that has just disappeared behind a bus.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.types import AgentClass, params_for
from .sensors import Detection

#: Hard ceiling on the association gate, metres. The gate itself is adaptive.
GATE_MAX = 8.0
#: Floor on the association gate, so a very confident track can still be updated.
GATE_MIN = 1.0
#: Gate width in standard deviations of the combined track + detection uncertainty.
GATE_SIGMAS = 3.0
#: Tracks closer than this are merge candidates. Scaled by the *smaller* of the two
#: vehicles: detections on one long bus do spread along its body, but on an Indian
#: road a two-wheeler is routinely within 6 m of a bus, and scaling by the larger
#: vehicle lets the bus track swallow it.
MERGE_DISTANCE_BASE = 2.0
MERGE_SIZE_FACTOR = 0.55
MERGE_SPEED_DELTA = 2.5
#: Below this many hits a track is too new to have a class opinion worth respecting.
MERGE_WEAK_HITS = 5
#: Speed above which a track's velocity direction is trusted as its heading.
#: Below it the estimate is held: at 0.4 m/s a stationary vehicle's velocity is
#: noise, and its "direction" is whichever way the noise happened to point.
HEADING_TRUST_SPEED = 1.0
#: Ground a track must actually cover before its heading is fixed from it, m.
#:
#: An instantaneous speed threshold cannot do this job. Velocity is initialised
#: at variance 25 - five metres a second of standard deviation - so a parked
#: vehicle's filtered velocity crosses any sane threshold within a tick or two
#: of being detected, and the heading latched from that noise is whichever way
#: the noise pointed. Nothing ever unlatched it. Measured on
#: bus_stop_overtake: six of ten stationary tracks carried a heading more than
#: 45 degrees wrong, mean error 25 degrees, and on village_road_unmarked 53.
#: The risk kernels are anisotropic, so an 11 m bus wearing a heading at 60
#: degrees to the road lays its length across the carriageway.
#:
#: Displacement cannot be faked by noise the same way: jitter about a fixed
#: point does not accumulate into a metre of travel, while anything genuinely
#: moving covers it in about a second.
HEADING_TRAVEL = 1.0
#: How many standard deviations of its own position uncertainty a track must
#: travel before the direction of that travel means anything.
#:
#: A newly detected object's position estimate is still converging and can jump
#: the better part of a metre between updates on nothing but a revised depth
#: estimate. Fixing a heading from that jump latches a direction taken from the
#: filter settling rather than from the object moving, which is how a parked car
#: came to sit at 85 degrees across a road it was parallel to for the first
#: seconds of its life.
HEADING_SIGMAS = 2.0
#: ...and it must cover that ground within this long, seconds.
#:
#: Distance alone is not enough either. A parked vehicle's filtered position
#: random-walks on detection noise, and given long enough it wanders a metre -
#: at which point the direction of the wander gets latched as its heading and
#: never revised. Measured on a parked car beside an empty road: heading
#: +100.7 degrees, essentially square across a road it was parallel to, which
#: laid its 4.65 m core half-length across the carriageway and turned a 4.2 m
#: car into a 9.3 m wall. The ego stopped level with it, 1.58 m clear of it,
#: reporting the road blocked, and never moved again.
#:
#: Together the two mean "averaged at least half a metre per second over a
#: couple of seconds", which drift cannot fake and anything genuinely
#: travelling passes without noticing.
HEADING_WINDOW = 2.0
#: Speed below which a track counts as stationary rather than travelling, m/s.
MOVING_SPEED = 0.8
#: ...or below this many standard deviations of its own velocity uncertainty.
#:
#: A fixed threshold is not enough on its own. Velocity is initialised at
#: variance 25 - five metres per second of standard deviation - so for the first
#: second of its life a parked car's filtered speed reads in the metres per
#: second and any fixed threshold calls it moving. It is then predicted rolling
#: forward, and the heading of that rolled-out path is noise: measured at 85
#: degrees across a road the car was parallel to.
MOVING_SIGMAS = 3.0
#: ...and it must have been seen this many times before its velocity counts.
#:
#: The covariance cannot carry this on its own, because early on the filter is
#: overconfident: two frames after a parked car was first detected its velocity
#: read 2.15 m/s at 85 degrees to the road with a reported sigma of only 0.79.
#: A velocity derived from two noisy positions is not an observation of motion
#: however small its stated uncertainty. Four hits is about 0.4 s, during which
#: the object is still tracked and still carries its risk kernel - only the
#: forward projection of it waits.
MOVING_HITS = 4

#: Per-class log-odds are bounded so a long-lived belief stays correctable. An
#: unbounded belief at +500 can never be revised by contrary evidence.
CLASS_LOGODDS_LIMIT = 40.0
#: Hits needed before a tentative track is confirmed and shown to the planner.
CONFIRM_HITS = 2
#: Consecutive misses before a confirmed track is deleted.
MAX_COAST = 12


@dataclass
class Track:
    """A fused, persistent estimate of one road user."""

    id: int
    x: float
    y: float
    vx: float
    vy: float
    #: 4x4 state covariance for [x, y, vx, vy].
    P: np.ndarray
    cls: AgentClass = AgentClass.CAR
    cls_logodds: dict = field(default_factory=dict)
    cls_confidence: float = 0.3
    age: float = 0.0
    hits: int = 1
    misses: int = 0
    confirmed: bool = False
    #: Ground truth id, for evaluation only. Never read by the planner.
    truth_id: int = -1
    #: Held orientation estimate, updated only while the object is unambiguously
    #: moving. A stationary vehicle's velocity is almost entirely filter noise, so
    #: deriving its heading per-tick from atan2(vy, vx) yields a direction that
    #: spins through the whole circle - and every heading-conditioned consumer
    #: downstream, the risk kernel above all, spins with it.
    heading_estimate: float = 0.0
    has_heading: bool = False
    #: Where the track was when its heading was last fixed, and the track age
    #: at that moment, for measuring how far it has genuinely travelled since
    #: and how long it took. ``None`` until the first update.
    heading_anchor: tuple[float, float] | None = None
    heading_anchor_age: float = 0.0

    @property
    def position(self) -> np.ndarray:
        return np.array([self.x, self.y])

    @property
    def velocity(self) -> np.ndarray:
        return np.array([self.vx, self.vy])

    @property
    def speed(self) -> float:
        return float(math.hypot(self.vx, self.vy))

    @property
    def heading(self) -> float:
        """Best available orientation - held, not recomputed from noisy velocity.

        Only meaningful once :attr:`has_heading` is set; until then this is a
        default rather than an observation, and a consumer that cannot check
        should be asking the road instead. See :data:`HEADING_TRAVEL`.
        """
        if self.has_heading and self.speed < HEADING_TRUST_SPEED:
            return float(self.heading_estimate)
        if self.speed >= HEADING_TRUST_SPEED:
            return float(math.atan2(self.vy, self.vx))
        return float(self.heading_estimate)

    @property
    def velocity_sigma(self) -> float:
        """Scalar velocity uncertainty, from the filter's own covariance."""
        return float(math.sqrt(max(self.P[2, 2] + self.P[3, 3], 1e-9) / 2.0))

    @property
    def is_moving(self) -> bool:
        """Whether this track is genuinely travelling, or merely jittering.

        Measured against the filter's own velocity uncertainty, so that a
        reading is only motion when it is larger than the noise that could have
        produced it. Deliberately not a question about whether the track has
        earned a heading. The two are separate: a vehicle first
        detected closing at 14 m/s is unambiguously moving on its first frame,
        and has not yet covered the ground that fixes its orientation. Coupling
        them would have every newly seen vehicle predicted stationary for its
        first metre, which is the one metre that matters most.

        Consumers that treat noise as motion do real damage: time-to-collision
        computed against a stationary car's noise velocity produces a closing
        speed pointed at the ego often enough to trip the emergency stop, which
        stops the vehicle, which freezes the geometry that produced the reading.
        """
        return (self.hits >= MOVING_HITS
                and self.speed >= max(MOVING_SPEED,
                                      MOVING_SIGMAS * self.velocity_sigma))

    @property
    def length(self) -> float:
        return params_for(self.cls).length

    @property
    def width(self) -> float:
        return params_for(self.cls).width

    @property
    def position_sigma(self) -> float:
        """Scalar positional uncertainty, used to inflate the risk field."""
        return float(math.sqrt(max(self.P[0, 0] + self.P[1, 1], 1e-6) / 2.0))


class Tracker:
    """Greedy-association constant-velocity multi-object tracker."""

    def __init__(self, dt: float, process_noise: float = 2.5):
        self.dt = dt
        self.q = process_noise
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    # -- prediction step --------------------------------------------------
    def _F(self) -> np.ndarray:
        dt = self.dt
        return np.array([[1, 0, dt, 0],
                         [0, 1, 0, dt],
                         [0, 0, 1, 0],
                         [0, 0, 0, 1]], dtype=float)

    def _Q(self) -> np.ndarray:
        """Discrete white-noise acceleration model."""
        dt = self.dt
        q = self.q ** 2
        a, b, c = dt ** 4 / 4.0, dt ** 3 / 2.0, dt ** 2
        return q * np.array([[a, 0, b, 0],
                             [0, a, 0, b],
                             [b, 0, c, 0],
                             [0, b, 0, c]], dtype=float)

    def predict(self) -> None:
        F, Q = self._F(), self._Q()
        for tr in self.tracks.values():
            x = F @ np.array([tr.x, tr.y, tr.vx, tr.vy])
            tr.x, tr.y, tr.vx, tr.vy = map(float, x)
            tr.P = F @ tr.P @ F.T + Q
            tr.age += self.dt

    def _update_heading(self, tr: "Track") -> None:
        """Fix a track's orientation from ground covered, not from velocity.

        See :data:`HEADING_TRAVEL`. The anchor is only advanced when the
        heading is actually taken from it, so a vehicle creeping forward at
        0.2 m/s still earns a heading after five seconds rather than never.
        """
        if tr.heading_anchor is None:
            tr.heading_anchor = (tr.x, tr.y)
            tr.heading_anchor_age = tr.age
            return
        dx, dy = tr.x - tr.heading_anchor[0], tr.y - tr.heading_anchor[1]
        needed = max(HEADING_TRAVEL, HEADING_SIGMAS * tr.position_sigma)
        if math.hypot(dx, dy) >= needed:
            tr.heading_estimate = math.atan2(dy, dx)
            tr.has_heading = True
        elif tr.age - tr.heading_anchor_age < HEADING_WINDOW:
            return                      # still earning it
        else:
            # A full window without covering the ground is positive evidence
            # that this thing is not travelling, so any heading it is carrying
            # was taken from drift and has to go. Nothing cleared it before,
            # and a heading latched once from a metre of random walk survived
            # the whole run: a parked car sat at +100.7 degrees to a road it
            # was parallel to.
            #
            # The cost is a vehicle that genuinely parked at an angle, which
            # now falls back to the road it is parked on. That error is
            # bounded by how far from parallel a vehicle actually parks; the
            # error this removes was unbounded.
            tr.has_heading = False
        tr.heading_anchor = (tr.x, tr.y)
        tr.heading_anchor_age = tr.age

    # -- update step ------------------------------------------------------
    def update(self, detections: list[Detection]) -> list[Track]:
        self.predict()
        assigned = self._associate(detections)

        for track_id, dets in assigned.items():
            tr = self.tracks[track_id]
            for det in dets:
                self._kalman_update(tr, det)
                if det.cls is not None:
                    self._fuse_class(tr, det)
            tr.hits += 1
            tr.misses = 0
            if tr.hits >= CONFIRM_HITS:
                tr.confirmed = True
            self._update_heading(tr)

        for tr in self.tracks.values():
            if tr.id not in assigned:
                tr.misses += 1

        # New tracks from detections nothing claimed. Radar alone may not start a
        # track: its lateral resolution is too poor to localise a new object, and
        # letting it do so scatters ghost tracks across the scene.
        claimed = {id(d) for dets in assigned.values() for d in dets}
        for det in detections:
            if id(det) not in claimed and det.sensor != "radar":
                self._spawn(det)

        self.tracks = {tid: tr for tid, tr in self.tracks.items()
                       if tr.misses <= MAX_COAST}
        self._merge_duplicates()
        return [tr for tr in self.tracks.values() if tr.confirmed]

    def forget(self, points, radius: float = 3.5) -> int:
        """Drop tracks for objects that have been removed from the world.

        Coasting exists so that something hidden behind a bus is not forgotten,
        and that is worth keeping: no sensor can tell "occluded" from "gone", so
        the safe reading of a missed detection is "still there". But in this
        sandbox objects really do cease to exist - a viewer erases one, or the
        simulator clears one the vehicle has touched so it is not left wedged
        inside it - and no sensor can observe *that* either. So the world says
        so directly, rather than leaving perception to hallucinate a vehicle
        that is no longer anywhere.

        It is the difference between a demo that shows a red risk blob sitting
        over empty road for a second after you delete a car, and one that does
        not.
        """
        gone = [tid for tid, tr in self.tracks.items()
                if any(math.hypot(tr.x - x, tr.y - y) <= radius for x, y in points)]
        for tid in gone:
            del self.tracks[tid]
        return len(gone)

    def _merge_duplicates(self) -> None:
        """Fold together tracks that are clearly the same object.

        Fragmentation is the characteristic failure of greedy association: one
        vehicle briefly generates two tracks, and the planner then sees a phantom
        neighbour beside a real one. Merging keeps the older, better-established
        track and its accumulated class evidence.
        """
        ids = sorted(self.tracks, key=lambda t: (-self.tracks[t].hits, t))
        dropped: set[int] = set()
        for i, a_id in enumerate(ids):
            if a_id in dropped:
                continue
            a = self.tracks[a_id]
            for b_id in ids[i + 1:]:
                if b_id in dropped:
                    continue
                b = self.tracks[b_id]
                limit = max(MERGE_DISTANCE_BASE,
                            MERGE_SIZE_FACTOR * min(a.length, b.length))
                if float(np.linalg.norm(a.position - b.position)) > limit:
                    continue
                # Never fold two tracks that both believe they are different
                # things. Only a track too new to have an opinion may be absorbed
                # into one of a different class.
                if a.cls is not b.cls and min(a.hits, b.hits) >= MERGE_WEAK_HITS:
                    continue
                # Normally require agreeing velocity, but a much weaker overlapping
                # track is a fragment however badly its velocity is estimated.
                weak = b.hits < 0.3 * a.hits
                if not weak and float(np.linalg.norm(
                        a.velocity - b.velocity)) > MERGE_SPEED_DELTA:
                    continue
                for cls, value in b.cls_logodds.items():
                    if isinstance(cls, AgentClass):
                        a.cls_logodds[cls] = a.cls_logodds.get(cls, 0.0) + value
                if b.has_heading and not a.has_heading:
                    a.heading_estimate, a.has_heading = b.heading_estimate, True
                a.hits += b.hits
                dropped.add(b_id)
        for tid in dropped:
            del self.tracks[tid]

    def _associate(self, detections: list[Detection]) -> dict[int, list[Detection]]:
        """Greedy nearest-neighbour association with a hard distance gate.

        Greedy rather than Hungarian: with a tight gate and 20 Hz updates the two
        agree almost always, and this runs inside every Monte-Carlo tick.
        """
        if not self.tracks or not detections:
            return {}
        track_ids = list(self.tracks)
        tpos = np.array([[self.tracks[t].x, self.tracks[t].y] for t in track_ids])
        dpos = np.array([[d.x, d.y] for d in detections])
        cost = np.linalg.norm(dpos[:, None, :] - tpos[None, :, :], axis=2)

        # Adaptive gate. A fixed gate is wrong here because radar's lateral error
        # grows with range - at 45 m it is over 1.5 m - so a fixed 3.5 m gate
        # rejects legitimate radar returns and spawns a duplicate track for the
        # same vehicle on almost every frame.
        track_sigma = np.array([
            math.sqrt(max(self.tracks[t].P[0, 0] + self.tracks[t].P[1, 1], 1e-6))
            for t in track_ids])
        det_sigma = np.array([d.sigma for d in detections])
        gate = np.clip(GATE_SIGMAS * np.hypot(det_sigma[:, None],
                                              track_sigma[None, :]),
                       GATE_MIN, GATE_MAX)

        out: dict[int, list[Detection]] = {}
        # One detection per sensor may legitimately hit the same track, so a track
        # can take several detections but a detection only ever one track.
        order = np.dstack(np.unravel_index(np.argsort(cost, axis=None), cost.shape))[0]
        used_det: set[int] = set()
        for di, ti in order:
            if cost[di, ti] > gate[di, ti]:
                continue
            if di in used_det:
                continue
            det = detections[int(di)]
            tid = track_ids[int(ti)]
            bucket = out.setdefault(tid, [])
            if any(d.sensor == det.sensor for d in bucket):
                continue        # already have a return from this modality
            bucket.append(det)
            used_det.add(int(di))
        return out

    def _kalman_update(self, tr: Track, det: Detection) -> None:
        x = np.array([tr.x, tr.y, tr.vx, tr.vy])
        if det.vx is not None:
            H = np.eye(4)
            z = np.array([det.x, det.y, det.vx, det.vy])
            R = np.diag([det.sigma ** 2, det.sigma ** 2, 0.35 ** 2, 1.2 ** 2])
        else:
            H = np.array([[1., 0, 0, 0], [0, 1., 0, 0]])
            z = np.array([det.x, det.y])
            R = np.eye(2) * det.sigma ** 2
        y = z - H @ x
        S = H @ tr.P @ H.T + R
        K = tr.P @ H.T @ np.linalg.inv(S)
        x = x + K @ y
        tr.P = (np.eye(4) - K @ H) @ tr.P
        tr.x, tr.y, tr.vx, tr.vy = map(float, x)

    def _fuse_class(self, tr: Track, det: Detection) -> None:
        """Accumulate class evidence in log-odds across frames.

        A single frame's label is unreliable; twenty frames of agreeing labels is
        not. Because the risk field is class-conditioned, holding the belief rather
        than the latest guess stops the planner flip-flopping between "cow" and
        "handcart" kernels on alternate ticks.
        """
        conf = float(np.clip(det.cls_confidence, 0.05, 0.95))
        evidence = math.log(conf / (1.0 - conf))
        odds = tr.cls_logodds
        odds[det.cls] = odds.get(det.cls, 0.0) + evidence
        for cls in list(odds):
            if isinstance(cls, AgentClass) and cls is not det.cls:
                odds[cls] -= 0.15 * evidence

        for cls in list(odds):
            if isinstance(cls, AgentClass):
                odds[cls] = float(np.clip(odds[cls], -CLASS_LOGODDS_LIMIT,
                                          CLASS_LOGODDS_LIMIT))

        classes = [c for c in odds if isinstance(c, AgentClass)]
        if not classes:
            return
        best = max(classes, key=lambda c: odds[c])
        tr.cls = best
        # Softmax via the log-sum-exp trick. Clamping the exponents instead (the
        # earlier approach) made two well-established classes both saturate and tie
        # at exactly 0.5, reporting maximum uncertainty about a class we were in
        # fact certain of.
        peak = odds[best]
        total = sum(math.exp(odds[c] - peak) for c in classes)
        tr.cls_confidence = float(1.0 / max(total, 1e-9))

    def _spawn(self, det: Detection) -> None:
        tr = Track(id=self._next_id, x=det.x, y=det.y,
                   vx=det.vx or 0.0, vy=det.vy or 0.0,
                   P=np.diag([det.sigma ** 2 + 0.5, det.sigma ** 2 + 0.5,
                              25.0, 25.0]),
                   cls=det.cls or AgentClass.CAR,
                   truth_id=det.truth_id)
        if det.cls is not None:
            self._fuse_class(tr, det)
        self.tracks[self._next_id] = tr
        self._next_id += 1
