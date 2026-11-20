// The reviews: review 1 (the transcript: names, spelling, the voice) and review 2 (the translation,
// with each line's character budget and the channel's glossary). The file stays the source of truth:
// every edit here changes its text line for line, and "Edit as text" shows it whole.

import { useEffect, useMemo, useRef, useState } from "react";

import { type Channel, continueRun, fileUrl, getReview, getSegments, type Review, type RunSummary, saveReview, type Segment } from "../api";
import { approve, setVoiceLine } from "../format";
import { go } from "../hooks";
import {
  type Block,
  marks,
  parseReview,
  replaceWord,
  type ReviewLine,
  setGlossary,
  setReviewLine,
  splitMarks,
} from "../studio";
import { CheckIcon, ErrorText, message, PlayIcon, StopIcon } from "./common";
import VoicePicker from "./VoicePicker";

/** Plays stretches of one long audio file (the speaker's voice), one at a time. */
function useStretches(url: string) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const stopAt = useRef(0);
  const [playing, setPlaying] = useState<number | null>(null);
  useEffect(() => () => audio.current?.pause(), [url]);

  function play(id: number, start: number, end: number) {
    if (!audio.current) {
      const a = new Audio(url);
      a.preload = "metadata";
      a.ontimeupdate = () => {
        if (a.currentTime >= stopAt.current) {
          a.pause();
          setPlaying(null);
        }
      };
      a.onerror = () => setPlaying(null);
      audio.current = a;
    }
    const a = audio.current;
    if (playing === id) {
      a.pause();
      setPlaying(null);
      return;
    }
    stopAt.current = end;
    const begin = () => {
      a.currentTime = start;
      a.play().catch(() => setPlaying(null));
    };
    if (a.readyState >= 1) begin();
    else a.addEventListener("loadedmetadata", begin, { once: true });
    setPlaying(id);
  }
  return { playing, play };
}

/** "01:14.6" → 74.6 */
function seconds(at: string): number {
  const m = at.match(/^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$/);
  if (!m) return 0;
  return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]);
}

type ReviewScreenProps = {
  name: string | null;
  runs: RunSummary[];
  channels: Channel[];
  busy: boolean;
  onChange: () => void;
};

export default function ReviewScreen({ name, runs, channels, busy, onChange }: ReviewScreenProps) {
  const run = runs.find((r) => r.name === name) ?? null;
  const kind = run?.waiting_for?.kind;
  if (!name || (run && kind !== "review1" && kind !== "review2")) {
    const waiting = runs.filter((r) => r.waiting_for?.kind === "review1" || r.waiting_for?.kind === "review2");
    return (
      <>
        <div className="page-title">
          <div>
            <div className="kicker">Reviews</div>
            <h1>{name && run ? `${name} waits for no review` : "Nothing waits for a review"}</h1>
            <p>Runs started with reviews stop twice: once for the transcript, once for the translation.</p>
          </div>
        </div>
        {waiting.length > 0 && (
          <section className="panel" style={{ marginTop: 24 }}>
            <h2>Waiting for you</h2>
            {waiting.map((r) => (
              <div key={r.name} className="run-row">
                <strong style={{ flex: "1 1 auto" }}>{r.name}</strong>
                <a className="btn go small" href={`#/review/${encodeURIComponent(r.name)}`}>
                  Open review
                </a>
              </div>
            ))}
          </section>
        )}
      </>
    );
  }
  return <ReviewEditor key={name} name={name} run={run} channels={channels} busy={busy} onChange={onChange} />;
}

type EditorProps = { name: string; run: RunSummary | null; channels: Channel[]; busy: boolean; onChange: () => void };

