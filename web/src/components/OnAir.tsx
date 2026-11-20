// The run on air (or the one picked in the list): what it does now, its clocks and stages, and the
// whole video as editing-timeline tracks: the speaker's voice, both texts, the dub and the music.

import { type ReactNode, useEffect, useMemo, useState } from "react";

import {
  type Channel,
  continueRun,
  fileUrl,
  getSegments,
  getWaveform,
  type Job,
  type RunSummary,
  type Segment,
  type Waveform,
} from "../api";
import { waitingText } from "../format";
import { go, useNow } from "../hooks";
import { clips, peaksPath, rulerTicks, stageName, stageStrip, timecode } from "../studio";
import { ErrorText, message } from "./common";

type OnAirProps = {
  name: string;
  run: RunSummary | null;
  job: Job | null; // only when it is about this run
  channels: Channel[];
  busy: boolean;
  onChange: () => void;
  backToAir?: () => void; // set when another run than the one on air is shown
};

export default function OnAir({ name, run, job, channels, busy, onChange, backToAir }: OnAirProps) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [wave, setWave] = useState<Waveform | null>(null);
  const [watching, setWatching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const running = job?.state === "running";
  const now = useNow(running);
  const done = new Set([...(run?.done ?? []), ...(job?.done ?? [])]);
  const stamp = `${done.size}`; // the lines change when a stage finishes
  const separated = done.has("separate");

  useEffect(() => {
    let live = true;
    getSegments(name)
      .then((s) => live && setSegments(s))
      .catch(() => live && setSegments([]));
    return () => {
      live = false;
    };
  }, [name, stamp]);

  useEffect(() => {
    let live = true;
    setWave(null);
    if (separated)
      getWaveform(name)
        .then((w) => live && setWave(w))
        .catch(() => live && setWave(null));
    return () => {
      live = false;
    };
  }, [name, separated]);

  useEffect(() => setWatching(false), [name]);

  const channel = channels.find((c) => c.name === run?.channel);
  const length = run?.seconds ?? wave?.seconds ?? segments.at(-1)?.end ?? 0;
  const strip = stageStrip(run, job, now);
  const p = running ? job?.progress : null;

  let kicker = "Stopped";
  let kickerCls = "";
  if (running) {
    kicker = `On air · ${stageName(job?.current ?? "starting")}`;
    kickerCls = "air";
  } else if (job?.state === "waiting" || (!job && run?.waiting_for)) {
    kicker = `Waiting for you · ${waitingText(job?.state === "waiting" ? job : { waiting_for: run?.waiting_for ?? null })}`;
    kickerCls = "amber";
  } else if (job?.state === "failed") kicker = "Stopped · it failed";
  else if (run?.finished || job?.state === "done") kicker = backToAir ? "Done" : "Last run · done";

  const machine = Object.entries(run?.timings ?? {})
    .filter(([stage]) => !stage.startsWith("review"))
    .reduce((sum, [, s]) => sum + s, 0);
  const clockLabel = running ? "Elapsed" : job ? "Took" : "Machine time";
  const clockValue = job ? timecode(((job.ended_ms ?? now) - job.started_ms) / 1000) : machine ? timecode(machine) : "--:--:--";

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      onChange();
    } catch (e) {
      setError(message(e));
    }
  }

  const waitingKind = job?.state === "waiting" ? job.waiting_for?.kind : !job || job.state !== "running" ? run?.waiting_for?.kind : undefined;
  const page = waitingKind === "voice" ? "voices" : waitingKind === "shorts" ? "shorts" : "review";
  const video = run?.files.video;

  let status: ReactNode = null;
  if (running) {
    const line =
      p && p.total > 0
        ? `${p.label ? p.label[0].toUpperCase() + p.label.slice(1) : stageName(p.stage)}: ${p.done} of ${p.total}${p.label?.startsWith("checking") ? " takes" : " lines"}`
        : (job?.log.filter((l) => l.trim()).at(-1)?.trim() ?? "Starting…");
    status = <p className="muted">{line}</p>;
  } else if (waitingKind) {
    status = (
      <div className="row">
        <span className="muted">The run waits for you before it goes on.</span>
        <button type="button" className="btn go" onClick={() => go({ page, run: name })}>
          {waitingText({ waiting_for: { kind: waitingKind, file: "" } })}
        </button>
      </div>
    );
  } else if (job?.state === "failed" || (run && !run.finished && !running)) {
    status = (
      <div className="row">
        <span className="error">{job?.state === "failed" ? job.error : `Stopped after ${stageName(run?.done.at(-1) ?? "the start")}.`}</span>
        <button type="button" className="btn" disabled={busy} onClick={() => act(() => continueRun(name))}>
          Continue
        </button>
      </div>
    );
  } else if (run?.finished) {
    const shorts = run.files.shorts.length;
    status = (
      <div className="row">
        <span className="muted">
          The dubbed video and its subtitles are ready{shorts ? `, and ${shorts} Short${shorts === 1 ? "" : "s"}` : ""}.
        </span>
        {video && (
          <button type="button" className="btn" aria-expanded={watching} onClick={() => setWatching(!watching)}>
            {watching ? "Hide the video" : "Watch"}
          </button>
        )}
        <a className="btn" href={`#/shorts/${encodeURIComponent(name)}`}>
          Shorts
        </a>
      </div>
    );
  }

  return (
    <section className="panel" aria-labelledby="onair" style={{ marginTop: 36 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", gap: 20 }}>
        <div style={{ minWidth: 0 }}>
          <div className={`kicker ${kickerCls}`}>
            {kicker}
            {backToAir && (
              <>
                {" · "}
                <button type="button" className="btn small" onClick={backToAir} style={{ marginLeft: 8 }}>
                  Back to the run on air
                </button>
              </>
            )}
          </div>
          <h1 id="onair" style={{ margin: "6px 0 10px", fontSize: 36, fontWeight: 600, lineHeight: 1.1, overflowWrap: "anywhere" }}>
            {name}
          </h1>
          <div className="row" style={{ gap: 8 }}>
            {run && <span className="chip">{run.channel}</span>}
            {channel && (
              <span className="chip">
                {channel.source_name} → {channel.target_name}
              </span>
            )}
            {segments.length > 0 && <span className="chip">{segments.length} lines</span>}
          </div>
        </div>
        <div className="clocks">
          <div>
            <div className="kicker">{clockLabel}</div>
            <div className="tc">{clockValue}</div>
          </div>
          <div>
            <div className="kicker">Video</div>
            <div className="tc muted">{length ? timecode(length) : "--:--:--"}</div>
          </div>
        </div>
      </div>
      <div aria-live="polite" style={{ marginTop: 16 }}>
        {status}
      </div>
      <ErrorText error={error} />

      <ol className="stages" aria-label="Stages">
        {strip.map((s) => (
          <li key={s.stage} className={`stage ${s.cls}`} style={{ flex: `${s.grow} 1 64px` }}>
            <span>{s.name}</span>
            <small>{s.note || " "}</small>
          </li>
        ))}
      </ol>

      {length > 0 && (segments.length > 0 || wave?.speech) ? (
        <Timeline
          segments={segments}
          length={length}
          wave={wave}
          run={run}
          job={job}
          src={channel?.source.toUpperCase() ?? "SRC"}
          tgt={channel?.target.toUpperCase() ?? "DUB"}
        />
      ) : (
        <p className="empty small">The timeline fills in once the speaker’s voice is separated and the video transcribed.</p>
      )}

      {job && job.log.length > 0 && (
        <div className="log-lines" aria-label="The engine's latest messages">
          {job.log
            .filter((l) => l.trim())
            .slice(-2)
            .map((l, i) => (
              <span key={i}>{l.trim()}</span>
            ))}
        </div>
      )}
      {watching && video && <video className="player" controls src={fileUrl(name, video)} />}
    </section>
  );
}

type TimelineProps = {
  segments: Segment[];
  length: number;
  wave: Waveform | null;
  run: RunSummary | null;
  job: Job | null;
  src: string;
  tgt: string;
};

function Timeline({ segments, length, wave, run, job, src, tgt }: TimelineProps) {
  const laid = useMemo(() => clips(segments, length, run, job), [segments, length, run, job]);
  const speech = useMemo(() => (wave?.speech ? peaksPath(wave.speech) : ""), [wave]);
  const music = useMemo(() => (wave?.music ? peaksPath(wave.music) : ""), [wave]);
  const ticks = rulerTicks(length);
  const p = job?.state === "running" ? job.progress : null;
  const head = p?.at != null && length > 0 ? Math.min(1, Math.max(0, p.at / length)) : null;
  return (
    <div className="timeline-box">
      <div className="timeline" role="group" aria-label="The video as tracks">
        <div className="ruler">
          <div className="track-label" style={{ justifyContent: "flex-start" }}>
            VIDEO
          </div>
          <div className="ticks">
            {ticks.map((t) => (
              <span key={t.label} style={{ left: t.left, transform: t.left === "100%" ? "translateX(-100%)" : undefined }}>
                {t.label}
              </span>
            ))}
          </div>
        </div>
        <div className="track">
          <div className="track-label">
            ORIGINAL<small>{src} · voice</small>
          </div>
          <div className="lane">
            {speech ? (
              <svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true" style={{ color: "var(--muted)" }}>
                <path d={speech} stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" fill="none" />
              </svg>
            ) : (
              laid.map((c) => <div key={c.id} className="clip done" style={{ left: c.left, width: c.width, opacity: 0.5 }} />)
            )}
          </div>
        </div>
        <div className="track">
          <div className="track-label">
            TEXT<small>{src}</small>
          </div>
          <div className="lane">
            {laid.map((c) => (
              <div key={c.id} className="clip done" style={{ left: c.left, width: c.width }} title={`${c.id}. ${c.src}`}>
                {c.src}
              </div>
            ))}
          </div>
        </div>
        <div className="track">
          <div className="track-label">
            TEXT<small>{tgt}</small>
          </div>
          <div className="lane">
            {laid.map((c) => (
              <div key={c.id} className={`clip ${c.text}`} style={{ left: c.left, width: c.width }} title={c.tgt ? `${c.id}. ${c.tgt}` : undefined}>
                {c.text === "done" ? c.tgt : ""}
              </div>
            ))}
          </div>
        </div>
        <div className="track">
          <div className="track-label">
            DUB<small>{tgt} · cloned</small>
          </div>
          <div className="lane">
            {laid.map((c) => (
              <div key={c.id} className={`clip ${c.dub}`} style={{ left: c.left, width: c.width }}>
                {c.dub === "now" ? "voicing" : c.dub === "done" ? c.id : ""}
              </div>
            ))}
          </div>
        </div>
        <div className="track">
          <div className="track-label">
            MUSIC &amp; FX<small>kept as it is</small>
          </div>
          <div className="lane">
            {music && (
              <svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true" style={{ color: "var(--faint)" }}>
                <path d={music} stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" fill="none" />
              </svg>
            )}
          </div>
        </div>
        {head != null && (
          <div className="playhead" aria-hidden="true" style={{ left: `calc(120px + (100% - 120px) * ${head.toFixed(4)})` }}>
            <span>{timecode(p?.at ?? 0, true)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
