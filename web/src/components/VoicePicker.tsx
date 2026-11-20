// The voice picker: samples from a run's speech and the channel's voice now, each with test
// sentences cloned from it, to listen to and compare; the one picked becomes the channel's voice.
// Used on the Voices screen and inside review 1 (there the choice goes into the review's voice line).

import { useEffect, useRef, useState } from "react";

import {
  fileUrl,
  getVoiceSamples,
  makeVoiceSamples,
  pickVoice,
  revoice,
  type VoiceSample,
  type VoiceSamples,
} from "../api";
import { likenessShare, stretch } from "../format";
import { ErrorText, message, PlayIcon, StopIcon } from "./common";

type Player = { playing: string | null; toggle: (url: string) => void };

/** One clip at a time, from small buttons (one audio element instead of a player per clip). */
function usePlayer(): Player {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  useEffect(() => () => audio.current?.pause(), []); // stop when the picker closes

  function toggle(url: string) {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying(null);
      audio.current.onerror = () => setPlaying(null);
    }
    const a = audio.current;
    if (playing === url) {
      a.pause();
      setPlaying(null);
      return;
    }
    a.src = url;
    a.play().catch(() => setPlaying(null));
    setPlaying(url);
  }
  return { playing, toggle };
}

type CardProps = {
  run: string;
  kicker: string;
  title: string;
  from?: string;
  sample: VoiceSample;
  lines: string[];
  chosen: boolean;
  player: Player;
  action: { label: string; disabled: boolean; onClick: () => void };
};

