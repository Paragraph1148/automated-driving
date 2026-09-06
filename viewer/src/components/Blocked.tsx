import type { Readout as R } from "../lib/useLive";

/**
 * "Nothing can get past this."
 *
 * The supervisor can tell the difference between waiting and there being
 * nowhere on this carriageway to go. The second is the one case the vehicle
 * cannot solve by itself, and the one where the viewer has a control it does
 * not: they can reach in and move the thing that is in the way. Saying so is
 * also the most convincing possible demonstration that the world is live — a
 * recording cannot ask you for help.
 */
export default function Blocked({ r, touch }: { r: R; touch: boolean }) {
  if (!r.blocked) return null;

  const what = r.blocked.cls.replace(/_/g, " ");
  // Only name a gesture this device actually has. Removal by shift-click does
  // not exist on a touch screen, so a touch viewer is told about the drag and
  // nothing else rather than sent after a key they have not got.
  const remove = touch ? "" : " — or shift-click it to remove it";

  return (
    <div className="blocked" role="status">
      <span className="blocked__tag">Road blocked</span>
      <span className="blocked__hint">
        {r.reversing ? (
          <>
            Backing up to try a different angle — {r.reversing.left.toFixed(1)} m of
            the shunt left, {r.reversing.room.toFixed(0)} m clear behind.
          </>
        ) : (
          <>
            Stopped {r.blocked.forSecs.toFixed(0)}s behind a <b>{what}</b> with
            nowhere past it. Drag it out of the way{remove}.
          </>
        )}
      </span>
    </div>
  );
}
