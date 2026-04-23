// BSSID → AccessPoint resolver.
//
// Each AP owns 1..N BSSIDs (junction table on the server). Admin explicitly
// assigns observed BSSIDs to an AP via the /access-points page. We do exact
// match here only — no prefix/wildcard guessing — so two visually-similar
// BSSIDs that happen to belong to different APs never collide.

import type { AccessPointView } from "../api/types";

export interface ApResolver {
  /** Human name for a BSSID, or undefined when unmapped. */
  name(bssid: string | null | undefined): string | undefined;
  /**
   * Grouping key — returns `ap:<name>` when mapped so every BSSID on the same
   * AP collapses into one group, else `bssid:<normalized bssid>` so each
   * unmapped BSSID is its own group. Used to color-segment charts.
   */
  groupKey(bssid: string | null | undefined): string;
}

export function buildApResolver(aps: AccessPointView[]): ApResolver {
  const nameByBssid = new Map<string, string>();
  for (const ap of aps) {
    for (const b of ap.bssids ?? []) {
      nameByBssid.set(b.toLowerCase(), ap.name);
    }
  }
  return {
    name(bssid) {
      if (!bssid) return undefined;
      return nameByBssid.get(bssid.toLowerCase());
    },
    groupKey(bssid) {
      if (!bssid) return "__unknown__";
      const b = bssid.toLowerCase();
      const named = nameByBssid.get(b);
      return named ? `ap:${named}` : `bssid:${b}`;
    },
  };
}
