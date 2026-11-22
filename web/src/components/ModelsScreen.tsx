// Models: which model does each task (text, vision, voice, hearing, separation), on which server,
// what each task needs, how hard the text model thinks, and a way to try each one. The engine owns
// the settings (`localdub models`); this screen shows and changes them.

import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  applyPreset,
  getModels,
  type ModelServerInfo,
  type ModelsOverview,
  type ModelTest,
  type Preset,
  removeModelServer,
  saveModels,
  saveModelServer,
  type TaskInfo,
  testModel,
} from "../api";
import { priceText, usd } from "../format";
import { ErrorText, message } from "./common";

/** What the text model thinks about, each with its own level, in the order they happen. */
export const USES: [string, string][] = [
  ["spelling", "Spelling"],
  ["translate", "Translation"],
  ["shorten", "Shortening"],
  ["shorts", "Choosing Shorts"],
];

const LEVEL_NAMES: Record<string, string> = { default: "server's default", none: "off", low: "low", medium: "medium", high: "high" };

/** A server's state in a few words: "8 models", "answers", "not reachable". */
export function serverState(server: ModelServerInfo): string {
  if (!server.reachable) return "not reachable";
  return server.models.length ? `${server.models.length} model${server.models.length === 1 ? "" : "s"}` : "answers";
}

/** Where a server's key comes from, in words. */
export function keyText(server: ModelServerInfo): string {
  if (!server.key_set) return "no key";
  return server.key_source === "stored" ? "key saved" : `key from $${server.key_source}`;
}

