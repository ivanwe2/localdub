// The AI console (how fast each model works, the latest requests) and the machines (GPUs, the
// models loaded, memory), live.

import type { Call, Status } from "../api";
import { formatBytes } from "../format";
import { callSpeed, latestSpeed, ledClasses, ledsLit } from "../studio";

function Leds({ lit, label, value, max, thin }: { lit: number; label: string; value: number; max: number; thin?: boolean }) {
  return (
    <div
      className={`leds${thin ? " thin" : ""}`}
      role="meter"
      aria-label={label}
      aria-valuenow={Math.round(value * 10) / 10}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      {ledClasses(lit).map((cls, i) => (
        <span key={i} className={cls} />
      ))}
    </div>
  );
}

const KIND: Record<string, string> = { chat: "text", tts: "voice", asr: "hearing", separation: "separation" };

/** Positions on the text meter's log scale (1 to 300 tokens per second). */
const SCALE = [1, 10, 50, 100, 200].map((v) => ({ v, left: `${((Math.log(v) / Math.log(300)) * 100).toFixed(1)}%` }));

export function AiConsole({ calls, live }: { calls: Call[]; live: boolean }) {
  const text = latestSpeed(calls, "chat");
  const voice = latestSpeed(calls, "tts");
  const hearing = latestSpeed(calls, "asr");
  const latest = [...calls].reverse().slice(0, 8);
  return (
    <section className="panel" aria-labelledby="ai">
      <div className="panel-head">
        <h2 id="ai">AI console</h2>
        <span className="kicker">{calls.length ? "this run’s latest requests" : "no requests yet"}</span>
      </div>
      <div className="meter-big">
        <div style={{ flex: "0 0 auto" }}>
          <div className="kicker">Text · {text?.model ?? "–"}</div>
          <div className="row" style={{ gap: 8, marginTop: 8, alignItems: "baseline" }}>
            <span className="big-number">{text ? text.value.toFixed(0) : "–"}</span>
            <span className="mono muted">tok/s</span>
          </div>
        </div>
        <div style={{ flex: "1 1 300px", minWidth: 0 }}>
          <Leds lit={live ? ledsLit(text?.value ?? null, 300, 24, true) : 0} label="Text model speed, tokens per second" value={text?.value ?? 0} max={300} />
          <div className="scale" aria-hidden="true">
            {SCALE.map((s) => (
              <span key={s.v} style={{ left: s.left }}>
                {s.v}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="meter-row">
        <div className="kicker">Voice · {voice?.model ?? "–"}</div>
        <Leds thin lit={live ? ledsLit(voice?.value ?? null, 6) : 0} label="Voice model speed, times real time" value={voice?.value ?? 0} max={6} />
        <span className="mono small muted">{voice ? `${voice.value.toFixed(1)}× real time` : "–"}</span>
      </div>
      <div className="meter-row">
        <div className="kicker">Hearing · {hearing?.model ?? "–"}</div>
        <Leds thin lit={live ? ledsLit(hearing?.value ?? null, 60) : 0} label="Transcription speed, times real time" value={hearing?.value ?? 0} max={60} />
        <span className="mono small muted">{hearing ? `${hearing.value.toFixed(0)}× real time` : "–"}</span>
      </div>
      {latest.length > 0 && (
        <div className="table-box">
          <table className="calls">
            <thead>
              <tr>
                <th scope="col">Kind</th>
                <th scope="col">What</th>
                <th scope="col">Model</th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Time
                </th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Speed
                </th>
              </tr>
            </thead>
            <tbody>
              {latest.map((c, i) => (
                <tr key={calls.length - i}>
                  <td>{KIND[c.kind] ?? c.kind}</td>
                  <td>{c.label}</td>
                  <td className="muted">{c.model}</td>
                  <td className="num">{c.seconds.toFixed(1)} s</td>
                  <td className="num">{callSpeed(c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Machines({ status }: { status: Status | null }) {
  return (
    <section className="panel" aria-labelledby="machines">
      <div className="panel-head">
        <h2 id="machines">Machines</h2>
        <span className="kicker">live</span>
      </div>
      {status?.error && <p className="error small">{status.error}</p>}
      <div className="stack" style={{ gap: 14, marginTop: 20 }}>
        {status?.gpus.map((g) => {
          const memory = g.memory_total_bytes ? g.memory_used_bytes / g.memory_total_bytes : 0;
          return (
            <div key={g.id} className="gpu">
              <div className="row" style={{ justifyContent: "space-between", gap: 12 }}>
                <strong>GPU {g.id}</strong>
                <span className="mono small muted">
                  {g.util_percent.toFixed(0)}% · {g.temperature_c.toFixed(0)} °C · {g.power_w.toFixed(0)} W
                </span>
              </div>
              <div className="small muted">{g.name}</div>
              <div className="bar air" title={`compute ${g.util_percent.toFixed(0)}%`}>
                <span style={{ width: `${Math.min(100, g.util_percent)}%` }} />
              </div>
              <div className="bar" title="memory">
                <span style={{ width: `${memory * 100}%` }} />
              </div>
              <div className="mono small muted" style={{ marginTop: 8 }}>
                {formatBytes(g.memory_used_bytes)} of {formatBytes(g.memory_total_bytes)}
              </div>
            </div>
          );
        })}
        <div>
          <div className="kicker">Models loaded</div>
          {status && status.models.length === 0 && <p className="empty small">None: the GPUs are free.</p>}
          {status?.models.map((m) => (
            <div key={m.model} className="model">
              <span>
                {m.name || m.model}
                {m.description && <span className="faint small"> · {m.description}</span>}
              </span>
              <span className={`state${m.state === "ready" ? " ready" : ""}`}>{m.state}</span>
            </div>
          ))}
        </div>
        {status?.memory && (
          <div>
            <div className="row small muted" style={{ justifyContent: "space-between" }}>
              <span>System memory</span>
              <span className="mono">
                {formatBytes(status.memory.used_bytes)} of {formatBytes(status.memory.total_bytes)}
              </span>
            </div>
            <div className="bar">
              <span style={{ width: `${(status.memory.used_bytes / (status.memory.total_bytes || 1)) * 100}%` }} />
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
