export interface Link {
  slug: string;
  url: string;
  createdAt: string; // ISO timestamp
}

export interface ClickStats {
  total: number;
  byDay: Record<string, number>; // "YYYY-MM-DD" -> count
  peakDay: string | null;
  peakCount: number;
}
