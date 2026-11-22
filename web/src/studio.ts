// Pure helpers for the studio screens (timeline, meters, stage strips, reviews, Shorts); tested in
// studio.test.ts.

import type { Call, Job, RunSummary, Segment } from "./api";

/** 401 → "00:06:41"; with `short`, 72 → "01:12" (hours only when there are some). */
export function timecode(seconds: number, short = false): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return short && h === 0 ? `${two(m)}:${two(s)}` : `${two(h)}:${two(m)}:${two(s)}`;
}

/** The pipeline's stages, in order, and how the page names them. */
export const STAGES = ["prep", "separate", "transcribe", "spelling", "review1", "translate", "review2", "voice", "mix"];

/** A Shorts-only run's stages: the transcript, then its Shorts in the video's language. */
export const SHORTS_STAGES = [...STAGES.slice(0, STAGES.indexOf("review1") + 1), "shorts-original"];

/** The stages of a run on disk, by its mode. */
export function runStages(run: { mode?: string } | null): string[] {
  return run?.mode === "shorts" ? SHORTS_STAGES : STAGES;
}

export function stageName(stage: string): string {
  return stage.replace(/^review(\d)$/, "review $1").replace(/^shorts-/, "Shorts, ");
}

/** How much room a stage gets in a strip (roughly how long it takes; never too thin to read). */
const GROW: Record<string, number> = {
  prep: 1, separate: 2, transcribe: 2, spelling: 2, review1: 1.5, translate: 3, review2: 1.5, voice: 5, mix: 1,
  "shorts-original": 3, "shorts-dub": 3,
};

export type StageCls = "done" | "you" | "now" | "wait" | "todo" | "fail";

export interface StageItem {
  stage: string;
  name: string;
  cls: StageCls;
  note: string;
  grow: number;
}

/**
 * The stage strip of a run: what's done (with its time; a review: "approved"), what runs now or
 * waits for you, and what's next. `job` is the server's job when it is about this run.
 */
export function stageStrip(
  run: (Pick<RunSummary, "done" | "timings" | "waiting_for"> & { mode?: string }) | null,
  job: Job | null,
  now = Date.now(),
): StageItem[] {
  const own = job != null && job.stages.length > 0;
  const stages = own ? job.stages : runStages(run);
  // a job with its own stages counts only what it did (fixing a line redoes a finished run's voice and mix)
  const done = new Set(own ? job.done : (run?.done ?? []));
  const timings = { ...(run?.timings ?? {}), ...(job?.timings ?? {}) };
  const current = job?.current ?? null;
  const waitingKind = job?.state === "waiting" ? job.waiting_for?.kind : run?.waiting_for?.kind;
  // the stage that waits: a review by its name; Shorts by their language ("shorts-original")
  const waitingStage = waitingKind === "shorts" ? `shorts-${run?.waiting_for?.lang ?? "original"}` : waitingKind;
  return stages.map((stage) => {
    const review = stage.startsWith("review");
    const shorts = stage.startsWith("shorts-");
    let cls: StageCls = "todo";
    let note = "";
    if (done.has(stage) || (shorts && timings[stage] != null)) {
      cls = review ? "you" : "done";
      note = review ? "approved" : timings[stage] != null ? clock(timings[stage]) : "";
    } else if (stage === current && job?.state === "running") {
      cls = "now";
      const p = job.progress;
      note = p && p.stage === stage && p.total > 0 ? `${p.done} of ${p.total}` : job.stage_started_ms ? clock((now - job.stage_started_ms) / 1000) : "";
    } else if (stage === current && job?.state === "failed") {
      cls = "fail";
      note = "stopped here";
    } else if ((stage === current && job?.state === "waiting") || (!current && waitingStage === stage)) {
      cls = "wait";
      note = "your turn";
    }
    return { stage, name: stageName(stage), cls, note, grow: GROW[stage] ?? 1.5 };
  });
}

/** 107 → "1:47", 5 → "0:05". */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

