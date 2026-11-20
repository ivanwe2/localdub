// The page: the studio (the run on air, the AI, the runs) and its screens, picked by the address's
// # (see hooks.ts Route), over the sound-wave background.

import { useEffect, useState } from "react";

import { AiConsole, Machines } from "./components/Console";
import Header from "./components/Header";
import NewRun from "./components/NewRun";
import OnAir from "./components/OnAir";
import ReviewScreen from "./components/ReviewScreen";
import RunList from "./components/RunList";
import ShortsScreen from "./components/ShortsScreen";
import VoiceScreen from "./components/VoiceScreen";
import WaveField, { type WaveMode } from "./components/WaveField";
import { useChannels, useJob, useRoute, useRuns, useStatus, useTheme } from "./hooks";

export default function App() {
  const job = useJob();
  const { runs, error, refresh } = useRuns(job);
  const { channels, refresh: refreshChannels } = useChannels(job);
  const status = useStatus();
  const route = useRoute();
  const [theme, toggleTheme] = useTheme();
  const [focus, setFocus] = useState<string | null>(null);
  const busy = job?.state === "running";

  // a run that starts takes the timeline
  useEffect(() => {
    if (job?.state === "running") setFocus(null);
  }, [job?.run, job?.state]);

  const waiting = runs.some((r) => r.waiting_for);
  const mode: WaveMode = busy ? "speaking" : job?.state === "waiting" || waiting ? "calm" : "still";
  const shown = focus ?? job?.run ?? runs[0]?.name ?? null;
  const onChange = () => {
    refresh();
    refreshChannels();
  };

  return (
    <>
      <WaveField mode={mode} />
      <div className="shell">
        <Header job={job} status={status} route={route} runs={runs} theme={theme} onTheme={toggleTheme} />
        <main>
          {route.page === "studio" && (
            <>
              {shown ? (
                <OnAir
                  name={shown}
                  run={runs.find((r) => r.name === shown) ?? null}
                  job={job?.run === shown ? job : null}
                  channels={channels}
                  busy={busy}
                  onChange={onChange}
                  backToAir={busy && job && shown !== job.run ? () => setFocus(null) : undefined}
                />
              ) : (
                <section className="panel" style={{ marginTop: 36 }}>
                  <div className="kicker">Nothing on air</div>
                  <h1 style={{ margin: "6px 0 8px", fontSize: 36 }}>Ready for a video</h1>
                  <p className="muted">
                    Upload one or download it from a link, choose the channel, and start: the run shows up here as a
                    timeline while it works.
                  </p>
                  <a className="btn primary" href="#/new" style={{ marginTop: 12 }}>
                    New run
                  </a>
                </section>
              )}
              <div className="columns">
                <div className="main">
                  <AiConsole calls={job?.calls ?? []} live={busy} />
                </div>
                <div className="side">
                  <Machines status={status} />
                </div>
              </div>
              <RunList
                runs={runs}
                error={error}
                job={job}
                channels={channels}
                focus={shown}
                onFocus={setFocus}
                busy={busy}
                onChange={onChange}
              />
            </>
          )}
          {route.page === "new" && <NewRun busy={busy} channels={channels} onStarted={onChange} />}
          {route.page === "review" && <ReviewScreen name={route.run} runs={runs} channels={channels} busy={busy} onChange={onChange} />}
          {route.page === "voices" && <VoiceScreen name={route.run} runs={runs} busy={busy} onChange={onChange} />}
          {route.page === "shorts" && <ShortsScreen name={route.run} runs={runs} channels={channels} busy={busy} onChange={onChange} />}
        </main>
      </div>
    </>
  );
}
