import { type FormEvent, useCallback, useEffect, useState } from "react";

import { fileUrl, followJob, type Job, listRuns, type RunSummary, startRun } from "./api";
import { formatSeconds, progress, suggestName, waitingText } from "./format";

export default function App() {
  const [job, setJob] = useState<Job | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);

  const refreshRuns = useCallback(() => {
    listRuns()
      .then((list) => {
        setRuns(list);
        setRunsError(null);
      })
      .catch((e: Error) => setRunsError(`Couldn't load the runs: ${e.message}`));
  }, []);

  useEffect(() => followJob(setJob), []);
  // The list changes when a run starts or ends, so reload it then.
  useEffect(refreshRuns, [refreshRuns, job?.run, job?.state]);

  return (
    <main>
      <header>
        <h1>localdub</h1>
        <p className="muted">Dub a video into another language in the creator's own voice.</p>
      </header>
      <JobPanel job={job} />
      <NewRun busy={job?.state === "running"} />
      <Runs runs={runs} error={runsError} />
    </main>
  );
}

/** The run the server is working on (or did last), live from /api/job/events. */
function JobPanel({ job }: { job: Job | null }) {
  if (!job) {
    return <section className="card muted">Nothing has run since the server started.</section>;
  }
  const percent = Math.round(progress(job) * 100);
  const continueWith =
    job.waiting_for?.kind === "shorts" ? `localdub shorts ${job.run}` : `localdub run ${job.run}`;
  return (
    <section className="card" aria-live="polite">
      <div className="row">
        <h2>{job.run}</h2>
        <span className={`badge ${job.state}`}>{job.state}</span>
      </div>
      <div className="bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${percent}%` }} />
      </div>
      <ol className="stages">
        {job.stages.map((stage) => (
          <li
            key={stage}
            className={job.done.includes(stage) ? "done" : stage === job.current ? "current" : ""}
          >
            {stage}
          </li>
        ))}
      </ol>
      {job.state === "waiting" && job.waiting_for && (
        <p className="note">
          {waitingText(job)}: edit <code>{job.waiting_for.file}</code>, change its first line to{" "}
          <code>status: approved</code>, then continue with <code>{continueWith}</code>.
        </p>
      )}
      {job.state === "failed" && <p className="error">{job.error}</p>}
    </section>
  );
}

/** Reads a remembered form value; the page works the same without storage. */
function remembered(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // private window or blocked storage: nothing to remember then
  }
}

/** The form that starts a run (POST /api/runs). */
function NewRun({ busy }: { busy: boolean }) {
  const [video, setVideo] = useState("");
  const [channel, setChannel] = useState(() => remembered("localdub.channel"));
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [noReview, setNoReview] = useState(true);
  const [shorts, setShorts] = useState(0);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function changeVideo(value: string) {
    setVideo(value);
    if (!nameEdited) setName(suggestName(value));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSending(true);
    try {
      await startRun({ video: video.trim(), channel: channel.trim(), name: name.trim(), no_review: noReview, shorts });
      remember("localdub.channel", channel.trim());
      setVideo("");
      setName("");
      setNameEdited(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="card">
      <h2>New run</h2>
      <form onSubmit={submit}>
        <label>
          Video
          <input
            value={video}
            onChange={(e) => changeVideo(e.target.value)}
            placeholder="/root/dub/videos/market.mp4"
            required
          />
          <small className="muted">A path on the machine where the server runs.</small>
        </label>
        <div className="row">
          <label>
            Channel
            <input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="mychannel" required />
          </label>
          <label>
            Run name
            <input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameEdited(true);
              }}
              placeholder="market"
              required
            />
          </label>
        </div>
        <div className="row">
          <label className="check">
            <input type="checkbox" checked={noReview} onChange={(e) => setNoReview(e.target.checked)} />
            Skip the reviews
          </label>
          <label className="check">
            Shorts in each language
            <input
              type="number"
              min={0}
              max={6}
              value={shorts}
              onChange={(e) => setShorts(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={sending || busy}>
          {busy ? "A run is going…" : sending ? "Starting…" : "Start"}
        </button>
      </form>
    </section>
  );
}

/** Every run on disk, newest first, with its results. */
function Runs({ runs, error }: { runs: RunSummary[]; error: string | null }) {
  return (
    <section className="card">
      <h2>Runs</h2>
      {error && <p className="error">{error}</p>}
      {!error && runs.length === 0 && <p className="muted">No runs yet.</p>}
      <ul className="runs">
        {runs.map((run) => (
          <RunItem key={run.name} run={run} />
        ))}
      </ul>
    </section>
  );
}

function RunItem({ run }: { run: RunSummary }) {
  const [watching, setWatching] = useState(false);
  const { video, subtitles, shorts } = run.files;
  const facts = [run.channel, new Date(run.created).toLocaleString()];
  if (run.seconds != null) facts.push(formatSeconds(run.seconds));
  return (
    <li>
      <div className="row">
        <strong>{run.name}</strong>
        <span className="muted">{facts.join(" · ")}</span>
        <span className={`badge ${run.finished ? "done" : "partial"}`}>
          {run.finished ? "dubbed" : (run.done.at(-1) ?? "new")}
        </span>
      </div>
      <div className="links">
        {video && (
          <>
            <button type="button" className="link" onClick={() => setWatching(!watching)}>
              {watching ? "Hide video" : "Watch"}
            </button>
            <a href={fileUrl(run.name, video)} download>
              Download video
            </a>
          </>
        )}
        {subtitles.map((path) => (
          <a key={path} href={fileUrl(run.name, path)} download>
            {path}
          </a>
        ))}
        {shorts.map((path) => (
          <a key={path} href={fileUrl(run.name, path)} target="_blank" rel="noreferrer">
            {path.split("/").pop()}
          </a>
        ))}
      </div>
      {watching && video && <video controls src={fileUrl(run.name, video)} />}
    </li>
  );
}
