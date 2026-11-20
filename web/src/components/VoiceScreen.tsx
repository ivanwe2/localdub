// The Voices screen: a run's voice samples beside the channel's voice now; pick one any time.

import type { RunSummary } from "../api";
import VoicePicker from "./VoicePicker";

type VoiceScreenProps = { name: string | null; runs: RunSummary[]; busy: boolean; onChange: () => void };

export default function VoiceScreen({ name, runs, busy, onChange }: VoiceScreenProps) {
  const run = runs.find((r) => r.name === name) ?? null;
  const transcribed = runs.filter((r) => r.done.includes("spelling"));
  if (!name || (run && !run.done.includes("spelling"))) {
    return (
      <>
        <div className="page-title">
          <div>
            <div className="kicker">Voices</div>
            <h1>{name ? `${name} isn’t transcribed yet` : "Voices come from a run’s speech"}</h1>
            <p>Once a run is transcribed, its cleanest stretches of speech can be compared with the channel’s voice.</p>
          </div>
        </div>
        {transcribed.length > 0 && (
          <section className="panel" style={{ marginTop: 24 }}>
            <h2>Transcribed runs</h2>
            {transcribed.map((r) => (
              <div key={r.name} className="run-row">
                <strong style={{ flex: "1 1 auto" }}>{r.name}</strong>
                <span className="small muted">{r.channel}</span>
                <a className="btn small" href={`#/voices/${encodeURIComponent(r.name)}`}>
                  Voices
                </a>
              </div>
            ))}
          </section>
        )}
      </>
    );
  }
  return (
    <>
      <div className="page-title">
        <div>
          <div className={`kicker${run?.waiting_for?.kind === "voice" ? " amber" : ""}`}>
            {run ? `${run.channel} · the voice its new runs use` : "Voices"}
          </div>
          <h1>Voices from {name}</h1>
          <p>
            Three clean stretches of this video’s speech, each with four test sentences cloned from it, beside the voice in
            use.
          </p>
        </div>
      </div>
      <div style={{ marginTop: 24 }}>
        <VoicePicker key={name} run={name} busy={busy} finished={run?.finished} onChange={onChange} />
      </div>
    </>
  );
}