export default function ModelsScreen({ busy }: { busy: boolean }) {
  const [data, setData] = useState<ModelsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    getModels()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(message(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <div className="page-title">
        <div>
          <div className="kicker">Models</div>
          <h1>Which model does what</h1>
          <p>
            Five tasks, each done by one model on one server: your llama-swap, or any OpenAI-compatible server, local or in
            the cloud. Pick one per task, try it, and set how hard the text model thinks.
          </p>
        </div>
        <button type="button" className="btn" onClick={load} disabled={loading}>
          {loading ? "Asking the servers…" : "Ask the servers again"}
        </button>
      </div>
      <ErrorText error={error} />
      {!data && loading && <p className="empty">Asking every server what it offers…</p>}
      {data && (
        <div className="columns">
          <div className="main stack" style={{ gap: 24 }}>
            {data.tasks.map((t) => (
              <TaskCard key={t.task} info={t} data={data} busy={busy} onSaved={load} />
            ))}
          </div>
          <aside className="side stack" style={{ gap: 24 }}>
            {(data.presets ?? []).some((p) => p.url) && <ReadyMade data={data} onChange={load} />}
            <Servers data={data} onChange={load} />
          </aside>
        </div>
      )}
    </>
  );
}

function TaskCard({ info, data, busy, onSaved }: { info: TaskInfo; data: ModelsOverview; busy: boolean; onSaved: () => void }) {
  const [server, setServer] = useState(info.server);
  const [model, setModel] = useState(info.model);
  const [saving, setSaving] = useState(false);
  const [trying, setTrying] = useState(false);
  const [result, setResult] = useState<ModelTest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setServer(info.server);
    setModel(info.model);
  }, [info.server, info.model]);

  const srv = data.servers.find((s) => s.name === server);
  const listed = srv?.models ?? [];
  const options = model && !listed.some((m) => m.id === model) ? [{ id: model, name: "not listed by this server", description: null }, ...listed] : listed;
  const chosen = listed.find((m) => m.id === model);
  const changed = server !== info.server || model !== info.model;
  // a ready-made setup's suggestions for this task, first in the list when its server is picked
  const suggested = (data.presets ?? []).find((p) => p.server === server && p.url)?.suggested[info.task]?.filter((x) => x.listed) ?? [];
  const others = options.filter((m) => !suggested.some((x) => x.id === m.id));
  const label = (m: { id: string; name: string | null }) => (m.name ? `${m.id} · ${m.name}` : m.id);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      await saveModels({ tasks: { [info.task]: { server, model: model.trim() } } });
      setResult(null);
      onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setSaving(false);
    }
  }

  async function tryIt() {
    setError(null);
    setResult(null);
    setTrying(true);
    try {
      setResult(await testModel(info.task));
    } catch (e) {
      setError(message(e));
    } finally {
      setTrying(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(info.snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // no clipboard (an insecure page, or blocked): the text is there to select
    }
  }

  return (
    <section className="panel model-card" aria-labelledby={`task-${info.task}`}>
      <div className="panel-head">
        <h2 id={`task-${info.task}`}>{info.title}</h2>
        <span className="kicker">{info.used_by.join(" · ")}</span>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        {info.what}
      </p>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <label className="field" style={{ flex: "0 1 220px" }}>
          Server
          <select value={server} onChange={(e) => setServer(e.target.value)}>
            {data.servers.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} · {s.kind}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Model
          {listed.length > 0 ? (
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {suggested.length > 0 ? (
                <>
                  <optgroup label="Suggested">
                    {suggested.map((x) => (
                      <option key={x.id} value={x.id}>
                        {x.id} · {x.why}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Every model it offers">
                    {others.map((m) => (
                      <option key={m.id} value={m.id}>
                        {label(m)}
                      </option>
                    ))}
                  </optgroup>
                </>
              ) : (
                options.map((m) => (
                  <option key={m.id} value={m.id}>
                    {label(m)}
                  </option>
                ))
              )}
            </select>
          ) : (
            <input className="mono" value={model} onChange={(e) => setModel(e.target.value)} placeholder="its name on that server" />
          )}
          <small>
            {chosen?.price && `${priceText(chosen.price)}. `}
            {chosen?.description ??
              (listed.length ? "" : srv?.reachable ? "this server lists no models: type the name" : "the server isn't reachable now")}
          </small>
        </label>
      </div>
      {srv?.remote && (
        <p className="small warn-text">
          {srv.name} is off your network: {info.task === "voice" ? "voice samples and lines" : info.task === "vision" ? "frames of the video" : info.task === "text" ? "the transcript and translation" : "the audio"} go there.
        </p>
      )}
      {info.task === "text" && <Thinking data={data} onSaved={onSaved} />}
      <div className="row" style={{ marginTop: 16 }}>
        {changed && (
          <button type="button" className="btn primary small" onClick={save} disabled={saving || !model.trim()}>
            {saving ? "Saving…" : "Save"}
          </button>
        )}
        <button type="button" className="btn small" onClick={tryIt} disabled={busy || trying || changed}>
          {trying ? "Trying… (loading the model can take a minute)" : "Try it"}
        </button>
        {busy && <span className="small faint">a run is on air: try it when it’s done</span>}
        {!busy && changed && <span className="small faint">save first, then try it</span>}
      </div>
      {result && <TestResult result={result} />}
      <ErrorText error={error} />
      <details className="needs">
        <summary>What it needs</summary>
        <p>{info.needs}</p>
        <p className="small muted">For example: {info.examples.join("; ")}.</p>
        <div className="snippet">
          <pre className="mono">{info.snippet}</pre>
          <button type="button" className="btn small" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <p className="small faint">The default model’s entry in the reference box’s llama-swap.yaml (deploy/gpu/llama-swap.yaml).</p>
      </details>
    </section>
  );
}

function Thinking({ data, onSaved }: { data: ModelsOverview; onSaved: () => void }) {
  const [levels, setLevels] = useState(data.efforts);
  const [error, setError] = useState<string | null>(null);
  // follow the saved levels when they change, not on every refresh (that would drop unsaved picks)
  const saved = JSON.stringify(data.efforts);
  useEffect(() => setLevels(JSON.parse(saved)), [saved]);
  const changes = Object.fromEntries(USES.map(([use]) => [use, levels[use]]).filter(([use, level]) => level !== data.efforts[use]));

  async function save() {
    setError(null);
    try {
      await saveModels({ efforts: changes });
      onSaved();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <fieldset className="thinking">
      <legend className="kicker">How hard it thinks</legend>
      <div className="row">
        {USES.map(([use, label]) => (
          <label key={use} className="field" style={{ flex: "1 1 120px" }}>
            {label}
            <select value={levels[use] ?? "default"} onChange={(e) => setLevels({ ...levels, [use]: e.target.value })}>
              {data.effort_levels.map((level) => (
                <option key={level} value={level}>
                  {LEVEL_NAMES[level] ?? level}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <p className="small faint" style={{ margin: "10px 0 0" }}>
        Translation thinks at medium: on a long video low is several times faster and a little rougher. “Server’s default” sends
        nothing, for servers that refuse the setting.
      </p>
      {Object.keys(changes).length > 0 && (
        <button type="button" className="btn primary small" onClick={save} style={{ marginTop: 10 }}>
          Save how it thinks
        </button>
      )}
      <ErrorText error={error} />
    </fieldset>
  );
}

function TestResult({ result }: { result: ModelTest }) {
  const clip = useMemo(() => (result.file ? `${result.file}?t=${Date.now()}` : null), [result]); // a new clip each try
  return (
    <div className={`test-result ${result.ok ? "ok" : "fail"}`} role="status">
      <strong>{result.ok ? "Works" : "Didn’t work"}</strong>
      <span className="mono small muted">
        {result.model} on {result.server} · {result.seconds} s{result.tok_per_s ? ` · ${result.tok_per_s} tok/s` : ""}
        {result.cost ? ` · ${usd(result.cost)}` : ""}
      </span>
      <span className="small">{result.detail}</span>
      {clip && <audio controls src={clip} />}
    </div>
  );
}

/** The hosted setups in one click (OpenRouter): its key, then text and vision on it; and the way back. */
function ReadyMade({ data, onChange }: { data: ModelsOverview; onChange: () => void }) {
  const presets = data.presets ?? [];
  const local = presets.find((p) => !p.url);
  return (
    <section className="panel" aria-labelledby="ready-made">
      <div className="panel-head">
        <h2 id="ready-made">Ready-made</h2>
      </div>
      <div className="stack" style={{ gap: 18 }}>
        {presets
          .filter((p) => p.url)
          .map((p) => (
            <PresetCard key={p.name} preset={p} local={local} onChange={onChange} />
          ))}
      </div>
    </section>
  );
}

function PresetCard({ preset, local, onChange }: { preset: Preset; local?: Preset; onChange: () => void }) {
  const [key, setKey] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uses = Object.entries(preset.tasks)
    .map(([task, model]) => `${task}: ${model}`)
    .join(" · ");

  async function apply(name: string, withKey?: string) {
    setError(null);
    setWorking(true);
    try {
      await applyPreset(name, withKey);
      setKey("");
      onChange();
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="preset">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{preset.title}</strong>
        {preset.active ? <span className="chip ok">in use</span> : preset.key_set ? <span className="chip">key saved</span> : null}
      </div>
      <p className="small muted" style={{ margin: "6px 0 10px" }}>
        {preset.what}
      </p>
      <p className="small mono" style={{ margin: "0 0 10px" }}>
        {uses}
      </p>
      {preset.active ? (
        local && (
          <button type="button" className="btn small" disabled={working} onClick={() => apply(local.name)}>
            {working ? "Switching…" : `Back to ${local.title.toLowerCase()}`}
          </button>
        )
      ) : (
        <form
          className="stack"
          style={{ gap: 8 }}
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void apply(preset.name, key.trim() || undefined);
          }}
        >
          {!preset.key_set && (
            <label className="field" style={{ flex: "0 0 auto" }}>
              Its API key
              <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-or-…" />
              <small>
                Kept on the server, readable only by its owner, never shown again.{" "}
                {preset.keys_at && (
                  <a href={preset.keys_at} target="_blank" rel="noreferrer">
                    Get a key
                  </a>
                )}
              </small>
            </label>
          )}
          <button type="submit" className="btn primary small" disabled={working || (!preset.key_set && !key.trim())}>
            {working ? "Setting it up…" : `Use ${preset.title} for text and vision`}
          </button>
        </form>
      )}
      <p className="small faint" style={{ margin: "10px 0 0" }}>
        To compare: dub the same video again under another name with this on, then look at both runs: each shows its text
        model and what its requests cost.
      </p>
      <ErrorText error={error} />
    </div>
  );
}

function Servers({ data, onChange }: { data: ModelsOverview; onChange: () => void }) {
  const [editing, setEditing] = useState<string | null>(null); // a server's name, or "" for a new one
  const [error, setError] = useState<string | null>(null);
  const done = () => {
    setEditing(null);
    onChange();
  };

  async function remove(name: string) {
    setError(null);
    try {
      await removeModelServer(name);
      onChange();
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <section className="panel" aria-labelledby="servers">
      <div className="panel-head">
        <h2 id="servers">Servers</h2>
        {editing === null && (
          <button type="button" className="btn small" onClick={() => setEditing("")}>
            Add a server
          </button>
        )}
      </div>
      {editing === "" && <ServerForm kinds={data.kinds} onDone={done} onCancel={() => setEditing(null)} />}
      {data.servers.map((s) =>
        editing === s.name ? (
          <ServerForm key={s.name} server={s} kinds={data.kinds} onDone={done} onCancel={() => setEditing(null)} />
        ) : (
          <div key={s.name} className="server-row">
            <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
              <strong>{s.name}</strong>
              <span className="chip">{s.kind}</span>
            </div>
            <div className="mono small muted" style={{ overflowWrap: "anywhere" }}>
              {s.url}
            </div>
            <div className="small" style={{ marginTop: 4 }}>
              <span className={s.reachable ? "ok-text" : "error"}>{serverState(s)}</span> · {keyText(s)}
              {s.remote && <span className="warn-text"> · off your network</span>}
            </div>
            {!s.reachable && s.error && <div className="small faint" style={{ overflowWrap: "anywhere" }}>{s.error}</div>}
            <div className="row" style={{ gap: 8, marginTop: 8 }}>
              <button type="button" className="btn small" onClick={() => setEditing(s.name)}>
                Edit
              </button>
              {s.name !== "main" && (
                <button type="button" className="btn small" onClick={() => remove(s.name)}>
                  Remove
                </button>
              )}
            </div>
          </div>
        ),
      )}
      <ErrorText error={error} />
      <p className="small faint" style={{ marginBottom: 0 }}>
        <b>llama-swap</b>: your GPU box; it starts and stops models as they’re asked for. <b>openai</b>: any OpenAI-compatible
        server (LM Studio, vLLM, Ollama, a cloud API); its voice and separation servers are called directly. Keys stay on the
        box and are never shown again.
      </p>
    </section>
  );
}

function ServerForm({ server, kinds, onDone, onCancel }: { server?: ModelServerInfo; kinds: string[]; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(server?.name ?? "");
  const [url, setUrl] = useState(server?.url ?? "");
  const [kind, setKind] = useState(server?.kind ?? "openai");
  const [key, setKey] = useState("");
  const [dropKey, setDropKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await saveModelServer(name.trim(), { url: url.trim(), kind, ...(key ? { key } : dropKey ? { key: "" } : {}) });
      onDone();
    } catch (e) {
      setError(message(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="server-form stack" style={{ gap: 12 }} onSubmit={submit} aria-label={server ? `Edit ${server.name}` : "Add a server"}>
      {!server && (
        <label className="field">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="cloud" required pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}" />
        </label>
      )}
      <label className="field">
        URL
        <input className="mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://gpu-box:9876" required />
        {server?.key_source === "stored" && <small>moving it to another address forgets its saved key</small>}
      </label>
      <label className="field">
        Kind
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k === "openai" ? "openai (OpenAI-compatible)" : k}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        API key
        <input
          type="password"
          autoComplete="off"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={server?.key_set ? "unchanged (type a new one to replace it)" : "none"}
        />
      </label>
      {server?.key_set && server.key_source === "stored" && (
        <label className="switch">
          <input type="checkbox" checked={dropKey} onChange={(e) => setDropKey(e.target.checked)} disabled={key !== ""} />
          Remove its saved key
        </label>
      )}
      <ErrorText error={error} />
      <div className="row" style={{ gap: 8 }}>
        <button type="submit" className="btn primary small" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