export type RunState = "air" | "wait" | "done" | "fail" | "stop";

const WAITS: Record<string, string> = {
  review1: "Your turn · review 1 of 2",
  review2: "Your turn · review 2 of 2",
  voice: "Your turn · pick a voice",
  shorts: "Your turn · pick the Shorts",
};

/** A run's state in words, from the run on disk and the job when it is about this run. */
export function runState(run: RunSummary, job: Job | null): { text: string; cls: RunState } {
  const mine = job?.run === run.name ? job : null;
  if (mine?.state === "running") return { text: `On air · ${stageName(mine.current ?? "starting")}`, cls: "air" };
  if (mine?.state === "failed" && !run.finished) return { text: `Failed · ${mine.error ?? "see the log"}`, cls: "fail" };
  if (run.waiting_for?.kind === "review1" && run.mode === "shorts") return { text: "Your turn · the transcript", cls: "wait" };
  if (run.waiting_for) return { text: WAITS[run.waiting_for.kind] ?? "Your turn", cls: "wait" };
  if (run.finished) {
    const n = run.files.shorts.length;
    return { text: n ? `Done · ${n} Short${n === 1 ? "" : "s"}` : "Done", cls: "done" };
  }
  const last = run.done.at(-1);
  return { text: last ? `Stopped after ${stageName(last)}` : "Not started", cls: "stop" };
}

/** The mini strip of a run in the list: one class per stage. */
export function miniStrip(run: RunSummary, job: Job | null): StageCls[] {
  return stageStrip(run, job?.run === run.name ? job : null).map((s) => s.cls);
}

/**
 * A sine-like line for the background (an SVG path, 0–`width` wide, centred on y = 100): smooth
 * curves of `length` per wave, `amp` high. The width is a whole number of waves, so it loops.
 */
export function sinePath(length: number, amp: number, width = 2880): string {
  let d = `M0 100 Q${length / 4} ${100 - 2 * amp} ${length / 2} 100`;
  for (let x = length; x <= width; x += length / 2) d += ` T${x} 100`;
  return d;
}

/** A waveform as an SVG path (1000 × 100): one vertical stroke per step, as tall as it is loud. */
export function peaksPath(values: number[]): string {
  if (!values.length) return "";
  const step = 1000 / values.length;
  return values
    .map((v, i) => {
      const h = Math.max(0.5, (Math.min(100, Math.max(0, v)) / 100) * 46);
      const x = ((i + 0.5) * step).toFixed(1);
      return `M${x} ${(50 - h).toFixed(1)}V${(50 + h).toFixed(1)}`;
    })
    .join("");
}

/** The ruler's marks for a video of `seconds`: a round step that gives at most `most` of them. */
export function rulerTicks(seconds: number, most = 8): { label: string; left: string }[] {
  if (!(seconds > 0)) return [];
  const steps = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const step = steps.find((s) => seconds / s <= most) ?? 7200;
  const ticks = [];
  for (let t = 0; t < seconds - step * 0.4; t += step) ticks.push({ label: timecode(t, true), left: at(t, seconds) });
  ticks.push({ label: timecode(seconds, true), left: "100%" });
  return ticks;
}

/** Where a time falls on a timeline of `length` seconds, as a CSS percentage. */
export function at(seconds: number, length: number): string {
  if (!length) return "0%";
  return `${Math.min(100, Math.max(0, (seconds / length) * 100)).toFixed(3)}%`;
}

export type ClipCls = "done" | "now" | "ghost";

export interface Clip {
  id: number;
  left: string;
  width: string;
  src: string;
  tgt: string;
  text: ClipCls; // the dub language's text track
  dub: ClipCls; // the cloned voice track
}

/**
 * The timeline's clips: one per line, placed by its time; the dub's text and voice are "done"
 * once the run has them, "now" for the line the engine works on, else "ghost".
 */
