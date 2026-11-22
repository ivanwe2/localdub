// Small pure helpers for the page; tested in format.test.ts.

import type { Channel, Download, Job, Language, StartRequest } from "./api";

/** The channel with this name (ignoring spaces around it), if it exists. */
export function findChannel(name: string, channels: Channel[]): Channel | undefined {
  return channels.find((c) => c.name === name.trim());
}

/**
 * The start request's language fields: only a new channel sends them (an existing channel's
 * languages are fixed), and the speaker only when one is chosen. Shorts only needs just the video's
 * language; a channel that made only Shorts gets its dub language (once) from its first dub.
 */
export function languageFields(
  channel: string,
  channels: Channel[],
  source: string,
  target: string,
  speaker: string,
  shortsOnly = false,
): Pick<StartRequest, "source" | "target" | "speaker"> {
  const who: Pick<StartRequest, "speaker"> = speaker === "man" || speaker === "woman" ? { speaker } : {};
  const known = findChannel(channel, channels);
  if (known) return known.target || shortsOnly ? {} : { target, ...who };
  return shortsOnly ? { source } : { source, target, ...who };
}

/** A channel's languages in short: "BG → EN", or "DE · Shorts only" before its first dub. */
export function channelPair(channel: Channel): string {
  return channel.target ? `${channel.source.toUpperCase()} → ${channel.target.toUpperCase()}` : `${channel.source.toUpperCase()} · Shorts only`;
}

/** The dub language a new channel starts with: English if it can be dubbed into, else the first. */
export function defaultTarget(languages: Language[]): string {
  const dub = languages.filter((l) => l.dub);
  return (dub.find((l) => l.code === "en") ?? dub[0])?.code ?? "";
}

/** What the video-language list says about a language's spelling dictionary. */
export function dictionaryNote(language: Language): string {
  if (language.dictionary_installed) return "";
  if (!language.dictionary) return " (no spelling check)";
  return ` (dictionary missing: apt-get install ${language.apt ?? language.dictionary})`;
}

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

/** A run name from a downloaded video's title: "Me at the zoo!" → "Me-at-the-zoo". */
export function nameFromTitle(title: string): string {
  return clean(title, 64);
}

type DownloadProgress = Pick<Download, "part" | "parts" | "downloaded_bytes" | "total_bytes">;

/** How far the file coming now is, 0 to 1 (0 while its size is unknown). */
export function partShare(d: DownloadProgress): number {
  if (d.part === 0 || !d.total_bytes) return 0;
  return Math.min(1, (d.downloaded_bytes ?? 0) / d.total_bytes);
}

/**
 * What a download is doing, in words: "Downloading the picture: 45% of 120 MB · 12 MB/s · 0:42 left".
 * YouTube sends the picture and the sound separately, and yt-dlp puts them together at the end.
 */
export function downloadText(d: DownloadProgress & Pick<Download, "speed_bps" | "eta_s">): string {
  if (d.part === 0) return "Looking up the video…";
  const share = partShare(d);
  if (share >= 1 && d.part >= d.parts) {
    return d.parts > 1 ? "Putting the picture and the sound together…" : "Finishing…";
  }
  const what =
    d.parts === 2 ? (d.part === 1 ? " the picture" : " the sound") : d.parts > 2 ? ` part ${d.part} of ${d.parts}` : "";
  let text = `Downloading${what}: ${Math.floor(share * 100)}%`;
  if (d.total_bytes) text += ` of ${formatBytes(d.total_bytes)}`;
  if (d.speed_bps) text += ` · ${formatBytes(d.speed_bps)}/s`;
  if (d.eta_s != null) text += ` · ${formatSeconds(d.eta_s)} left`;
  return text;
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
  voice: "Pick a voice",
};

/** Review 1's voice choice: the value of its `voice:` line ("auto", "2", …); null if it has none. */
export function voiceLine(text: string): string | null {
  const line = text.split("\n").find((l) => /^voice:/i.test(l.trim()));
  return line === undefined ? null : line.trim().slice("voice:".length).trim();
}

/** The review text with its `voice:` line set to this choice; unchanged if it has no such line. */
export function setVoiceLine(text: string, choice: string): string {
  return text
    .split("\n")
    .map((l) => (/^voice:/i.test(l.trim()) ? `voice: ${choice}` : l))
    .join("\n");
}

/**
 * How full a likeness bar is: scores bunch up between 0.85 and 1 (anything human-like scores
 * above 0.8), so the bar spans 0.8 → 1.
 */
export function likenessShare(sim: number): number {
  return Math.min(1, Math.max(0, (sim - 0.8) / 0.2));
}

/** A stretch of the video, "01:20–01:38" (whole seconds). */
export function stretch(start: number, end: number): string {
  const mmss = (s: number) => {
    const whole = Math.round(s);
    return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
  };
  return `${mmss(start)}–${mmss(end)}`;
}

/** What a person has to do while the job (or a run) waits, in words. */
export function waitingText(job: Pick<Job, "waiting_for">): string {
  const kind = job.waiting_for?.kind ?? "";
  return WAITING[kind] ?? `Waiting for ${kind || "a person"}`;
}

/**
 * The browser notification for a job that just stopped running: done, waiting for you, or failed;
 * `hash` is the screen it opens. null while it still runs.
 */
export function jobNotice(job: Pick<Job, "run" | "state" | "waiting_for" | "error">): { title: string; body: string; hash: string } | null {
  const run = encodeURIComponent(job.run);
  if (job.state === "waiting") {
    const kind = job.waiting_for?.kind ?? "";
    const page = kind === "voice" ? "voices" : kind === "shorts" ? "shorts" : "review";
    return { title: `${job.run}: your turn`, body: waitingText(job), hash: `#/${page}/${run}` };
  }
  if (job.state === "done") return { title: `${job.run}: done`, body: "Everything it was asked to make is ready.", hash: "#/" };
  if (job.state === "failed") return { title: `${job.run}: stopped`, body: job.error ?? "It stopped; see the studio.", hash: "#/" };
  return null;
}

/** 0.4213 → "$0.42", 0.0025 → "$0.0025": what model requests cost, readable at any size. */
export function usd(amount: number): string {
  if (amount >= 0.01) return `$${amount.toFixed(2)}`;
  return `$${Number(amount.toPrecision(2))}`;
}

/** A price per million tokens: "$2 in · $10 out per M tokens". */
export function priceText(p: { in: number; out: number }): string {
  const n = (x: number) => (x >= 1 ? String(Number(x.toFixed(2))) : String(Number(x.toPrecision(2))));
  return `$${n(p.in)} in · $${n(p.out)} out per M tokens`;
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
