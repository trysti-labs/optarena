import { ClickStats } from "../models/types";

/**
 * Aggregate ISO click timestamps into per-day stats.
 *
 * Days are the UTC "YYYY-MM-DD" of each timestamp. peakDay is the day with
 * the most clicks; on a tie, the EARLIEST such day wins. No clicks means
 * total 0, empty byDay, peakDay null, peakCount 0.
 */
export function summarizeClicks(timestamps: string[]): ClickStats {
  const byDay: Record<string, number> = {};
  for (const ts of timestamps) {
    const day = ts.slice(0, 10);
    byDay[day] = (byDay[day] ?? 0) + 1;
  }

  let peakDay: string | null = null;
  let peakCount = 0;
  for (const day of Object.keys(byDay).sort()) {
    if (byDay[day] > peakCount) {
      peakDay = day;
      peakCount = byDay[day];
    }
  }

  return { total: timestamps.length, byDay, peakDay, peakCount };
}
