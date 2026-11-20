// Every run as a track: its stages at a glance, what it waits for, and what can be done next.

import { useState } from "react";

import { type Channel, continueRun, fileUrl, type Job, type RunSummary } from "../api";
import { miniStrip, runState, timecode } from "../studio";
import { ErrorText, message } from "./common";

type Filter = "all" | "wait" | "done" | "stop";

type RunListProps = {
  runs: RunSummary[];
  error: string | null;
  job: Job | null;
  channels: Channel[];
  focus: string | null;
  onFocus: (name: string) => void;
  busy: boolean;
  onChange: () => void;
};

export default function RunList({ runs, error, job, channels, focus, onFocus, busy, onChange }: RunListProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const states = runs.map((r) => ({ run: r, state: runState(r, job) }));
  const count = (f: Filter) => states.filter((s) => matches(f, s.state.cls)).length;
  const shown = states.filter((s) => matches(filter, s.state.cls));
  const filters: [Filter, string][] = [
    ["all", "All"],
    ["wait", "Your turn"],
    ["done", "Done"],
    ["stop", "Stopped"],
  ];
  return (
    <section className="panel" aria-labelledby="runs" style={{ marginTop: 24 }}>
      <div className="panel-head" style={{ alignItems: "center" }}>
        <div className="row" style={{ gap: 12 }}>
          <h2 id="runs" style={{ margin: 0, fontSize: 20 }}>
            Runs
          </h2>
          <div className="row" role="group" aria-label="Show" style={{ gap: 6 }}>
            {filters.map(([f, label]) => (
              <button key={f} type="button" className="chip" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {label} {count(f)}
              </button>
            ))}
          </div>
        </div>
        <a className="btn primary" href="#/new">
          New run
        </a>
      </div>
      <ErrorText error={error} />
      {!error && runs.length === 0 && <p className="empty">No runs yet: start one with a video.</p>}
      <div style={{ marginTop: 14 }}>
        {shown.map(({ run, state }) => (
          <RunRow
            key={run.name}
            run={run}
            state={state}
            job={job}
            channel={channels.find((c) => c.name === run.channel)}
            focused={run.name === focus}
            onFocus={() => onFocus(run.name)}
            busy={busy}
            onChange={onChange}
          />
        ))}
      </div>
    </section>
  );
}

function matches(filter: Filter, cls: string): boolean {
  if (filter === "all") return true;
  if (filter === "stop") return cls === "stop" || cls === "fail";
  return cls === filter;
}

type RunRowProps = {
  run: RunSummary;
  state: ReturnType<typeof runState>;
  job: Job | null;
  channel: Channel | undefined;
  focused: boolean;
  onFocus: () => void;
  busy: boolean;
  onChange: () => void;
};

function RunRow({ run, state, job, channel, focused, onFocus, busy, onChange }: RunRowProps) {
  const [error, setError] = useState<string | null>(null);
  const name = encodeURIComponent(run.name);
  const kind = run.waiting_for?.kind;
  const onAir = job?.run === run.name && job.state === "running";
  const transcribed = run.done.includes("spelling");
  const { video, subtitles, shorts } = run.files;
  const files = [...(video ? [video] : []), ...subtitles, ...shorts];

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
    <article className={`run-row${focused ? " focus" : ""}`}>
      <div style={{ flex: "2 1 220px", minWidth: 0 }}>
        <button type="button" className="run-name" onClick={onFocus} title="Show it on the timeline above">
          {run.name}
        </button>
        <div className="small muted">
          {run.channel}
          {channel ? ` · ${channel.source.toUpperCase()} → ${channel.target.toUpperCase()}` : ""} · {new Date(run.created).toLocaleDateString()}
        </div>
      </div>
      <span className="mono muted" style={{ flex: "0 0 88px", fontSize: 14 }}>
        {run.seconds != null ? timecode(run.seconds) : "--:--:--"}
      </span>
      <div className="mini" aria-hidden="true">
        {miniStrip(run, job).map((cls, i) => (
          <span key={i} className={cls} />
        ))}
      </div>
      <span className={`run-state ${state.cls}`}>
        <span className="mark" />
        <span style={{ overflowWrap: "anywhere" }}>{state.text}</span>
      </span>
      <div className="run-actions">
        {(kind === "review1" || kind === "review2") && (
          <a className="btn go small" href={`#/review/${name}`}>
            Open review
          </a>
        )}
        {kind === "voice" && (
          <a className="btn go small" href={`#/voices/${name}`}>
            Pick a voice
          </a>
        )}
        {kind === "shorts" && (
          <a className="btn go small" href={`#/shorts/${name}`}>
            Pick Shorts
          </a>
        )}
        {!run.finished && !kind && !onAir && (
          <button type="button" className="btn small" disabled={busy} onClick={() => act(() => continueRun(run.name))}>
            Continue
          </button>
        )}
        {video && (
          <a className="btn small" href={fileUrl(run.name, video)} target="_blank" rel="noreferrer">
            Watch
          </a>
        )}
        {run.finished && kind !== "shorts" && (
          <a className="btn small" href={`#/shorts/${name}`}>
            Shorts
          </a>
        )}
        {transcribed && kind !== "voice" && (
          <a className="btn small" href={`#/voices/${name}`}>
            Voices
          </a>
        )}
        {files.length > 0 && (
          <details className="files">
            <summary className="btn small">Files</summary>
            <div className="file-list">
              {files.map((f) => (
                <a key={f} href={fileUrl(run.name, f)} download>
                  {f}
                </a>
              ))}
            </div>
          </details>
        )}
      </div>
      {error && (
        <div className="row-error">
          <ErrorText error={error} />
        </div>
      )}
    </article>
  );
}
