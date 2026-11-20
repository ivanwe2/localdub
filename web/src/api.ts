// The server's API (server/src/lib.rs). The types mirror the Rust structs that serde turns into JSON.

export type JobState = "running" | "done" | "waiting" | "failed";

export interface WaitingFor {
  kind: string; // "review1", "review2" or "shorts"
  file: string;
}

/** One request to a model: text ("chat"), voice clone ("tts"), transcription ("asr"), separation. */
export interface Call {
  kind: string;
  model: string;
  label: string;
  seconds: number;
  tokens: number | null;
  tok_per_s: number | null;
  prompt_tokens: number | null;
  prompt_per_s: number | null;
  chars: number | null;
  audio_seconds: number | null;
}

/** The one run the server is working on, or did last (server/src/job.rs). */
export interface Job {
  run: string;
  state: JobState;
  stages: string[];
  done: string[];
  current: string | null;
  waiting_for: WaitingFor | null;
  error: string | null;
  calls: Call[]; // the latest model requests, oldest first
  log: string[]; // the engine's latest messages, oldest first
}

/** A model llama-swap has running. */
export interface RunningModel {
  model: string;
  state: string; // "starting", "ready", "stopping"
  name: string;
  description: string;
}

export interface Gpu {
  id: string;
  name: string;
  util_percent: number;
  memory_used_bytes: number;
  memory_total_bytes: number;
  temperature_c: number;
  power_w: number;
}

/** The models and GPUs right now (server/src/status.rs). */
export interface Status {
  models: RunningModel[];
  gpus: Gpu[];
  memory: { used_bytes: number; total_bytes: number } | null;
  error: string | null;
  at_ms: number;
}

/** A run's results, as paths relative to its folder (served from /files/<run>/<path>). */
export interface RunFiles {
  video: string | null;
  subtitles: string[];
  shorts: string[];
}

/** What a run waits for: a file to edit and approve, then the run is continued. */
export interface Waiting {
  kind: string; // "review1", "review2" or "shorts"
  lang: string | null; // for Shorts: "dub" or "original"
  file: string; // relative to the run's folder
}

/** One run on disk (server/src/runs.rs). */
export interface RunSummary {
  name: string;
  channel: string;
  created: string;
  source: string;
  seconds: number | null;
  done: string[];
  finished: boolean;
  files: RunFiles;
  waiting_for: Waiting | null;
}

/** The file a run waits for, with its text. */
export interface Review {
  kind: string;
  lang: string | null;
  file: string;
  text: string;
}

/** An uploaded video. */
export interface Video {
  name: string;
  path: string; // what POST /api/runs takes as `video`
  bytes: number;
}

/** The body of POST /api/runs (server/src/start.rs). */
export interface StartRequest {
  video: string;
  channel: string;
  name: string;
  no_review: boolean;
  shorts: number;
  /** A new channel's languages (codes like "bg", "en") and, optionally, its speaker. */
  source?: string;
  target?: string;
  speaker?: Speaker;
}

/** Only the translation's grammar uses it (first-person forms in gendered languages). */
export type Speaker = "man" | "woman";

/** A channel and the languages it dubs from and into (server/src/channels.rs). */
export interface Channel {
  name: string;
  source: string;
  target: string;
  source_name: string;
  target_name: string;
  speaker: Speaker | null;
  has_voice: boolean;
}

/** A language the engine knows (`localdub languages`). */
export interface Language {
  code: string;
  name: string;
  dub: boolean; // can a channel be dubbed into it?
  dictionary: string | null;
  dictionary_installed: boolean;
  apt: string | null; // the Debian package with the dictionary
}

export async function listChannels(): Promise<Channel[]> {
  return (await check(await fetch("/api/channels"))).json();
}

export async function listLanguages(): Promise<Language[]> {
  return (await check(await fetch("/api/languages"))).json();
}

/** The server's answer when it isn't 2xx: the status and its plain-text reason. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message || `HTTP ${status}`);
    this.status = status;
  }
}

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text()).trim());
  }
  return response;
}

export async function listRuns(): Promise<RunSummary[]> {
  return (await check(await fetch("/api/runs"))).json();
}

export async function startRun(request: StartRequest): Promise<Job> {
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return (await check(response)).json();
}

async function post<T = Job>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await check(response)).json();
}

/** A video downloaded from a link with yt-dlp on the server (server/src/download.rs). */
export interface Download {
  url: string;
  state: "downloading" | "done" | "failed";
  title: string | null;
  /** Which file is coming now, from 1 (0: not started yet), of how many: picture, then sound. */
  part: number;
  parts: number;
  downloaded_bytes: number | null; // of the file coming now
  total_bytes: number | null;
  speed_bps: number | null;
  eta_s: number | null;
  video: Video | null; // when done
  error: string | null;
  log: string[]; // yt-dlp's warnings and errors
}

