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
        """Best available orientation - held, not recomputed from noisy velocity."""
        if self.speed >= HEADING_TRUST_SPEED:
            return float(math.atan2(self.vy, self.vx))
        return float(self.heading_estimate)

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
            if tr.speed >= HEADING_TRUST_SPEED:
                tr.heading_estimate = math.atan2(tr.vy, tr.vx)
                tr.has_heading = True

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
