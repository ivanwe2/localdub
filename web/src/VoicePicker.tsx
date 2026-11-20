// The voice picker: voice samples from a run's speech and the channel's voice now, each with test
// sentences cloned from it, to listen to and compare; the one picked becomes the channel's voice.

import { type ReactNode, useEffect, useRef, useState } from "react";

import {
  fileUrl,
  getVoiceSamples,
  makeVoiceSamples,
  pickVoice,
  revoice,
  type VoiceSample,
  type VoiceSamples,
} from "./api";
import { likenessShare, stretch } from "./format";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

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

function Play({ url, label, title, player }: { url: string; label: string; title: string; player: Player }) {
  const on = player.playing === url;
  return (
    <button
      type="button"
      className={on ? "play on" : "play"}
      aria-pressed={on}
      aria-label={`${on ? "Stop" : "Play"} ${title}`}
      title={title}
      onClick={() => player.toggle(url)}
    >
      {on ? "■" : "▶"} {label}
    </button>
  );
}

type SampleRowProps = {
  run: string;
  title: string;
  sample: VoiceSample;
  lines: string[];
  player: Player;
  children: ReactNode;
};

/** A voice sample: its likeness score, the sample itself and the test sentences cloned from it. */
function SampleRow({ run, title, sample, lines, player, children }: SampleRowProps) {
  return (
    <li>
      <div className="row">
        <strong>{title}</strong>
        {sample.sim != null && (
          <span className="likeness" title="how much the test clones sound like the speaker's real voice (1.00 = the same)">
            <span className="meter">
              <span style={{ width: `${likenessShare(sample.sim) * 100}%` }} />
            </span>
            sounds like the speaker {sample.sim.toFixed(2)}
          </span>
        )}
      </div>
      <div className="links">
        <Play url={fileUrl(run, sample.path)} label="Sample" title={`the sample (${title})`} player={player} />
        {sample.tests.map((test, i) => (
          <Play
            key={test}
            url={fileUrl(run, test)}
            label={String(i + 1)}
            title={`test sentence ${i + 1}: ${lines[i] ?? ""}`}
            player={player}
          />
        ))}
        {children}
      </div>
    </li>
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

  async function act(action: () => Promise<unknown>, done: string | null) {
    setError(null);
    setWorking(true);
    try {
      await action();
      setNote(done);
      onChange?.();
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }

  if (samples === undefined) return <p className="muted">Loading the voices…</p>;
  if (review && !samples) return null; // an older run's candidates: the review text lists them
  const disabled = busy || working;
  const best = samples?.candidates[0]?.n;
  const isChosen = (n: number) =>
    review
      ? review.chosen === String(n) || (review.chosen.toLowerCase() === "auto" && n === best)
      : samples?.picked === n;

  return (
    <div className="review voice">
      {!samples ? (
        <p className="muted">
          No voice samples yet. Making them finds the 3 cleanest stretches of this video's speech and clones a few test
          sentences in the dub language from each, and from the channel's voice now, to listen and compare (a minute or
          two on the GPU).
        </p>
      ) : (
        <>
          <p className="muted">
            Listen to each sample and its test sentences.{" "}
            {samples.scored
              ? "The score says how much the clones sound like the speaker; differences under 0.01 are noise, so trust your ear."
              : "No scores: the speaker model isn't installed where localdub runs (pip install localdub[voice]); listen to choose."}
            {review && " Your choice goes into the review's voice line and applies when you approve it."}
          </p>
          <ul className="candidates">
            {samples.current && !review && (
              <SampleRow
                run={run}
                title={`In use: ${samples.current.from || "the channel's voice"}`}
                sample={samples.current}
                lines={samples.lines}
                player={player}
              >
                <button
                  type="button"
                  className="link"
                  disabled={disabled || samples.picked === "current"}
                  onClick={() => act(() => pickVoice(run, "current"), "The channel keeps its voice.")}
                >
                  Keep this voice
                </button>
              </SampleRow>
            )}
            {samples.candidates.map((c) => (
              <SampleRow
                key={c.n}
                run={run}
                title={`Candidate ${c.n} · ${stretch(c.start, c.end)}${isChosen(c.n) ? " · chosen" : ""}`}
                sample={c}
                lines={samples.lines}
                player={player}
              >
                <button
                  type="button"
                  className="link"
                  disabled={isChosen(c.n) || (!review && disabled)}
                  onClick={() =>
                    review
                      ? review.choose(c.n)
                      : act(() => pickVoice(run, c.n), `Candidate ${c.n} is the channel's voice now: new runs use it.`)
                  }
                >
                  Use this voice
                </button>
              </SampleRow>
            ))}
          </ul>
        </>
      )}
      {note && (
        <p>
          {note}{" "}
          {finished && note.startsWith("Candidate") && (
            <button
              type="button"
              className="link"
              disabled={disabled}
              onClick={() => act(() => revoice(run), "Re-dubbing this run with the new voice…")}
            >
              Re-dub this run with it
            </button>
          )}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {!review && (
        <button
          type="button"
          className={samples ? "secondary" : undefined}
          disabled={disabled}
          onClick={() => act(() => makeVoiceSamples(run), null)}
        >
          {samples ? "Make the samples again" : "Make voice samples"}
        </button>
      )}
    </div>
  );
}
