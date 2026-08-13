import { useEffect } from "react";
import { useAgentSocket } from "./useAgentSocket";

const REFRESH_EVENT_TYPES = new Set([
  "finding", "finding_updated", "finding_approved", "finding_applied", "finding_rejected", "analysis_complete",
]);

/**
 * Re-runs `onRefresh` whenever a WebSocket event arrives that means this
 * cluster's findings may have changed: a new finding raised during an
 * analysis pass, an approve/reject/apply action, a full pass completing, or
 * a background-drafted query rewrite ("finding_updated") landing on a
 * finding a moment after it was first raised.
 *
 * Without this, any page that only fetches findings once on mount/cluster-
 * change (Dashboard, Insights, Pending Approval, Applied History, Needs Code
 * Change) goes stale the moment a pass runs in the background, from the
 * scheduler, or via "Run now" -- the chat panel would show a brand-new
 * critical finding in real time while these pages kept showing "0 findings"
 * from before that pass ran, until a manual refresh/re-navigation.
 */
export function useFindingsRefresh(clusterId: string | null | undefined, onRefresh: () => void) {
  const { lastEvent } = useAgentSocket();

  useEffect(() => {
    if (!lastEvent || !REFRESH_EVENT_TYPES.has(lastEvent.type)) return;
    // "finding" events nest the finding under payload.finding; the others
    // (finding_approved/applied/rejected, analysis_complete) broadcast an
    // object that already has cluster_id at the top level.
    const eventClusterId: string | undefined = lastEvent.payload?.cluster_id ?? lastEvent.payload?.finding?.cluster_id;
    if (clusterId && eventClusterId && eventClusterId !== clusterId) return;
    onRefresh();
    // onRefresh is expected to be a stable-enough page-level function; re-running
    // this effect on every render would reconnect nothing extra but does add churn,
    // so intentionally key off the event/cluster only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent, clusterId]);
}