export function clips(segments: Segment[], length: number, run: Pick<RunSummary, "done"> | null, job: Job | null): Clip[] {
  const done = new Set([...(run?.done ?? []), ...(job?.done ?? [])]);
  const p = job?.state === "running" ? job.progress : null;
  return segments.map((s) => {
    const translated = done.has("translate") || (p?.stage === "translate" && p.line != null && s.id <= p.line);
    let text: ClipCls = translated || s.tgt ? "done" : "ghost";
    if (p?.stage === "translate" && p.line != null && s.id === p.line + 1) text = "now";
    let dub: ClipCls = done.has("voice") ? "done" : "ghost";
    if (p?.stage === "voice" && p.line != null && p.label?.startsWith("voicing")) {
      dub = s.id < p.line ? "done" : s.id === p.line ? "now" : "ghost";
    }
    if (p?.stage === "voice" && p.label?.startsWith("re-voicing") && s.id === p.line) dub = "now"; // a line being fixed
    return {
      id: s.id,
      left: at(s.start, length),
      width: at(Math.max(0.2, s.end - s.start), length),
      src: s.src,
      tgt: s.tgt ?? "",
      text,
      dub,
    };
  });
}

/** What makes a voiced line worth a listen, as check.txt says it; [] if nothing does. */
export function lineNotes(s: Segment): string[] {
  const notes: string[] = [];
  const t = s.take;
  if (s.tgt?.trim() && !t) notes.push("no audio");
  if (t && !t.ok) notes.push(`failed the check after ${t.tries} take${t.tries === 1 ? "" : "s"}${t.why ? ` (${t.why})` : ""}; heard “${t.heard}”`);
  if (t && (s.overlap ?? 0) > 0.1) notes.push(`runs ${(s.overlap ?? 0).toFixed(1)}s into the next line (${t.fit})`);
  for (const flag of s.flags ?? []) notes.push(flag);
  if (s.shortened_from) notes.push(`shortened to fit, was: “${s.shortened_from}”`);
  return notes;
}

/**
 * How many of `leds` light for `value` out of `max`: on a log scale (tokens per second spread
 * from 1 to hundreds) or a straight one.
 */
export function ledsLit(value: number | null, max: number, leds = 24, log = false): number {
  if (value == null || value <= 0) return 0;
  const share = log ? Math.log(Math.max(1, value)) / Math.log(max) : value / max;
  return Math.round(Math.min(1, Math.max(0, share)) * leds);
}

/** The LEDs' classes: neutral, then amber, then on-air red at the top. */
export function ledClasses(lit: number, leds = 24): string[] {
  return Array.from({ length: leds }, (_, i) =>
    i >= lit ? "" : i >= leds - 3 ? "hot" : i >= leds - 8 ? "warm" : "on",
  );
}

/** The latest speed of a kind of call: tokens per second for text, else times real time. */
export function latestSpeed(calls: Call[], kind: "chat" | "tts" | "asr"): { value: number; model: string } | null {
  for (let i = calls.length - 1; i >= 0; i--) {
    const c = calls[i];
    if (c.kind !== kind) continue;
    if (kind === "chat" && c.tok_per_s != null) return { value: c.tok_per_s, model: c.model };
    if (kind !== "chat" && c.audio_seconds != null && c.seconds > 0) return { value: c.audio_seconds / c.seconds, model: c.model };
  }
  return null;
}

/** The speed column of a call in the requests table. */
export function callSpeed(c: Call): string {
  if (c.tok_per_s != null) return `${c.tok_per_s.toFixed(0)} tok/s`;
  if (c.audio_seconds != null && c.seconds > 0) return `${(c.audio_seconds / c.seconds).toFixed(1)}×`;
  return "";
}

// --- reviews -----------------------------------------------------------------------------------

export type Note =
  | { kind: "learned" | "fixed" | "name"; pairs: [string, string][] }
  | { kind: "unknown"; words: string[] }
  | { kind: "budget"; slot: number | null; chars: number; budget: number; over: boolean }
  | { kind: "note"; text: string };