/** Downloads the video at this link into the server's videos folder (one download at a time). */
export function startDownload(url: string): Promise<Download> {
  return post<Download>("/api/downloads", { url });
}

/** Calls `onDownload` with the download now and every time it changes; returns a stop function. */
export function followDownload(onDownload: (download: Download | null) => void): () => void {
  return follow("/api/download/events", onDownload);
}

const runPath = (run: string) => `/api/runs/${encodeURIComponent(run)}`;

/** Goes on where the run stopped: after a review or an error, or renders the picked Shorts. */
export function continueRun(run: string): Promise<Job> {
  return post(`${runPath(run)}/continue`);
}

/** Proposes Shorts for a finished run; the run then waits for you to pick them. */
export function makeShorts(run: string, lang: "dub" | "original"): Promise<Job> {
  return post(`${runPath(run)}/shorts`, { lang });
}

/** A voice sample to listen to: the sample itself and the test sentences cloned from it. */
export interface VoiceSample {
  sim: number | null; // how much the clones sound like the speaker, 0..1 (null: not scored)
  path: string; // relative to the run, for fileUrl
  tests: string[];
}

/** A run's voice samples (server/src/voice.rs, from `localdub voice`). */
export interface VoiceSamples {
  lang: string;
  lines: string[]; // the test sentences, in the order of each sample's tests
  scored: boolean;
  candidates: (VoiceSample & { n: number; start: number; end: number })[];
  current: (VoiceSample & { from: string }) | null; // the channel's voice now
  picked: number | string | null;
}

/** The run's voice samples, or null if it has none yet. */
export async function getVoiceSamples(run: string): Promise<VoiceSamples | null> {
  const response = await fetch(`${runPath(run)}/voice`);
  if (response.status === 404) return null;
  return (await check(response)).json();
}

/** Makes voice samples from the run's speech to compare with the channel's voice now (a GPU job). */
export function makeVoiceSamples(run: string): Promise<Job> {
  return post(`${runPath(run)}/voice`);
}

/** Makes a candidate (or "current": keep it) the channel's voice. */
export function pickVoice(run: string, pick: number | "current"): Promise<Job> {
  return post(`${runPath(run)}/voice/pick`, { pick });
}

/** Dubs a finished run again with the channel's voice. */
export function revoice(run: string): Promise<Job> {
  return post(`${runPath(run)}/revoice`);
}

export async function getReview(run: string): Promise<Review> {
  return (await check(await fetch(`${runPath(run)}/review`))).json();
}

export async function saveReview(run: string, text: string): Promise<void> {
  await check(
    await fetch(`${runPath(run)}/review`, {
      method: "PUT",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: text,
    }),
  );
}

export async function listVideos(): Promise<Video[]> {
  return (await check(await fetch("/api/videos"))).json();
}

/**
 * Uploads a video under `name`, calling `onProgress` with 0..1 as it goes. XMLHttpRequest, not
 * fetch: only it reports how much of an upload has been sent.
 */
export function uploadVideo(file: File, name: string, onProgress: (share: number) => void): Promise<Video> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", `/api/videos/${encodeURIComponent(name)}`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve(JSON.parse(request.responseText));
      else reject(new ApiError(request.status, request.responseText.trim()));
    };
    request.onerror = () => reject(new ApiError(0, "the upload broke off (is the server still running?)"));
    request.send(file);
  });
}

/** Calls `onJob` with the job now and every time it changes; returns a function that stops. */
export function followJob(onJob: (job: Job | null) => void): () => void {
  return follow("/api/job/events", onJob);
}

/** Calls `onStatus` with the models' and GPUs' status every couple of seconds; returns a stop function. */
export function followStatus(onStatus: (status: Status) => void): () => void {
  return follow("/api/status/events", onStatus);
}

function follow<T>(path: string, onValue: (value: T) => void): () => void {
  const events = new EventSource(path); // reconnects by itself if the connection drops
  events.onmessage = (event) => onValue(JSON.parse(event.data));
  return () => events.close();
}

/** Where the browser fetches one of a run's files. */
export function fileUrl(run: string, path: string): string {
  const parts = [run, ...path.split("/")].map(encodeURIComponent);
  return `/files/${parts.join("/")}`;
}
