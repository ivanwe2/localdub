import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  type Call,
  type Channel,
  continueRun,
  type Download,
  fileUrl,
  followDownload,
  followJob,
  followStatus,
  getReview,
  type Job,
  type Language,
  listChannels,
  listLanguages,
  listRuns,
  listVideos,
  makeShorts,
  type Review,
  type RunSummary,
  saveReview,
  startDownload,
  startRun,
  type Status,
  uploadVideo,
  type Video,
} from "./api";
import {
  approve,
  candidates,
  defaultTarget,
  dictionaryNote,
  downloadText,
  findChannel,
  languageFields,
  nameFromTitle,
  partShare,
  setVoiceLine,
  voiceLine,
  formatBytes,
  formatSeconds,
  sparkline,
  progress,
  suggestName,
  toggleCandidate,
  uploadName,
  waitingText,
} from "./format";
import VoicePicker from "./VoicePicker";

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
      <AiPanel calls={job?.calls ?? []} />
      {job && job.log.length > 0 && <LogPanel lines={job.log} />}
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

const KIND: Record<string, string> = {
  chat: "text",
  tts: "voice",
  asr: "transcription",
  separation: "separation",
};

/**
 * What the AI is doing: the models running, each GPU (live from /api/status/events), and the
 * latest requests of this run with their speed.
 */