export interface ReviewLine {
  index: number; // its line in the file
  label: string; // "BG", "EN", …
  text: string;
}

export interface Block {
  id: number;
  at: string; // "01:12"
  notes: Note[];
  lines: ReviewLine[];
}

export interface ParsedReview {
  status: string;
  voice: string | null;
  glossary: [string, string][] | null; // review 2 only
  blocks: Block[];
}

const BLOCK = /^\[(\d+)\]\s+(\S+)\s*(.*)$/;
const LABELLED = /^([A-Za-z]{2,3}):\s?(.*)$/;

function pairs(list: string): [string, string][] {
  return list
    .split(/,\s*(?=\S+\s+->)/)
    .map((p) => p.replace(/\s*\(not applied\)\s*$/, "").split(/\s+->\s+/))
    .filter((p) => p.length === 2)
    .map((p) => [p[0].trim(), p[1].trim()] as [string, string]);
}

function notesOf(header: string): Note[] {
  const notes: Note[] = [];
  let slot: number | null = null;
  for (const part of header.split(/\s{3,}/).map((p) => p.trim()).filter(Boolean)) {
    let m: RegExpMatchArray | null;
    if ((m = part.match(/^(learned|fixed):\s*(.*)$/))) notes.push({ kind: m[1] as "learned" | "fixed", pairs: pairs(m[2]) });
    else if ((m = part.match(/^name\?\s*(.*)$/))) notes.push({ kind: "name", pairs: pairs(m[1]) });
    else if ((m = part.match(/^unknown:\s*(.*)$/))) notes.push({ kind: "unknown", words: m[1].split(/,\s*/).filter(Boolean) });
    else if ((m = part.match(/^slot\s+([\d.]+)s$/))) slot = Number(m[1]);
    else if ((m = part.match(/^(\d+)\/(\d+) chars$/))) notes.push({ kind: "budget", slot, chars: Number(m[1]), budget: Number(m[2]), over: false });
    else if (part === "OVER") {
      const b = notes.find((n) => n.kind === "budget");
      if (b && b.kind === "budget") b.over = true;
    } else notes.push({ kind: "note", text: part.replace(/^\[|\]$/g, "") });
  }
  return notes;
}

/** A review file (review-1-transcript.txt or review-2-translation.txt) as blocks of lines. */
export function parseReview(text: string): ParsedReview {
  const lines = text.split("\n");
  const out: ParsedReview = { status: "pending", voice: null, glossary: null, blocks: [] };
  let block: Block | null = null;
  let inGlossary = false;
  lines.forEach((raw, index) => {
    const line = raw.trim();
    const low = line.toLowerCase();
    if (low.startsWith("status:")) {
      out.status = line.slice(7).trim().toLowerCase();
      return;
    }
    if (low.startsWith("voice:")) {
      out.voice = line.slice(6).trim();
      return;
    }
    if (low.startsWith("glossary:")) {
      inGlossary = true;
      out.glossary = [];
      return;
    }
    const b = line.match(BLOCK);
    if (b) {
      inGlossary = false;
      block = { id: Number(b[1]), at: b[2], notes: notesOf(b[3]), lines: [] };
      out.blocks.push(block);
      return;
    }
    if (!line || line.startsWith("#")) return;
    if (inGlossary && line.includes("=")) {
      const [a, ...rest] = line.split("=");
      if (a.trim() && rest.join("=").trim()) out.glossary?.push([a.trim(), rest.join("=").trim()]);
      return;
    }
    const l = line.match(LABELLED);
    if (block && l) block.lines.push({ index, label: l[1].toUpperCase(), text: l[2] });
  });
  return out;
}

/** The file with one labelled line's text changed. */
export function setReviewLine(text: string, line: ReviewLine, value: string): string {
  const lines = text.split("\n");
  lines[line.index] = `${line.label}: ${value.replace(/\n/g, " ")}`;
  return lines.join("\n");
}

