// The server's API (server/src/lib.rs). The types mirror the Rust structs that serde turns into JSON.

export type JobState = "running" | "done" | "waiting" | "failed";

export interface WaitingFor {
  kind: string; // "review1", "review2" or "shorts"
  file: string;
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

async function post(path: string, body: unknown = {}): Promise<Job> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await check(response)).json();
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
  const events = new EventSource("/api/job/events");
  events.onmessage = (event) => onJob(JSON.parse(event.data));
  return () => events.close();
}

/** Where the browser fetches one of a run's files. */
export function fileUrl(run: string, path: string): string {
  const parts = [run, ...path.split("/")].map(encodeURIComponent);
  return `/files/${parts.join("/")}`;
}
