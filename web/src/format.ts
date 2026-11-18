// Small pure helpers for the page; tested in format.test.ts.

import type { Job } from "./api";

/** 170 → "2:50", 3725 → "1:02:05". */
export function formatSeconds(seconds: number): string {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** How far the job is, 0 to 1: the finished share of its stages. */
export function progress(job: Job): number {
  if (job.state === "done") return 1;
  if (job.stages.length === 0) return 0;
  const done = job.stages.filter((stage) => job.done.includes(stage)).length;
  return done / job.stages.length;
}

/**
 * A run name from the video's file name, following the server's rule: letters, digits, '.', '_'
 * and '-', starting with a letter or digit, at most 64 characters.
 * "/videos/My Video (1).mp4" → "My-Video-1".
 */
export function suggestName(video: string): string {
  const file = video.split(/[\\/]/).pop() ?? "";
  const stem = file.includes(".") ? file.slice(0, file.lastIndexOf(".")) : file;
  return stem
    .replace(/[^\p{L}\p{N}._-]+/gu, "-")
    .replace(/^[^\p{L}\p{N}]+/u, "")
    .replace(/-+$/, "")
    .slice(0, 64);
}

/** What a person has to do while the job waits, in words. */
export function waitingText(job: Job): string {
  const what: Record<string, string> = {
    review1: "Review 1 of 2 (the transcript)",
    review2: "Review 2 of 2 (the translation)",
    shorts: "Pick the Shorts",
  };
  const kind = job.waiting_for?.kind ?? "";
  return what[kind] ?? `Waiting for ${kind || "a person"}`;
}
