// Shorts: the proposed ones on a timeline of the whole video, a 9:16 preview, cuts moved a whole
// sentence at a time, ticked ones rendered. The candidates file stays the source of truth.

import { useEffect, useMemo, useRef, useState } from "react";

import {
  type Channel,
  continueRun,
  fileUrl,
  getReview,
  getSegments,
  getWaveform,
  makeShorts,
  originalUrl,
  type RunSummary,
  saveReview,
  type Segment,
  type Waveform,
} from "../api";
import { approve, candidates, toggleCandidate } from "../format";
import { go } from "../hooks";
import { at, candidateSentences, parseWhen, peaksPath, rulerTicks, setCandidateSentences, timecode } from "../studio";
import { ErrorText, message, PlayIcon, StopIcon } from "./common";

type Lang = "dub" | "original";

type ShortsScreenProps = { name: string | null; runs: RunSummary[]; channels: Channel[]; busy: boolean; onChange: () => void };

export default function ShortsScreen({ name, runs, channels, busy, onChange }: ShortsScreenProps) {
  const run = runs.find((r) => r.name === name) ?? null;
  const waitingLang = run?.waiting_for?.kind === "shorts" ? ((run.waiting_for.lang as Lang | null) ?? null) : null;
  const shortsOnly = run?.mode === "shorts"; // no dub: Shorts in the original only
  const [lang, setLang] = useState<Lang>(waitingLang ?? (shortsOnly ? "original" : "dub"));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (waitingLang) setLang(waitingLang);
    else if (shortsOnly) setLang("original");
  }, [waitingLang, shortsOnly, name]);

  if (!name || !run) {
    const finished = runs.filter(canCut);
    return (
      <>
        <div className="page-title">
          <div>
            <div className="kicker">Shorts</div>
            <h1>{name ? `No run called ${name}` : "Shorts come after the dub or the transcript"}</h1>
            <p>Pick a run: localdub proposes the strongest moments, you tick the ones to render.</p>
          </div>
        </div>
        {finished.length > 0 && (
          <section className="panel" style={{ marginTop: 24 }}>
            <h2>Runs to cut Shorts from</h2>
            {finished.map((r) => (
              <div key={r.name} className="run-row">
                <strong style={{ flex: "1 1 auto" }}>{r.name}</strong>
                <a className="btn small" href={`#/shorts/${encodeURIComponent(r.name)}`}>
                  Shorts
                </a>
              </div>
            ))}
          </section>
        )}
      </>
    );
  }

  const channel = channels.find((c) => c.name === run.channel);
  const names: Record<Lang, string> = { dub: channel?.target_name ?? "the dub", original: channel?.source_name ?? "the original" };
  const rendered = run.files.shorts.filter((p) => p.startsWith(`shorts/${lang}/`));

  async function make() {
    setError(null);
    try {
      await makeShorts(run!.name, lang);
      onChange();
      go({ page: "studio" });
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <>
      <div className="page-title">
        <div>
          <div className={`kicker${waitingLang ? " amber" : ""}`}>{waitingLang ? "Pick the Shorts" : "Shorts"}</div>
          <h1>{run.name}</h1>
          <p>Ranked by the vision model from the picture, the motion and what is said. Tick the ones to render.</p>
        </div>
        <div className="tabs" role="group" aria-label="Which Shorts">
          {((shortsOnly ? ["original"] : ["dub", "original"]) as Lang[]).map((l) => (
            <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}>
              {l === "dub" ? "Dub" : "Original"} · {names[l]}
            </button>
          ))}
        </div>
      </div>
      {lang === waitingLang ? (
        <Picker key={`${run.name}-${lang}`} run={run} preview={lang === "original" ? (originalUrl(run) ?? dubUrl(run)) : dubUrl(run)} busy={busy} onChange={onChange} />
      ) : (
        <section className="panel" style={{ marginTop: 24 }}>
          <div className="panel-head">
            <h2>{rendered.length ? `${rendered.length} rendered` : "None yet"}</h2>
            {canCut(run) && !run.waiting_for && (
              <button type="button" className="btn primary" disabled={busy} onClick={make}>
                Propose Shorts in {names[lang]}
              </button>
            )}
          </div>
          {!canCut(run) && (
            <p className="empty">{shortsOnly ? "Shorts come after the transcript: this run isn’t there yet." : "Shorts come after the dub: this run isn’t dubbed yet."}</p>
          )}
          {run.waiting_for && run.waiting_for.kind !== "shorts" && <p className="empty">The run waits for something else first.</p>}
          {waitingLang && waitingLang !== lang && <p className="empty">Pick the {names[waitingLang]} Shorts first.</p>}
          {busy && <p className="small muted">A run is on air; proposing Shorts needs the GPUs, so it waits until it’s done.</p>}
          <ErrorText error={error} />
          {rendered.length > 0 && (
            <div className="row" style={{ marginTop: 18, alignItems: "flex-start", gap: 16 }}>
              {rendered.map((p) => (
                <figure key={p} style={{ margin: 0 }}>
                  <video className="short-video" controls preload="metadata" src={fileUrl(run.name, p)} />
                  <figcaption className="mono small muted" style={{ maxWidth: 160, overflowWrap: "anywhere" }}>
                    <a href={fileUrl(run.name, p)} download>
                      {p.split("/").pop()}
                    </a>
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </section>
      )}
    </>
  );
}

function detail(details: string[], key: string): string {
  const line = details.find((d) => d.toLowerCase().startsWith(`${key}:`));
  return line ? line.slice(key.length + 1).trim() : "";
}

/** Shorts can be cut from it: its dub is done (a Shorts-only run: its transcript is checked). */
function canCut(run: RunSummary): boolean {
  return run.mode === "shorts" ? run.done.includes("review1") : run.finished;
}

function dubUrl(run: RunSummary): string | null {
  return run.files.video ? fileUrl(run.name, run.files.video) : null;
}

function Picker({ run, preview, busy, onChange }: { run: RunSummary; preview: string | null; busy: boolean; onChange: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [asText, setAsText] = useState(false);
  const [selected, setSelected] = useState(0);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [wave, setWave] = useState<Waveform | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    getReview(run.name)
      .then((r) => setText(r.text))
      .catch((e) => setError(message(e)));
    getSegments(run.name)
      .then(setSegments)
      .catch(() => setSegments([]));
    getWaveform(run.name)
      .then(setWave)
      .catch(() => setWave(null));
  }, [run.name]);

  const list = useMemo(() => (text ? candidates(text) : []), [text]);
  const length = run.seconds ?? wave?.seconds ?? 0;
  const c = list[selected] ?? list[0];
  const when = c ? parseWhen(c.when) : null;
  const range = c && text ? candidateSentences(text, c.line) : null;
  const lastId = segments.at(-1)?.id ?? 0;
  const speech = useMemo(() => (wave?.speech ? peaksPath(wave.speech) : ""), [wave]);
  const startAt = when?.[0];

  useEffect(() => {
    const v = video.current;
    if (v && startAt != null) {
      v.pause();
      v.currentTime = startAt;
      setPlaying(false);
    }
  }, [selected, startAt]);

  function togglePlay() {
    const v = video.current;
    if (!v || !when) return;
    if (playing) {
      v.pause();
      setPlaying(false);
      return;
    }
    if (v.currentTime < when[0] || v.currentTime >= when[1]) v.currentTime = when[0];
    v.play().catch(() => setPlaying(false));
    setPlaying(true);
  }

  function move(first: number, last: number) {
    if (!text || !c) return;
    setText(setCandidateSentences(text, c.line, first, last, segments));
  }

  async function render() {
    if (!text) return;
    setError(null);
    setWorking(true);
    try {
      await saveReview(run.name, approve(text));
      await continueRun(run.name);
      onChange();
      go({ page: "studio" });
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }

  if (text === null) return error ? <ErrorText error={error} /> : <p className="empty">Loading the proposals…</p>;
  const picked = list.filter((x) => x.picked).length;
  const ticks = rulerTicks(length);
  const caption = c ? detail(c.details, "starts") || c.title : "";

  return (
    <>
      <section className="panel" aria-label="The whole video" style={{ marginTop: 24 }}>
        <div className="panel-head">
          <span className="kicker">The whole video · the speaker’s voice</span>
          <span className="mono small muted">{length ? timecode(length) : ""}</span>
        </div>
        <div className="timeline-box" style={{ marginTop: 12 }}>
          <div style={{ minWidth: 720 }}>
            <div className="ruler">
              <div className="ticks" style={{ height: 22 }}>
                {ticks.map((t) => (
                  <span key={t.label} style={{ left: t.left, transform: t.left === "100%" ? "translateX(-100%)" : undefined }}>
                    {t.label}
                  </span>
                ))}
              </div>
            </div>
            <div className="shorts-lane">
              {speech && (
                <svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true" style={{ color: "var(--faint)" }}>
                  <path d={speech} stroke="currentColor" strokeWidth={1.2} vectorEffect="non-scaling-stroke" fill="none" />
                </svg>
              )}
              {list.map((x, i) => {
                const w = parseWhen(x.when);
                if (!w || !length) return null;
                const current = i === selected;
                return (
                  <button
                    key={x.line}
                    type="button"
                    className={`marker${x.picked ? " picked" : ""}`}
                    aria-current={current}
                    aria-label={`Short ${x.n}: ${x.title}`}
                    style={{ left: at(w[0], length), width: at(w[1] - w[0], length) }}
                    onClick={() => setSelected(i)}
                  >
                    {x.n}
                    {current && (
                      <>
                        <span className="handle" style={{ left: -5 }} aria-hidden="true" />
                        <span className="handle" style={{ right: -5 }} aria-hidden="true" />
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      <div className="columns">
        <section className="main panel" aria-labelledby="short">
          {asText ? (
            <>
              <label className="kicker" htmlFor="raw-shorts">
                The candidates file
              </label>
              <textarea id="raw-shorts" className="raw" style={{ marginTop: 10 }} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
            </>
          ) : c ? (
            <div className="row" style={{ alignItems: "flex-start", gap: 28 }}>
              <div className="preview" aria-label="Preview, 9 by 16">
                {preview && (
                  <video
                    ref={video}
                    muted
                    playsInline
                    preload="metadata"
                    src={preview}
                    onTimeUpdate={(e) => {
                      if (when && e.currentTarget.currentTime >= when[1]) {
                        e.currentTarget.pause();
                        setPlaying(false);
                      }
                    }}
                  />
                )}
                <span className="time">{when ? timecode(when[0], true) : ""}</span>
                <div className="caption">
                  <span>{caption}</span>
                </div>
                <button type="button" className="big-play" aria-pressed={playing} aria-label={playing ? "Stop the preview" : "Play the preview"} onClick={togglePlay}>
                  {playing ? <StopIcon size={16} /> : <PlayIcon size={16} />}
                </button>
              </div>
              <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                <div className="kicker">
                  Short {c.n} · rank {c.n} of {list.length}
                </div>
                <h2 id="short" style={{ margin: "6px 0 0", fontSize: 26 }}>
                  {c.title}
                </h2>
                <div className="mono muted" style={{ marginTop: 6 }}>
                  {when ? `${timecode(when[0], true)}–${timecode(when[1], true)} · ${Math.round(when[1] - when[0])} s` : c.when}
                  {range ? ` · sentences ${range[0]}–${range[1]}` : ""}
                </div>
                <dl className="details">
                  {(["hook", "why", "screen", "starts", "ends"] as const).map((k) =>
                    detail(c.details, k) ? (
                      <div key={k} style={{ display: "contents" }}>
                        <dt className="kicker">{k}</dt>
                        <dd>{detail(c.details, k)}</dd>
                      </div>
                    ) : null,
                  )}
                </dl>
                {range && segments.length > 0 && (
                  <>
                    <div className="row" style={{ gap: 8, marginTop: 22 }}>
                      <span className="small muted" style={{ marginRight: 4 }}>
                        Start
                      </span>
                      <button type="button" className="step-btn" aria-label="Start one sentence earlier" disabled={range[0] <= 1} onClick={() => move(range[0] - 1, range[1])}>
                        −1
                      </button>
                      <button type="button" className="step-btn" aria-label="Start one sentence later" disabled={range[0] >= range[1]} onClick={() => move(range[0] + 1, range[1])}>
                        +1
                      </button>
                      <span className="small muted" style={{ margin: "0 4px 0 12px" }}>
                        End
                      </span>
                      <button type="button" className="step-btn" aria-label="End one sentence earlier" disabled={range[1] <= range[0]} onClick={() => move(range[0], range[1] - 1)}>
                        −1
                      </button>
                      <button type="button" className="step-btn" aria-label="End one sentence later" disabled={range[1] >= lastId} onClick={() => move(range[0], range[1] + 1)}>
                        +1
                      </button>
                    </div>
                    <p className="small faint" style={{ margin: "8px 0 0" }}>
                      The cut moves a whole sentence at a time, so a Short never starts or ends mid-word.
                    </p>
                  </>
                )}
              </div>
            </div>
          ) : (
            <p className="empty">No proposals in the file.</p>
          )}
        </section>

        <aside className="side panel" aria-labelledby="proposed">
          <div className="panel-head">
            <h2 id="proposed">Proposed</h2>
            <span className="kicker">{picked} ticked</span>
          </div>
          <div style={{ marginTop: 10 }}>
            {list.map((x, i) => (
              <div key={x.line} className="cand-row" aria-current={i === selected}>
                <input type="checkbox" checked={x.picked} aria-label={`Render Short ${x.n}`} onChange={() => setText(toggleCandidate(text, x.line))} />
                <button type="button" className="pick" onClick={() => setSelected(i)}>
                  <span className="mono faint" style={{ marginRight: 8 }}>
                    {x.n}
                  </span>
                  <span style={{ fontWeight: 500 }}>{x.title}</span>
                </button>
                <span className="mono small muted">{x.when.match(/\((\d+s)\)/)?.[1] ?? ""}</span>
              </div>
            ))}
          </div>
          <ErrorText error={error} />
          <button type="button" className="btn go wide" style={{ marginTop: 18, minHeight: 44 }} disabled={working || busy || picked === 0} onClick={render}>
            {picked === 1 ? "Render 1 Short" : `Render ${picked} Shorts`}
          </button>
          <button type="button" className="btn small" style={{ marginTop: 10 }} aria-pressed={asText} onClick={() => setAsText(!asText)}>
            {asText ? "Show as a list" : "Edit as text (titles, cuts)"}
          </button>
          <p className="small faint" style={{ margin: "10px 0 0" }}>
            Rendered on the box, 9:16, with the captions burned in.
          </p>
        </aside>
      </div>
    </>
  );
}
