import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  continueRun,
  fileUrl,
  followJob,
  getReview,
  type Job,
  listRuns,
  listVideos,
  makeShorts,
  type Review,
  type RunSummary,
  saveReview,
  startRun,
  uploadVideo,
  type Video,
} from "./api";
import {
  approve,
  candidates,
  formatSeconds,
  progress,
  suggestName,
  toggleCandidate,
  uploadName,
  waitingText,
} from "./format";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

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
      .catch((e) => setRunsError(`Couldn't load the runs: ${message(e)}`));
  }, []);

  useEffect(() => followJob(setJob), []);
  // The list changes when a run starts or ends, so reload it then.
  useEffect(refreshRuns, [refreshRuns, job?.run, job?.state]);

  const busy = job?.state === "running";
  return (
    <main>
      <header>
        <h1>localdub</h1>
        <p className="muted">Dub a video into another language in the creator's own voice.</p>
      </header>
      <JobPanel job={job} />
      <NewRun busy={busy} />
      <Runs runs={runs} error={runsError} busy={busy} onChange={refreshRuns} />
    </main>
  );
}

/** The run the server is working on (or did last), live from /api/job/events. */
function JobPanel({ job }: { job: Job | null }) {
  if (!job) {
    return <section className="card muted">Nothing has run since the server started.</section>;
  }
  const percent = Math.round(progress(job) * 100);
  return (
    <section className="card" aria-live="polite">
      <div className="row">
        <h2>{job.run}</h2>
        <span className={`badge ${job.state}`}>{job.state}</span>
      </div>
      {job.stages.length > 0 && (
        <>
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
        </>
      )}
      {job.state === "running" && job.stages.length === 0 && <p className="muted">Working…</p>}
      {job.state === "waiting" && (
        <p className="note">
          {waitingText(job)}: open it under <strong>Runs</strong> below.
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

/** The form that starts a run (POST /api/runs), with uploading the video. */
function NewRun({ busy }: { busy: boolean }) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [video, setVideo] = useState("");
  const [upload, setUpload] = useState<number | null>(null); // 0..1 while uploading
  const [channel, setChannel] = useState(() => remembered("localdub.channel"));
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [noReview, setNoReview] = useState(true);
  const [shorts, setShorts] = useState(0);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshVideos = useCallback(() => {
    listVideos()
      .then(setVideos)
      .catch(() => setVideos([]));
  }, []);
  useEffect(refreshVideos, [refreshVideos]);

  function chooseVideo(path: string) {
    setVideo(path);
    if (!nameEdited) setName(suggestName(path));
  }

  async function uploadFile(file: File) {
    setError(null);
    setUpload(0);
    try {
      const uploaded = await uploadVideo(file, uploadName(file.name), setUpload);
      refreshVideos();
      chooseVideo(uploaded.path);
    } catch (e) {
      setError(message(e));
    } finally {
      setUpload(null);
    }
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
      setError(message(e));
    } finally {
      setSending(false);
    }
  }

  const uploading = upload !== null;
  return (
    <section className="card">
      <h2>New run</h2>
      <form onSubmit={submit}>
        <div className="row">
          <label>
            Video
            <select value={videos.some((v) => v.path === video) ? video : ""} onChange={(e) => chooseVideo(e.target.value)}>
              <option value="">{videos.length ? "Choose an uploaded video…" : "No uploaded videos yet"}</option>
              {videos.map((v) => (
                <option key={v.path} value={v.path}>
                  {v.name} ({(v.bytes / 1e6).toFixed(0)} MB)
                </option>
              ))}
            </select>
          </label>
          <label>
            Upload a video
            <input
              type="file"
              accept="video/*"
              disabled={uploading}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void uploadFile(file);
              }}
            />
          </label>
        </div>
        {uploading && (
          <div className="bar" role="progressbar" aria-valuenow={Math.round(upload * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${upload * 100}%` }} />
          </div>
        )}
        <label>
          …or a path on the server
          <input value={video} onChange={(e) => chooseVideo(e.target.value)} placeholder="/root/dub/excerpt.mp4" required />
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
        <button type="submit" disabled={sending || busy || uploading}>
          {busy ? "A run is going…" : sending ? "Starting…" : "Start"}
        </button>
      </form>
    </section>
  );
}

type RunsProps = { runs: RunSummary[]; error: string | null; busy: boolean; onChange: () => void };

/** Every run on disk, newest first, with its results and what can be done next. */
function Runs({ runs, error, busy, onChange }: RunsProps) {
  return (
    <section className="card">
      <h2>Runs</h2>
      {error && <p className="error">{error}</p>}
      {!error && runs.length === 0 && <p className="muted">No runs yet.</p>}
      <ul className="runs">
        {runs.map((run) => (
          <RunItem key={run.name} run={run} busy={busy} onChange={onChange} />
        ))}
      </ul>
    </section>
  );
}

function RunItem({ run, busy, onChange }: { run: RunSummary; busy: boolean; onChange: () => void }) {
  const [watching, setWatching] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { video, subtitles, shorts } = run.files;
  const facts = [run.channel, new Date(run.created).toLocaleString()];
  if (run.seconds != null) facts.push(formatSeconds(run.seconds));
  const state = run.finished ? "dubbed" : run.waiting_for ? "waiting" : (run.done.at(-1) ?? "new");

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      onChange();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <li>
      <div className="row">
        <strong>{run.name}</strong>
        <span className="muted">{facts.join(" · ")}</span>
        <span className={`badge ${run.finished ? "done" : run.waiting_for ? "waiting" : "partial"}`}>{state}</span>
      </div>
      <div className="links">
        {run.waiting_for && (
          <button type="button" className="link" onClick={() => setReviewing(!reviewing)}>
            {reviewing ? "Close" : waitingText(run)}
          </button>
        )}
        {!run.finished && !run.waiting_for && (
          <button type="button" className="link" disabled={busy} onClick={() => act(() => continueRun(run.name))}>
            Continue
          </button>
        )}
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
        {run.finished && !run.waiting_for && (
          <>
            <button type="button" className="link" disabled={busy} onClick={() => act(() => makeShorts(run.name, "dub"))}>
              Make Shorts (dubbed)
            </button>
            <button
              type="button"
              className="link"
              disabled={busy}
              onClick={() => act(() => makeShorts(run.name, "original"))}
            >
              Make Shorts (original)
            </button>
          </>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {reviewing && run.waiting_for && (
        <ReviewPanel
          run={run.name}
          busy={busy}
          onDone={() => {
            setReviewing(false);
            onChange();
          }}
        />
      )}
      {watching && video && <video controls src={fileUrl(run.name, video)} />}
    </li>
  );
}

/**
 * The file a run waits for. Reviews are edited as text, as in the CLI; Shorts are picked with
 * checkboxes (which edit the same text). "Approve and continue" marks it approved, saves it and
 * continues the run.
 */
function ReviewPanel({ run, busy, onDone }: { run: string; busy: boolean; onDone: () => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [text, setText] = useState("");
  const [asText, setAsText] = useState(false);
  const [saved, setSaved] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getReview(run)
      .then((r) => {
        setReview(r);
        setText(r.text);
      })
      .catch((e) => setError(message(e)));
  }, [run]);

  function edit(next: string) {
    setText(next);
    setSaved(false);
  }

  async function save(andContinue: boolean) {
    setError(null);
    setWorking(true);
    try {
      const final = andContinue ? approve(text) : text;
      await saveReview(run, final);
      setText(final);
      setSaved(true);
      if (andContinue) {
        await continueRun(run);
        onDone();
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }

  if (!review) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  const shorts = review.kind === "shorts";
  const list = shorts ? candidates(text) : [];
  const picked = list.filter((c) => c.picked).length;
  return (
    <div className="review">
      <p className="muted">
        <code>{review.file}</code>
        {shorts
          ? " · tick the Shorts to render (3 is a good number)."
          : " · fix what's wrong; the comments at the top explain the format."}
      </p>
      {shorts && !asText ? (
        <ul className="candidates">
          {list.map((c) => (
            <li key={c.line}>
              <label className="check">
                <input type="checkbox" checked={c.picked} onChange={() => edit(toggleCandidate(text, c.line))} />
                <strong>
                  {c.n}. {c.title}
                </strong>
                <span className="muted">{c.when}</span>
              </label>
              {c.details.map((d) => (
                <div key={d} className="muted detail">
                  {d}
                </div>
              ))}
            </li>
          ))}
        </ul>
      ) : (
        <textarea value={text} onChange={(e) => edit(e.target.value)} rows={18} spellCheck={false} />
      )}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="button" disabled={working || busy || (shorts && picked === 0)} onClick={() => save(true)}>
          {shorts ? `Render ${picked} Short${picked === 1 ? "" : "s"}` : "Approve and continue"}
        </button>
        {!shorts && (
          <button type="button" className="secondary" disabled={working || saved} onClick={() => save(false)}>
            {saved ? "Saved" : "Save for later"}
          </button>
        )}
        {shorts && (
          <button type="button" className="link" onClick={() => setAsText(!asText)}>
            {asText ? "Show as a list" : "Edit as text (change cuts or titles)"}
          </button>
        )}
      </div>
    </div>
  );
}