/** `sentence` with the first whole-word `from` replaced by `to` (any alphabet). */
export function replaceWord(sentence: string, from: string, to: string): string {
  const escaped = from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return sentence.replace(new RegExp(`(?<![\\p{L}\\p{N}])${escaped}(?![\\p{L}\\p{N}])`, "u"), to);
}

/** The words in a sentence to mark: unknown words, names to check, and the model's fixes. */
export function marks(block: Block): string[] {
  const out: string[] = [];
  for (const n of block.notes) {
    if (n.kind === "unknown") out.push(...n.words);
    if (n.kind === "name") out.push(...n.pairs.map((p) => p[0]));
    if (n.kind === "fixed" || n.kind === "learned") out.push(...n.pairs.map((p) => p[1]));
  }
  return out;
}

/** A sentence split into plain and marked parts, for highlighting `words` in it. */
export function splitMarks(sentence: string, words: string[]): { text: string; mark: boolean }[] {
  const wanted = [...new Set(words.filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!wanted.length) return [{ text: sentence, mark: false }];
  const pattern = wanted.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${pattern})(?![\\p{L}\\p{N}])`, "gu");
  const parts: { text: string; mark: boolean }[] = [];
  let last = 0;
  for (const m of sentence.matchAll(re)) {
    const i = m.index ?? 0;
    if (i > last) parts.push({ text: sentence.slice(last, i), mark: false });
    parts.push({ text: m[0], mark: true });
    last = i + m[0].length;
  }
  if (last < sentence.length) parts.push({ text: sentence.slice(last), mark: false });
  return parts;
}

/** The file with its glossary section (review 2) replaced by these terms. */
export function setGlossary(text: string, terms: [string, string][]): string {
  const lines = text.split("\n");
  const start = lines.findIndex((l) => l.trim().toLowerCase().startsWith("glossary:"));
  if (start === -1) return text;
  let end = start + 1;
  while (end < lines.length && !BLOCK.test(lines[end].trim()) && (lines[end].trim() === "" || lines[end].includes("="))) end++;
  const body = terms.filter(([a, b]) => a.trim() && b.trim()).map(([a, b]) => `${a.trim()} = ${b.trim()}`);
  lines.splice(start + 1, end - start - 1, ...body, "");
  return lines.join("\n");
}

// --- Shorts -------------------------------------------------------------------------------------

/** "00:10-00:40 (30s)" → [10, 40]; null if it isn't that. */
export function parseWhen(when: string): [number, number] | null {
  const m = when.match(/(\d+):(\d+)(?::(\d+))?-(\d+):(\d+)(?::(\d+))?/);
  if (!m) return null;
  const secs = (a: string, b: string, c?: string) => (c ? Number(a) * 3600 + Number(b) * 60 + Number(c) : Number(a) * 60 + Number(b));
  return [secs(m[1], m[2], m[3]), secs(m[4], m[5], m[6])];
}

/** The sentences a Shorts candidate's line cuts ("sentences 3-9"); null if it has none. */
export function candidateSentences(text: string, line: number): [number, number] | null {
  const m = text.split("\n")[line]?.match(/sentences\s+(\d+)\s*-\s*(\d+)/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

/**
 * The candidates file with one candidate cut from sentence `first` to `last` instead (the
 * renderer reads "sentences A-B"); its times are updated from the run's lines.
 */
export function setCandidateSentences(text: string, line: number, first: number, last: number, segments: Segment[]): string {
  const lines = text.split("\n");
  const a = segments.find((s) => s.id === first);
  const b = segments.find((s) => s.id === last);
  if (!a || !b || first > last) return text;
  const mm = (t: number) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  lines[line] = lines[line]
    .replace(/sentences\s+\d+\s*-\s*\d+/, `sentences ${first}-${last}`)
    .replace(/\d+:\d+(?::\d+)?-\d+:\d+(?::\d+)?\s+\(\d+s\)/, `${mm(a.start)}-${mm(b.end)} (${Math.round(b.end - a.start)}s)`);
  return lines.join("\n");
}
