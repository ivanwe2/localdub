// A finished dub's lines, to fix one without redoing the rest: hear the original and the dub side by
// side, change the words or ask for another take. The server voices the line again, mixes the dub
// again and renders the dubbed Shorts with it again (POST /api/runs/{name}/lines/{id}).

import { useEffect, useRef, useState } from "react";

import { fileUrl, fixLine, type Segment } from "../api";
import { lineNotes, timecode } from "../studio";
import { ErrorText, message, PlayIcon, StopIcon } from "./common";

type Props = {
  name: string;
  segments: Segment[];
  picked: number | null;
  onPick: (id: number | null) => void;
  busy: boolean;
  src: string; // the languages' codes, for the labels
  tgt: string;
  version: string; // changes when the dub is mixed again, so the audio is fetched anew
  onStarted: () => void;
};

export default function LineFixer({ name, segments, picked, onPick, busy, src, tgt, version, onStarted }: Props) {
  const [all, setAll] = useState(false);
  const player = useRangePlayer();
  const worth = segments.filter((s) => lineNotes(s).length > 0);
  const shown = all ? segments : segments.filter((s) => lineNotes(s).length > 0 || s.id === picked);

  useEffect(() => {
    if (picked != null) document.getElementById(`line-${picked}`)?.scrollIntoView?.({ block: "nearest" });
  }, [picked]);

  return (
    <section className="lines" aria-labelledby="lines-title">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <div className="kicker" id="lines-title">
            Fix a line
          </div>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Pick a line here or on the timeline: listen to both, change the words or ask for another take.
          </p>
        </div>
        <div className="tabs" role="group" aria-label="Which lines">
          <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>
            Worth a listen · {worth.length}
          </button>
          <button type="button" aria-pressed={all} onClick={() => setAll(true)}>
            All · {segments.length}
          </button>
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="empty small">Every line passed the check and fits. Show all lines to fix one anyway.</p>
      ) : (
        <ol className="line-list">
          {shown.map((s) => {
            const notes = lineNotes(s);
            const open = picked === s.id;
            return (
              <li key={s.id} id={`line-${s.id}`} className={open ? "open" : undefined}>
                <button type="button" className="line-row" aria-expanded={open} onClick={() => onPick(open ? null : s.id)}>
                  <span className="tc">{s.id}</span>
                  <span className="tc muted">{timecode(s.start, true)}</span>
                  <span className="line-text">{s.tgt?.trim() ? s.tgt : <em className="muted">silent</em>}</span>
                  {s.fixed && <span className="chip ok">fixed</span>}
                  {notes.length > 0 && !open && <span className="line-note">{notes[0]}</span>}
                </button>
                {open && (
                  <LineEditor
                    key={`${s.id}:${s.tgt}`}
                    name={name}
                    s={s}
                    busy={busy}
                    src={src}
                    tgt={tgt}
                    version={version}
                    player={player}
                    onStarted={onStarted}
                  />
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

type EditorProps = {
  name: string;
  s: Segment;
  busy: boolean;
  src: string;
  tgt: string;
  version: string;
  player: RangePlayer;
  onStarted: () => void;
};

function LineEditor({ name, s, busy, src, tgt, version, player, onStarted }: EditorProps) {
  const [text, setText] = useState(s.tgt ?? "");
  const [error, setError] = useState<string | null>(null);
  const words = text.split(/\s+/).filter(Boolean).join(" ");
  const changed = words !== (s.tgt ?? "").trim();
  const notes = lineNotes(s);
  const audio = (file: string) => `${fileUrl(name, file)}?v=${encodeURIComponent(version)}`;
  const dubAt = s.take && s.placed != null ? s.placed : null;

  async function send(change: { text?: string; new_take?: boolean }) {
    setError(null);
    player.stop();
    try {
      await fixLine(name, s.id, change);
      onStarted();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <div className="line-editor">
      <div className="line-sides">
        <div>
          <div className="kicker">{src} · original</div>
          <p style={{ margin: "6px 0 10px" }}>{s.src}</p>
          <PlayButton
            label="Original"
            on={player.playing === `src-${s.id}`}
            onClick={() => player.toggle(`src-${s.id}`, audio("audio44.wav"), s.start - 0.15, s.end + 0.25)}
          />
        </div>
        <div>
          <label className="kicker" htmlFor={`line-${s.id}-text`}>
            {tgt} · dub
          </label>
          <textarea
            id={`line-${s.id}-text`}
            rows={2}
            value={text}
            maxLength={1000}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
            style={{ margin: "6px 0 10px" }}
          />
          <PlayButton
            label="Dub"
            disabled={dubAt == null}
            on={player.playing === `dub-${s.id}`}
            onClick={() => dubAt != null && player.toggle(`dub-${s.id}`, audio("dub-mix.wav"), dubAt - 0.15, dubAt + (s.take?.seconds ?? 0) + 0.25)}
          />
        </div>
      </div>
      {notes.length > 0 && (
        <ul className="line-notes">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {s.take && !notes.length && (
        <p className="muted small">
          Passed the check: heard “{s.take.heard}”; {s.take.fit}.
        </p>
      )}
      <div className="row" style={{ marginTop: 10 }}>
        <button type="button" className="btn primary" disabled={busy || !changed} onClick={() => send({ text: words })}>
          {words ? "Voice the new words" : "Leave the line silent"}
        </button>
        <button type="button" className="btn" disabled={busy || changed || !(s.tgt ?? "").trim()} onClick={() => send({ new_take: true })}>
          Another take
        </button>
        {changed && (
          <button type="button" className="btn" onClick={() => setText(s.tgt ?? "")}>
            Undo
          </button>
        )}
      </div>
      <p className="muted small" style={{ marginBottom: 0 }}>
        {busy
          ? "Something is on air; fix the line once it's done."
          : "The line is voiced and checked again, then the dub is mixed again (a minute or two). Dubbed Shorts with this line are rendered again."}
      </p>
      <ErrorText error={error} />
    </div>
  );
}

function PlayButton({ label, on, disabled, onClick }: { label: string; on: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" className="btn small" aria-pressed={on} disabled={disabled} onClick={onClick}>
      {on ? <StopIcon size={12} /> : <PlayIcon size={12} />} {on ? "Stop" : label}
    </button>
  );
}

type RangePlayer = { playing: string | null; toggle: (key: string, url: string, from: number, to: number) => void; stop: () => void };

/** One stretch of audio at a time (from `from` to `to` seconds of a long file), from small buttons. */
function useRangePlayer(): RangePlayer {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  useEffect(() => () => audio.current?.pause(), []); // stop when the list goes away

  function stop() {
    audio.current?.pause();
    setPlaying(null);
  }

  function toggle(key: string, url: string, from: number, to: number) {
    if (playing === key) return stop();
    const a = (audio.current ??= new Audio());
    a.pause();
    const begin = () => {
      a.currentTime = Math.max(0, from);
      a.play().catch(() => setPlaying(null));
    };
    a.ontimeupdate = () => {
      if (a.currentTime >= to) stop();
    };
    a.onended = () => setPlaying(null);
    a.onerror = () => setPlaying(null);
    if (a.getAttribute("src") !== url) {
      a.onloadedmetadata = begin;
      a.src = url;
    } else {
      a.onloadedmetadata = null;
      begin();
    }
    setPlaying(key);
  }

  return { playing, toggle, stop };
}
