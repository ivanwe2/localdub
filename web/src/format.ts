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

/** 14_971_568_128 → "15.0 GB" (decimal gigabytes, like the drives and cards say). */
export function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${Math.round(bytes / 1e3)} kB`;
}

/**
 * The points of a little line chart (an SVG polyline) for these values, in a `width` × `height`
 * box: the oldest on the left, the largest at the top. [] for fewer than two values.
 */
export function sparkline(values: number[], width: number, height: number): string {
  if (values.length < 2) return "";
  const top = Math.max(...values) || 1;
  const step = width / (values.length - 1);
  return values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - (v / top) * height).toFixed(1)}`)
    .join(" ");
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
  const dot = file.lastIndexOf(".");
  return clean(dot > 0 ? file.slice(0, dot) : file, 64);
}

/**
 * A safe file name for an upload, by the same rule (the server takes up to 128 characters):
 * "My Video (1).MP4" → "My-Video-1.mp4".
 */
export function uploadName(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  const ext = dot > 0 ? fileName.slice(dot + 1).toLowerCase().replace(/[^a-z0-9]/g, "") : "";
  const stem = clean(dot > 0 ? fileName.slice(0, dot) : fileName, 100) || "video";
  return ext ? `${stem}.${ext}` : stem;
}

/** Letters, digits, '.', '_' and '-' only (others become '-'), starting with a letter or digit. */
function clean(stem: string, max: number): string {
  return stem
    .replace(/[^\p{L}\p{N}._-]+/gu, "-")
    .replace(/^[^\p{L}\p{N}]+/u, "")
    .replace(/-+$/, "")
    .slice(0, max);
}

const WAITING: Record<string, string> = {
  review1: "Review 1 of 2 (the transcript)",
  review2: "Review 2 of 2 (the translation)",
  shorts: "Pick the Shorts",
};

/** What a person has to do while the job (or a run) waits, in words. */
export function waitingText(job: Pick<Job, "waiting_for">): string {
  const kind = job.waiting_for?.kind ?? "";
  return WAITING[kind] ?? `Waiting for ${kind || "a person"}`;
}

/** The review text with its status line set to approved, which tells the engine to go on. */
export function approve(text: string): string {
  const lines = text.split("\n");
  const at = lines.findIndex((line) => line.trim().toLowerCase().startsWith("status:"));
  if (at === -1) return `status: approved\n${text}`;
  lines[at] = "status: approved";
  return lines.join("\n");
}

/** One Shorts candidate in a candidates.txt: `[x] 1   00:10-00:40 (30s)   sentences 3-9   "Title"`. */
export interface Candidate {
  line: number; // its line in the text
  picked: boolean;
  n: number;
  title: string;
  when: string; // e.g. "00:10-00:40 (30s)"
  details: string[]; // the indented lines below it: hook, why, screen, starts, ends
}

const CANDIDATE = /^\[(.?)\]\s*(\d+)\s+(\S+\s+\(\d+s\))?.*?(?:"(.*)")?\s*$/;

/** The candidates listed in a Shorts candidates file, in their order. */
export function candidates(text: string): Candidate[] {
  const lines = text.split("\n");
  const found: Candidate[] = [];
  lines.forEach((raw, i) => {
    const m = CANDIDATE.exec(raw.trim());
    if (!m) return;
    const details: string[] = [];
    for (let j = i + 1; j < lines.length && /^\s+\S/.test(lines[j]); j++) details.push(lines[j].trim());
    found.push({
      line: i,
      picked: m[1].trim() !== "",
      n: Number(m[2]),
      title: m[4] ?? "",
      when: m[3] ?? "",
      details,
    });
  });
  return found;
}

/** The text with that candidate's mark switched: `[ ]` ↔ `[x]`. */
export function toggleCandidate(text: string, line: number): string {
  const lines = text.split("\n");
  lines[line] = lines[line].replace(/^(\s*)\[(.?)\]/, (_, space, mark) => `${space}[${mark.trim() ? " " : "x"}]`);
  return lines.join("\n");
}