function ReviewEditor({ name, run, channels, busy, onChange }: EditorProps) {
  const [review, setReview] = useState<Review | null>(null);
  const [original, setOriginal] = useState("");
  const [text, setText] = useState("");
  const [asText, setAsText] = useState(false);
  const [saved, setSaved] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [looked, setLooked] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<number | null>(null);
  const [overOnly, setOverOnly] = useState(false);
  const [segments, setSegments] = useState<Segment[]>([]);
  const player = useStretches(fileUrl(name, "vocals.wav"));

  useEffect(() => {
    getReview(name)
      .then((r) => {
        setReview(r);
        setText(r.text);
        setOriginal(r.text);
      })
      .catch((e) => setError(message(e)));
    getSegments(name)
      .then(setSegments)
      .catch(() => setSegments([]));
  }, [name]);

  const parsed = useMemo(() => parseReview(text), [text]);
  const first = useMemo(() => parseReview(original), [original]);
  const channel = channels.find((c) => c.name === run?.channel);

  function edit(next: string, block?: number) {
    setText(next);
    setSaved(false);
    if (block != null) setLooked((s) => new Set(s).add(block));
  }

  async function save(andContinue: boolean) {
    setError(null);
    setWorking(true);
    try {
      const final = andContinue ? approve(text) : text;
      await saveReview(name, final);
      setText(final);
      setSaved(true);
      if (andContinue) {
        await continueRun(name);
        onChange();
        go({ page: "studio" });
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }

  if (!review) return error ? <ErrorText error={error} /> : <p className="empty">Loading the review…</p>;
  const transcript = review.kind === "review1";
  const byId = new Map(segments.map((s) => [s.id, s]));
  const over = parsed.blocks.filter((b) => isOver(b)).length;
  const blocks = overOnly ? parsed.blocks.filter((b) => isOver(b)) : parsed.blocks;
  const learns = transcript ? learned(first.blocks, parsed.blocks) : [];

  return (
    <>
      <div className="page-title">
        <div>
          <div className="kicker amber">{transcript ? "Review 1 of 2 · the transcript" : "Review 2 of 2 · the translation"}</div>
          <h1>{name}</h1>
          <p>
            {transcript
              ? `What the speaker said${channel ? `, in ${channel.source_name}` : ""}. Fix names and words the model heard wrong; the dub is made from this. Only doubtful lines are listed.`
              : `Every line and its ${channel?.target_name ?? "dub"} translation. Fix what sounds wrong; a line over its budget gets sped up a little, so shorter is better.`}
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {!transcript && over > 0 && (
            <div className="row" role="group" aria-label="Show" style={{ gap: 6 }}>
              <button type="button" className="chip" aria-pressed={overOnly} onClick={() => setOverOnly(true)}>
                Over budget {over}
              </button>
              <button type="button" className="chip" aria-pressed={!overOnly} onClick={() => setOverOnly(false)}>
                All {parsed.blocks.length}
              </button>
            </div>
          )}
          <button type="button" className="btn small" aria-pressed={asText} onClick={() => setAsText(!asText)}>
            {asText ? "Show as lines" : "Edit as text"}
          </button>
        </div>
      </div>

      <div className="columns">
        <div className="main stack">
          {asText ? (
            <section className="panel">
              <label className="kicker" htmlFor="raw">
                {review.file}
              </label>
              <textarea id="raw" className="raw" style={{ marginTop: 10 }} value={text} onChange={(e) => edit(e.target.value)} spellCheck={false} />
            </section>
          ) : (
            <section className="panel" aria-label="Lines" style={{ padding: "10px 14px" }}>
              {blocks.length === 0 && <p className="empty">Nothing to check: every word is known and nothing was changed.</p>}
              {blocks.map((b) => {
                const seg = byId.get(b.id);
                const start = seg?.start ?? seconds(b.at);
                const end = seg?.end ?? start + 8;
                return (
                  <div key={b.id} className={`block${open === b.id ? " open" : ""}`}>
                    <div className="when">
                      <span>#{b.id}</span>
                      <span>{b.at.replace(/\.\d$/, "")}</span>
                      <button
                        type="button"
                        className="play"
                        aria-pressed={player.playing === b.id}
                        aria-label={`${player.playing === b.id ? "Stop" : "Hear"} line ${b.id} in the original`}
                        onClick={() => player.play(b.id, start, end)}
                      >
                        {player.playing === b.id ? <StopIcon /> : <PlayIcon />}
                      </button>
                    </div>
                    <div className="body">
                      {transcript ? (
                        <TranscriptBlock
                          block={b}
                          editing={open === b.id}
                          onOpen={() => setOpen(b.id)}
                          looked={looked.has(b.id)}
                          change={(line, value) => edit(setReviewLine(text, line, value), b.id)}
                          seen={() => setLooked((s) => new Set(s).add(b.id))}
                        />
                      ) : (
                        <TranslationBlock block={b} srcLabel={channel?.source.toUpperCase()} change={(line, value) => edit(setReviewLine(text, line, value), b.id)} />
                      )}
                    </div>
                  </div>
                );
              })}
            </section>
          )}
          {transcript && parsed.voice !== null && (
            <section className="panel" aria-labelledby="voice">
              <div className="panel-head" style={{ marginBottom: 14 }}>
                <h2 id="voice">The voice</h2>
                <span className="kicker">voice: {parsed.voice || "–"}</span>
              </div>
              <VoicePicker run={name} busy={busy} review={{ chosen: parsed.voice, choose: (n) => edit(setVoiceLine(text, String(n))) }} />
            </section>
          )}
        </div>

        <aside className="side stack">
          {transcript && (
            <section className="panel" aria-labelledby="looked">
              <h2 id="looked">Looked at</h2>
              <div className="mono" style={{ marginTop: 12, fontSize: 28 }}>
                {looked.size}
                <span className="faint"> / {parsed.blocks.length}</span>
              </div>
              <div className="bar amber" style={{ marginTop: 10 }}>
                <span style={{ width: `${parsed.blocks.length ? (looked.size / parsed.blocks.length) * 100 : 0}%` }} />
              </div>
              <p className="small muted" style={{ margin: "12px 0 0" }}>
                Flagged: words the spelling dictionary doesn’t know, names to check, and changes the model made.
              </p>
            </section>
          )}
          {transcript && (
            <section className="panel" aria-labelledby="learns">
              <h2 id="learns">The channel learns</h2>
              {learns.length === 0 ? (
                <p className="empty small">What you fix or keep here, applied to its next videos without asking again.</p>
              ) : (
                <div style={{ marginTop: 12 }}>
                  {learns.map((l) => (
                    <div key={l.text} className="learn">
                      <span>{l.text}</span>
                      <span className="kicker">{l.kind}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}
          {!transcript && parsed.glossary && (
            <Glossary terms={parsed.glossary} labels={[channel?.source.toUpperCase() ?? "SRC", channel?.target.toUpperCase() ?? "DUB"]} change={(terms) => edit(setGlossary(text, terms))} />
          )}
          <ErrorText error={error} />
          <div className="stack" style={{ gap: 10 }}>
            <button type="button" className="btn go wide" style={{ minHeight: 44 }} disabled={working || busy} onClick={() => save(true)}>
              {transcript ? "Approve and translate" : "Approve and voice it"}
            </button>
            <button type="button" className="btn wide" disabled={working || saved} onClick={() => save(false)}>
              {saved ? "Saved" : "Save, approve later"}
            </button>
            {busy && <span className="small muted">A run is on air; approving waits until it’s done.</span>}
          </div>
        </aside>
      </div>
    </>
  );
}

function isOver(b: Block): boolean {
  const tgt = b.lines[1];
  const budget = b.notes.find((n) => n.kind === "budget");
  return Boolean(tgt && budget && budget.kind === "budget" && tgt.text.length > budget.budget);
}

/** What approving teaches the channel, from the notes and what the lines say now. */
function learned(before: Block[], now: Block[]): { text: string; kind: string }[] {
  const out: { text: string; kind: string }[] = [];
  now.forEach((b) => {
    const was = before.find((x) => x.id === b.id);
    const line = b.lines[0]?.text ?? "";
    for (const n of b.notes) {
      if (n.kind === "name") n.pairs.forEach(([a, c]) => line.includes(c) && !line.includes(a) && out.push({ text: `${a} → ${c}`, kind: "name" }));
      if (n.kind === "fixed") n.pairs.forEach(([a, c]) => line.includes(c) && out.push({ text: `${a} → ${c}`, kind: "spelling" }));
      if (n.kind === "unknown") n.words.forEach((w) => line.includes(w) && out.push({ text: w, kind: "a real word" }));
    }
    if (was && was.lines[0] && was.lines[0].text !== line) out.push({ text: `line ${b.id}`, kind: "edited" });
  });
  return out.filter((x, i) => out.findIndex((y) => y.text === x.text) === i).slice(0, 12);
}

type TranscriptBlockProps = {
  block: Block;
  editing: boolean;
  onOpen: () => void;
  looked: boolean;
  change: (line: ReviewLine, value: string) => void;
  seen: () => void;
};

function TranscriptBlock({ block, editing, onOpen, looked, change, seen }: TranscriptBlockProps) {
  const line = block.lines[0];
  if (!line) return null;
  const words = marks(block);
  return (
    <>
      {editing ? (
        <label className="kicker" style={{ display: "block" }}>
          Line {block.id}, as it will be translated
          <textarea
            value={line.text}
            rows={2}
            autoFocus
            onChange={(e) => change(line, e.target.value)}
            style={{ marginTop: 6, fontSize: 17, borderColor: "var(--amber)" }}
          />
        </label>
      ) : (
        <button type="button" className="sentence" onClick={onOpen} aria-label={`Edit line ${block.id}: ${line.text}`}>
          {splitMarks(line.text, words).map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
        </button>
      )}
      {block.notes.map((n, i) => {
        if (n.kind === "name")
          return n.pairs.map(([a, b]) =>
            line.text.includes(a) ? (
              <div key={`${i}${a}`} className="fixes">
                <span>
                  A name? The dictionary doesn’t know <strong>{a}</strong>.
                </span>
                <button type="button" className="btn small use" onClick={() => change(line, replaceWord(line.text, a, b))}>
                  It is {b}
                </button>
                <button type="button" className="btn small" onClick={seen}>
                  Keep {a}
                </button>
              </div>
            ) : (
              <div key={`${i}${a}`} className="fixes">
                <CheckIcon /> {a} → {b}, from now on
              </div>
            ),
          );
        if (n.kind === "fixed")
          return n.pairs.map(([a, b]) => (
            <div key={`${i}${a}`} className="fixes">
              <span>
                The model changed <strong>{a}</strong> to <strong>{b}</strong>.
              </span>
              {line.text.includes(b) ? (
                <button type="button" className="btn small" onClick={() => change(line, replaceWord(line.text, b, a))}>
                  Undo it
                </button>
              ) : (
                <span className="faint">undone</span>
              )}
            </div>
          ));
        if (n.kind === "learned")
          return (
            <div key={i} className="fixes faint">
              Already learned: {n.pairs.map(([a, b]) => `${a} → ${b}`).join(", ")}
            </div>
          );
        if (n.kind === "unknown")
          return (
            <div key={i} className="fixes">
              <span>
                Not in the dictionary: {n.words.join(", ")}. Left as they are, the channel learns them as real words.
              </span>
              {!looked && (
                <button type="button" className="btn small" onClick={seen}>
                  They’re right
                </button>
              )}
            </div>
          );
        if (n.kind === "note")
          return (
            <div key={i} className="fixes faint">
              {n.text}
            </div>
          );
        return null;
      })}
    </>
  );
}

function TranslationBlock({ block, srcLabel, change }: { block: Block; srcLabel?: string; change: (line: ReviewLine, value: string) => void }) {
  const [src, tgt] = block.lines;
  const budget = block.notes.find((n) => n.kind === "budget");
  const notes = block.notes.filter((n) => n.kind === "note");
  if (!src || !tgt) return null;
  const limit = budget && budget.kind === "budget" ? budget.budget : null;
  const over = limit != null && tgt.text.length > limit;
  return (
    <>
      <div className="sentence src">
        <span className="label">{srcLabel ?? src.label}</span>
        {src.text}
      </div>
      <label style={{ display: "block", marginTop: 8 }}>
        <span className="visually-hidden">Line {block.id} in {tgt.label}</span>
        <textarea
          value={tgt.text}
          rows={2}
          onChange={(e) => change(tgt, e.target.value)}
          style={{ fontSize: 16, borderColor: over ? "var(--fail)" : undefined }}
          aria-describedby={`budget-${block.id}`}
        />
      </label>
      <div className="row" style={{ gap: 12, marginTop: 6 }}>
        {limit != null && (
          <span id={`budget-${block.id}`} className={`budget${over ? " over" : ""}`}>
            {tgt.text.length}/{limit} characters{over ? " · over: it will be sped up" : ""}
            {budget && budget.kind === "budget" && budget.slot != null ? ` · ${budget.slot.toFixed(1)} s to say it` : ""}
          </span>
        )}
        {notes.map((n, i) => (
          <span key={i} className="small faint">
            {n.kind === "note" ? n.text : ""}
          </span>
        ))}
      </div>
    </>
  );
}

function Glossary({ terms, labels, change }: { terms: [string, string][]; labels: [string, string]; change: (terms: [string, string][]) => void }) {
  const [rows, setRows] = useState<[string, string][]>(terms.length ? terms : []);
  const update = (next: [string, string][]) => {
    setRows(next);
    change(next);
  };
  return (
    <section className="panel" aria-labelledby="glossary">
      <h2 id="glossary">Glossary</h2>
      <p className="small muted" style={{ margin: "8px 0 0" }}>
        Terms the translator always uses for this channel, from now on.
      </p>
      {rows.map(([a, b], i) => (
        <div key={i} className="term">
          <input aria-label={`${labels[0]} term ${i + 1}`} value={a} onChange={(e) => update(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <span className="faint">=</span>
          <input aria-label={`${labels[1]} term ${i + 1}`} value={b} onChange={(e) => update(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <button type="button" className="icon-btn" aria-label={`Remove term ${i + 1}`} onClick={() => update(rows.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="btn small" style={{ marginTop: 12 }} onClick={() => setRows([...rows, ["", ""]])}>
        Add a term
      </button>
    </section>
  );
}