function AiPanel({ calls }: { calls: Call[] }) {
  const [status, setStatus] = useState<Status | null>(null);
  useEffect(() => followStatus(setStatus), []);

  const chats = calls.filter((c) => c.kind === "chat" && c.tok_per_s != null);
  const last = chats.at(-1);
  const speeds = chats.slice(-20).map((c) => c.tok_per_s ?? 0);
  return (
    <section className="card">
      <h2>AI</h2>
      {status?.error && <p className="error">{status.error}</p>}
      <div className="row">
        <div className="models">
          {status && status.models.length === 0 && <p className="muted">No model loaded.</p>}
          {status?.models.map((m) => (
            <div key={m.model} className="model">
              <span className={`badge ${m.state === "ready" ? "done" : "running"}`}>{m.state}</span>{" "}
              <strong>{m.name || m.model}</strong>
              {m.description && <div className="muted">{m.description}</div>}
            </div>
          ))}
        </div>
        {last && (
          <div className="speed">
            <div className="big">{last.tok_per_s?.toFixed(0)}</div>
            <div className="muted">tok/s · {last.model}</div>
            {speeds.length > 1 && (
              <svg viewBox="0 0 120 30" width="120" height="30" aria-label="speed of the latest requests">
                <polyline points={sparkline(speeds, 120, 28)} fill="none" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            )}
          </div>
        )}
      </div>
      <div className="gpus">
        {status?.gpus.map((g) => {
          const share = g.memory_total_bytes ? g.memory_used_bytes / g.memory_total_bytes : 0;
          return (
            <div key={g.id} className="gpu">
              <div className="row">
                <strong>GPU {g.id}</strong>
                <span className="muted">
                  {g.util_percent.toFixed(0)}% · {g.temperature_c.toFixed(0)} °C · {g.power_w.toFixed(0)} W
                </span>
              </div>
              <div className="bar" title={`${formatBytes(g.memory_used_bytes)} of ${formatBytes(g.memory_total_bytes)}`}>
                <div style={{ width: `${share * 100}%` }} />
              </div>
              <div className="muted small">
                {formatBytes(g.memory_used_bytes)} of {formatBytes(g.memory_total_bytes)} memory
              </div>
            </div>
          );
        })}
      </div>
      {calls.length > 0 && (
        <details>
          <summary>Latest requests ({calls.length})</summary>
          <table className="calls">
            <tbody>
              {[...calls].reverse().map((c, i) => (
                <tr key={calls.length - i}>
                  <td>{KIND[c.kind] ?? c.kind}</td>
                  <td>{c.label}</td>
                  <td className="muted">{c.model}</td>
                  <td className="num">{c.seconds.toFixed(1)} s</td>
                  <td className="num">{c.tok_per_s != null ? `${c.tok_per_s.toFixed(0)} tok/s` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}

/** The engine's own messages, the newest at the bottom. */
function LogPanel({ lines }: { lines: string[] }) {
  return (
    <section className="card">
      <details>
        <summary>Engine log</summary>
        <pre className="log">{lines.join("\n")}</pre>
      </details>
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

/**
 * Downloading a video from a link with yt-dlp on the server, live from /api/download/events. It
 * runs beside a dub (it needs no GPU). When a download this page saw going is done, `onDone` gets it.
 */
function DownloadBox({ onDone }: { onDone: (download: Download) => void }) {
  const [url, setUrl] = useState("");
  const [download, setDownload] = useState<Download | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Download["state"] | undefined>(undefined);

  useEffect(() => followDownload(setDownload), []);
  useEffect(() => {
    if (seen.current === "downloading" && download?.state === "done") onDone(download);
    seen.current = download?.state;
  }, [download, onDone]);

  const going = download?.state === "downloading";
  async function start() {
    if (!url.trim() || going) return;
    setError(null);
    setSending(true);
    try {
      await startDownload(url.trim()); // its progress comes through followDownload
      setUrl("");
    } catch (e) {
      setError(message(e));
    } finally {
      setSending(false);
    }
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault(); // Enter here downloads; it doesn't start the run
    void start();
  }

  const share = download ? partShare(download) : 0;
  return (
    <div className="download">
      <div className="row">
        <label>
          …or download it from a link
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={onKey}
            placeholder="https://www.youtube.com/watch?v=…"
          />
        </label>
        <button type="button" className="secondary" disabled={!url.trim() || going || sending} onClick={() => void start()}>
          {going ? "Downloading…" : sending ? "Starting…" : "Download"}
        </button>
      </div>
      <p className="muted small">Only videos you have the rights to dub.</p>
      {going && (
        <div aria-live="polite">
          <div className="muted">{download.title ?? download.url}</div>
          <div className="bar" role="progressbar" aria-valuenow={Math.round(share * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${share * 100}%` }} />
          </div>
          <p className="muted small">{downloadText(download)}</p>
        </div>
      )}
      {download?.state === "done" && download.video && (
        <p className="muted small">Downloaded: {download.video.name}</p>
      )}
      {download?.state === "failed" && <p className="error">The download failed: {download.error}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
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
  // a new channel's languages (an existing channel keeps its own)
  const [channels, setChannels] = useState<Channel[]>([]);
  const [languages, setLanguages] = useState<Language[] | null>(null); // null: not asked yet
  const [languagesError, setLanguagesError] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [speaker, setSpeaker] = useState("");

  const refreshVideos = useCallback(() => {
    listVideos()
      .then(setVideos)
      .catch(() => setVideos([]));
  }, []);
  useEffect(refreshVideos, [refreshVideos]);

  const refreshChannels = useCallback(() => {
    listChannels()
      .then(setChannels)
      .catch(() => setChannels([]));
  }, []);
  useEffect(refreshChannels, [refreshChannels, busy]); // a run that starts or ends may have made one

  const known = findChannel(channel, channels);
  const isNew = channel.trim() !== "" && !known;
  useEffect(() => {
    if (!isNew || languages !== null) return; // asked once, when a new channel's name is typed
    listLanguages()
      .then((list) => {
        setLanguages(list);
        setTarget((current) => current || defaultTarget(list));
      })
      .catch((e) => {
        setLanguages([]);
        setLanguagesError(message(e));
      });
  }, [isNew, languages]);

  function chooseVideo(path: string, title?: string) {
    setVideo(path);
    if (!nameEdited) setName((title && nameFromTitle(title)) || suggestName(path));
  }

  function downloaded(download: Download) {
    refreshVideos();
    if (download.video) chooseVideo(download.video.path, download.title ?? undefined);
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
      await startRun({
        video: video.trim(),
        channel: channel.trim(),
        name: name.trim(),
        no_review: noReview,
        shorts,
        ...languageFields(channel, channels, source, target, speaker),
      });
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
        <DownloadBox onDone={downloaded} />
        <label>
          …or a path on the server
          <input value={video} onChange={(e) => chooseVideo(e.target.value)} placeholder="/root/dub/excerpt.mp4" required />
        </label>
        <div className="row">
          <label>
            Channel
            <input
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              placeholder="mychannel"
              list="channels"
              required
            />
            <datalist id="channels">
              {channels.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.source_name} → {c.target_name}
                </option>
              ))}
            </datalist>
            {known && (
              <span className="muted">
                dubs {known.source_name} into {known.target_name}
                {known.has_voice ? "" : "; its voice is chosen in the first review"}
              </span>
            )}
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
        {isNew && (
          <fieldset>
            <legend>A new channel: its languages (they stay with the channel)</legend>
            {languagesError && <p className="error">Couldn't load the languages: {languagesError}</p>}
            <div className="row">
              <label>
                Video language
                <select value={source} onChange={(e) => setSource(e.target.value)} required>
                  <option value="">Choose…</option>
                  {(languages ?? []).map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.name}
                      {dictionaryNote(l)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Dub language
                <select value={target} onChange={(e) => setTarget(e.target.value)} required>
                  <option value="">Choose…</option>
                  {(languages ?? [])
                    .filter((l) => l.dub && l.code !== source)
                    .map((l) => (
                      <option key={l.code} value={l.code}>
                        {l.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Speaker
                <select value={speaker} onChange={(e) => setSpeaker(e.target.value)}>
                  <option value="">Not set</option>
                  <option value="man">A man</option>
                  <option value="woman">A woman</option>
                </select>
                <span className="muted">only for the grammar of "I" in gendered languages</span>
              </label>
            </div>
          </fieldset>
        )}
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
  const [voicing, setVoicing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const waitsForVoice = run.waiting_for?.kind === "voice";
  const transcribed = run.done.includes("spelling");
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
        {run.waiting_for && !waitsForVoice && (
          <button type="button" className="link" onClick={() => setReviewing(!reviewing)}>
            {reviewing ? "Close" : waitingText(run)}
          </button>
        )}
        {(waitsForVoice || (transcribed && !run.waiting_for)) && (
          <button type="button" className="link" onClick={() => setVoicing(!voicing)}>
            {voicing ? "Close the voices" : waitsForVoice ? waitingText(run) : "Voice…"}
          </button>
        )}
        {!run.finished && (!run.waiting_for || waitsForVoice) && (
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
      {voicing && <VoicePicker run={run.name} finished={run.finished} busy={busy} onChange={onChange} />}
      {reviewing && run.waiting_for && !waitsForVoice && (
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
  const voice = review.kind === "review1" ? voiceLine(text) : null; // the voice is chosen here too
  return (
    <div className="review">
      <p className="muted">
        <code>{review.file}</code>
        {shorts
          ? " · tick the Shorts to render (3 is a good number)."
          : " · fix what's wrong; the comments at the top explain the format."}
      </p>
      {voice !== null && (
        <VoicePicker
          run={run}
          busy={busy}
          review={{ chosen: voice, choose: (n) => edit(setVoiceLine(text, String(n))) }}
        />
      )}
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