function VoiceCard({ run, kicker, title, from, sample, lines, chosen, player, action }: CardProps) {
  const url = fileUrl(run, sample.path);
  const playing = player.playing === url;
  return (
    <article className={`voice-card${chosen ? " chosen" : ""}`} aria-label={title}>
      <div style={{ flex: "1 1 200px", minWidth: 0 }}>
        <div className={`kicker${chosen ? " air" : ""}`}>{kicker}</div>
        <div style={{ marginTop: 4, font: "600 18px/1.3 var(--display)" }}>{title}</div>
        {from && <div className="mono small muted" style={{ marginTop: 2, overflowWrap: "anywhere" }}>{from}</div>}
      </div>
      <div style={{ flex: "1 1 200px", minWidth: 0 }}>
        {sample.sim != null ? (
          <>
            <div className="row" style={{ justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
              <span className="small muted">sounds like the speaker</span>
              <span className="mono" style={{ fontSize: 20, fontWeight: 500 }}>
                {sample.sim.toFixed(3)}
              </span>
            </div>
            <div className="likeness" role="meter" aria-label="Likeness" aria-valuenow={sample.sim} aria-valuemin={0.8} aria-valuemax={1}>
              <span style={{ width: `${likenessShare(sample.sim) * 100}%` }} />
            </div>
            <div className="row mono faint" style={{ justifyContent: "space-between", marginTop: 4, fontSize: 11 }}>
              <span>0.80</span>
              <span>1.00</span>
            </div>
          </>
        ) : (
          <span className="small muted">not scored: listen</span>
        )}
      </div>
      <div className="row" style={{ gap: 12, flex: "0 1 auto" }}>
        <button
          type="button"
          className="big-play"
          aria-pressed={playing}
          aria-label={`${playing ? "Stop" : "Play"} the sample: ${title}`}
          onClick={() => player.toggle(url)}
        >
          {playing ? <StopIcon size={16} /> : <PlayIcon size={16} />}
        </button>
        <div className="pads" role="group" aria-label="Test sentences cloned from it" style={{ width: 236 }}>
          {sample.tests.map((test, i) => {
            const testUrl = fileUrl(run, test);
            const on = player.playing === testUrl;
            return (
              <button
                key={test}
                type="button"
                className="pad"
                aria-pressed={on}
                aria-label={`${on ? "Stop" : "Play"} test sentence ${i + 1}: ${lines[i] ?? ""}`}
                title={lines[i]}
                onClick={() => player.toggle(testUrl)}
              >
                {i + 1}
              </button>
            );
          })}
        </div>
      </div>
      <button type="button" className="btn" disabled={action.disabled} onClick={action.onClick}>
        {action.label}
      </button>
    </article>
  );
}

type VoicePickerProps = {
  run: string;
  busy: boolean;
  finished?: boolean;
  /** Inside review 1: the review's voice line and how to change it (it applies when approved). */
  review?: { chosen: string; choose: (n: number) => void };
  onChange?: () => void;
};

export default function VoicePicker({ run, busy, finished, review, onChange }: VoicePickerProps) {
  const [samples, setSamples] = useState<VoiceSamples | null | undefined>(undefined); // undefined: loading
  const [note, setNote] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const player = usePlayer();

  useEffect(() => {
    getVoiceSamples(run)
      .then(setSamples)
      .catch((e) => {
        setSamples(null);
        setError(message(e));
      });
  }, [run, busy]); // again when a job ends: it may have made them

  async function act(action: () => Promise<unknown>, done: string | null, pick: string | null = null) {
    setError(null);
    setWorking(true);
    try {
      await action();
      setNote(done);
      setPicked(pick);
      onChange?.();
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }

  if (samples === undefined) return <p className="empty">Loading the voices…</p>;
  if (review && !samples) return null; // an older run's candidates: the review text lists them
  const disabled = busy || working;
  const best = samples?.candidates[0]?.n;
  const isChosen = (n: number) =>
    review ? review.chosen === String(n) || (review.chosen.toLowerCase() === "auto" && n === best) : samples?.picked === n;

  return (
    <div>
      {!samples ? (
        <p className="empty">
          No voice samples yet. Making them finds the 3 cleanest stretches of this video’s speech and clones a few test
          sentences in the dub language from each, and from the channel’s voice now, to listen and compare (a minute or
          two on the GPU).
        </p>
      ) : (
        <>
          <p className="muted" style={{ margin: "0 0 16px", maxWidth: 820 }}>
            {samples.scored
              ? "The score says how much the clones sound like the speaker; differences under 0.01 are noise, so trust your ear."
              : "No scores: the speaker model isn't installed where localdub runs (pip install localdub[voice]); listen to choose."}
            {review && " Your choice goes into the review’s voice line and applies when you approve it."}
          </p>
          {samples.lines.length > 0 && (
            <section className="panel" aria-label="The test sentences" style={{ padding: "16px 22px", marginBottom: 14 }}>
              <div className="row" style={{ gap: "10px 28px", alignItems: "baseline" }}>
                <span className="kicker">The test sentences</span>
                {samples.lines.map((line, i) => (
                  <span key={line} style={{ fontSize: 14 }}>
                    <span className="mono" style={{ color: "var(--air)", marginRight: 6 }}>
                      {i + 1}
                    </span>
                    {line}
                  </span>
                ))}
              </div>
            </section>
          )}
          <div className="stack" style={{ gap: 14 }}>
            {samples.current && !review && (
              <VoiceCard
                run={run}
                kicker={samples.picked === "current" ? "In use · kept" : "In use"}
                title="The channel’s voice"
                from={samples.current.from || undefined}
                sample={samples.current}
                lines={samples.lines}
                chosen={samples.picked === "current" || samples.picked == null}
                player={player}
                action={{
                  label: samples.picked === "current" ? "Kept" : "Keep this voice",
                  disabled: disabled || samples.picked === "current",
                  onClick: () => act(() => pickVoice(run, "current"), "The channel keeps its voice."),
                }}
              />
            )}
            {samples.candidates.map((c, i) => (
              <VoiceCard
                key={c.n}
                run={run}
                kicker={`Candidate ${c.n}${i === 0 && samples.scored ? " · best match" : ""}${isChosen(c.n) ? " · chosen" : ""}`}
                title={stretch(c.start, c.end)}
                from={`${Math.round(c.end - c.start)} s of this video’s speech`}
                sample={c}
                lines={samples.lines}
                chosen={isChosen(c.n)}
                player={player}
                action={{
                  label: isChosen(c.n) ? (review ? "Chosen" : "In use now") : "Use this voice",
                  disabled: isChosen(c.n) || (!review && disabled),
                  onClick: () =>
                    review
                      ? review.choose(c.n)
                      : act(() => pickVoice(run, c.n), `Candidate ${c.n} is the channel’s voice now: new runs use it.`, String(c.n)),
                }}
              />
            ))}
          </div>
        </>
      )}
      {note && (
        <div className="notice" role="status">
          <span>{note}</span>
          {finished && picked && (
            <button type="button" className="btn primary" disabled={disabled} onClick={() => act(() => revoice(run), "Re-dubbing this run with the new voice…")}>
              Re-dub this run with it
            </button>
          )}
        </div>
      )}
      <ErrorText error={error} />
      {!review && (
        <div className="row" style={{ marginTop: 18 }}>
          <button type="button" className={samples ? "btn" : "btn primary"} disabled={disabled} onClick={() => act(() => makeVoiceSamples(run), null)}>
            {samples ? "Make new samples" : "Make voice samples"}
          </button>
          {busy && <span className="small muted">A run is on air; this waits for the GPUs.</span>}
        </div>
      )}
    </div>
  );
}
