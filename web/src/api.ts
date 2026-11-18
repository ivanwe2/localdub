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
