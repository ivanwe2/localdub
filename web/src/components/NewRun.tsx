// A new run: the video (uploaded, downloaded from a link, or already on the box), the channel (a
// new one chooses its languages), the run's name and options.

import { type DragEvent, type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  type Channel,
  type Download,
  type Language,
  listLanguages,
  listVideos,
  startDownload,
  startRun,
  uploadVideo,
  type Video,
} from "../api";
import {
  defaultTarget,
  dictionaryNote,
  downloadText,
  findChannel,
  formatBytes,
  languageFields,
  nameFromTitle,
  partShare,
  suggestName,
  uploadName,
} from "../format";
import { go, useDownload } from "../hooks";
import { ErrorText, message, UploadIcon } from "./common";

function remembered(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // private window or blocked storage: nothing to remember then
  }
}

type Source = "upload" | "link" | "path";

type NewRunProps = { busy: boolean; channels: Channel[]; onStarted: () => void };

export default function NewRun({ busy, channels, onStarted }: NewRunProps) {
  const [source, setSource] = useState<Source>("upload");
  const [videos, setVideos] = useState<Video[]>([]);
  const [video, setVideo] = useState("");
  const [upload, setUpload] = useState<number | null>(null); // 0..1 while uploading
  const [over, setOver] = useState(false);
  const [channel, setChannel] = useState(() => remembered("localdub.channel"));
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [reviews, setReviews] = useState(false);
  const [shorts, setShorts] = useState(0);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [languages, setLanguages] = useState<Language[] | null>(null);
  const [languagesError, setLanguagesError] = useState<string | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [speaker, setSpeaker] = useState("");
  const channelInput = useRef<HTMLInputElement>(null);

  const refreshVideos = useCallback(() => {
    listVideos()
      .then(setVideos)
      .catch(() => setVideos([]));
  }, []);
  useEffect(refreshVideos, [refreshVideos]);

  const known = findChannel(channel, channels);
  const isNew = channel.trim() !== "" && !known;
  useEffect(() => {
    if (!isNew || languages !== null) return; // asked once, when a new channel's name is typed
    listLanguages()
      .then((list) => {
        setLanguages(list);
        setTo((current) => current || defaultTarget(list));
      })
      .catch((e) => {
        setLanguages([]);
        setLanguagesError(message(e));
      });
  }, [isNew, languages]);

  function chooseVideo(path: string, title?: string) {
    setVideo(path);
    if (!nameEdited) setName((title && nameFromTitle(title)) || suggestName(path));
  }

  const downloaded = useCallback(
    (d: Download) => {
      refreshVideos();
      if (d.video) {
        setVideo(d.video.path);
        if (!nameEdited) setName((d.title && nameFromTitle(d.title)) || suggestName(d.video.path));
      }
    },
    [refreshVideos, nameEdited],
  );

  async function uploadFile(file: File) {
    setError(null);
    setUpload(0);
    try {
      const uploaded = await uploadVideo(file, uploadName(file.name), setUpload);
      refreshVideos();
      chooseVideo(uploaded.path);
    } catch (e) {
      setError(message(e));
    } finally {
      setUpload(null);
    }
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    const file = event.dataTransfer.files[0];
    if (file) void uploadFile(file);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSending(true);
    try {
      await startRun({
        video: video.trim(),
        channel: channel.trim(),
        name: name.trim(),
        no_review: !reviews,
        shorts,
        ...languageFields(channel, channels, from, to, speaker),
      });
      remember("localdub.channel", channel.trim());
      onStarted();
      go({ page: "studio" });
    } catch (e) {
      setError(message(e));
    } finally {
      setSending(false);
    }
  }

  const uploading = upload !== null;
  const chosen = videos.find((v) => v.path === video);
  const tabs: [Source, string][] = [
    ["upload", "Upload"],
    ["link", "From a link"],
    ["path", "On the box"],
  ];
  return (
    <>
      <div className="page-title">
        <div>
          <h1>New run</h1>
          <p>A video in, a dubbed video out, in the speaker’s own voice. Music and effects stay as they are.</p>
        </div>
      </div>
      <div className="columns">
        <form className="main stack" style={{ gap: 24 }} onSubmit={submit} aria-label="New run">
          <section className="panel">
            <div className="step">
              <span className="n">01</span>
              <h2>The video</h2>
            </div>
            <div className="tabs" role="group" aria-label="Where the video comes from">
              {tabs.map(([key, label]) => (
                <button key={key} type="button" aria-pressed={source === key} onClick={() => setSource(key)}>
                  {label}
                </button>
              ))}
            </div>
            {source === "upload" && (
              <div
                className={`drop${over ? " over" : ""}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setOver(true);
                }}
                onDragLeave={() => setOver(false)}
                onDrop={drop}
              >
                <UploadIcon />
                <div>
                  <strong style={{ color: "var(--text)" }}>Drop a video here</strong> or{" "}
                  <label>
                    choose a file
                    <input
                      className="visually-hidden"
                      type="file"
                      accept="video/*"
                      disabled={uploading}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (file) void uploadFile(file);
                      }}
                    />
                  </label>
                </div>
                <div className="mono small faint">streamed to the box, into its videos folder</div>
                {uploading && (
                  <div style={{ width: "100%", maxWidth: 420 }}>
                    <div className="bar air thick" role="progressbar" aria-label="Upload" aria-valuenow={Math.round(upload * 100)} aria-valuemin={0} aria-valuemax={100}>
                      <span style={{ width: `${upload * 100}%` }} />
                    </div>
                    <div className="mono small muted" style={{ marginTop: 6 }}>
                      {Math.round(upload * 100)}%
                    </div>
                  </div>
                )}
              </div>
            )}
            {source === "link" && <DownloadBox onDone={downloaded} />}
            {source === "path" && (
              <div className="row" style={{ marginTop: 18 }}>
                <label className="field">
                  A video already on the box
                  <input
                    className="mono"
                    value={video}
                    onChange={(e) => chooseVideo(e.target.value)}
                    placeholder="/root/dub/videos/…"
                  />
                  <small>Only videos inside the localdub folder can be dubbed.</small>
                </label>
              </div>
            )}
            {video && (
              <p className="small muted" style={{ margin: "14px 0 0" }}>
                Video: <span className="mono">{chosen?.name ?? video}</span>
              </p>
            )}
          </section>

          <section className="panel">
            <div className="step">
              <span className="n">02</span>
              <h2>The channel</h2>
            </div>
            <div className="row">
              <label className="field">
                Channel
                <input
                  ref={channelInput}
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  placeholder="mychannel"
                  list="channel-names"
                  required
                />
                <small>
                  {known
                    ? `dubs ${known.source_name} into ${known.target_name}${known.speaker ? ` · speaker: a ${known.speaker}` : ""}${known.has_voice ? " · has its voice" : " · its voice is picked in the first review"}`
                    : isNew
                      ? "a new channel: choose its languages below"
                      : "an existing channel, or a new name"}
                </small>
                <datalist id="channel-names">
                  {channels.map((c) => (
                    <option key={c.name} value={c.name} />
                  ))}
                </datalist>
              </label>
            </div>
            <div className="row" style={{ gap: 8, marginTop: 12 }}>
              {channels.slice(0, 4).map((c) => (
                <button key={c.name} type="button" className="chip" aria-pressed={channel === c.name} onClick={() => setChannel(c.name)}>
                  {c.name} · {c.source.toUpperCase()} → {c.target.toUpperCase()}
                </button>
              ))}
              <button
                type="button"
                className="chip"
                onClick={() => {
                  setChannel("");
                  channelInput.current?.focus();
                }}
              >
                + a new channel
              </button>
            </div>
            {isNew && (
              <fieldset className="new-channel">
                <legend className="kicker amber">A new channel · its languages stay with it</legend>
                <ErrorText error={languagesError && `Couldn't load the languages: ${languagesError}`} />
                <div className="row">
                  <label className="field">
                    Video language
                    <select value={from} onChange={(e) => setFrom(e.target.value)} required>
                      <option value="">Choose…</option>
                      {(languages ?? []).map((l) => (
                        <option key={l.code} value={l.code}>
                          {l.name}
                          {dictionaryNote(l)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Dub language
                    <select value={to} onChange={(e) => setTo(e.target.value)} required>
                      <option value="">Choose…</option>
                      {(languages ?? [])
                        .filter((l) => l.dub && l.code !== from)
                        .map((l) => (
                          <option key={l.code} value={l.code}>
                            {l.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="field">
                    Speaker
                    <select value={speaker} onChange={(e) => setSpeaker(e.target.value)}>
                      <option value="">Not set</option>
                      <option value="man">A man</option>
                      <option value="woman">A woman</option>
                    </select>
                    <small>only for the grammar of “I” in gendered languages</small>
                  </label>
                </div>
                <p className="small faint" style={{ margin: "12px 0 0" }}>
                  The channel’s voice is picked in its first review, from samples of this video.
                </p>
              </fieldset>
            )}
          </section>

          <section className="panel">
            <div className="step">
              <span className="n">03</span>
              <h2>The run</h2>
            </div>
            <div className="row bottom" style={{ gap: 20 }}>
              <label className="field">
                Run name
                <input
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    setNameEdited(true);
                  }}
                  placeholder="market"
                  required
                />
                <small>from the video’s name or title; letters, digits, . _ -</small>
              </label>
              <div className="stack" style={{ gap: 6 }}>
                <span style={{ fontSize: 14, fontWeight: 500 }} id="shorts-label">
                  Shorts in each language
                </span>
                <div className="stepper" role="group" aria-labelledby="shorts-label">
                  <button type="button" aria-label="Fewer Shorts" disabled={shorts === 0} onClick={() => setShorts(Math.max(0, shorts - 1))}>
                    −
                  </button>
                  <output aria-live="polite">{shorts}</output>
                  <button type="button" aria-label="More Shorts" disabled={shorts === 6} onClick={() => setShorts(Math.min(6, shorts + 1))}>
                    +
                  </button>
                </div>
              </div>
            </div>
            <label className="switch" style={{ marginTop: 18 }}>
              <input type="checkbox" checked={reviews} onChange={(e) => setReviews(e.target.checked)} />
              Review the transcript and the translation before the voice
            </label>
            <p className="small faint" style={{ margin: "6px 0 0 30px" }}>
              {reviews
                ? "Two stops: the transcript (names, spelling, the voice) and the translation. What you fix there, the channel learns."
                : "Straight through; nothing is learned for the channel."}
            </p>
          </section>

          <ErrorText error={error} />
          <div className="row">
            <button type="submit" className="btn primary" style={{ minWidth: 180, minHeight: 44 }} disabled={sending || busy || uploading || !video.trim()}>
              {busy ? "A run is on air…" : sending ? "Starting…" : "Start the run"}
            </button>
            <span className="small muted">One run at a time on the GPUs; a download can run beside it.</span>
          </div>
        </form>

        <aside className="side panel" aria-labelledby="videos">
          <div className="panel-head">
            <h2 id="videos">Videos on the box</h2>
            <span className="kicker">{videos.length}</span>
          </div>
          {videos.length === 0 && <p className="empty small">None yet: upload one or download it from a link.</p>}
          <div style={{ marginTop: 14 }}>
            {videos.map((v) => (
              <div key={v.path} className={`video-row${v.path === video ? " chosen" : ""}`}>
                <span className="name">{v.name}</span>
                <span className="mono small muted">{formatBytes(v.bytes)}</span>
                <button type="button" className="btn small" aria-pressed={v.path === video} onClick={() => chooseVideo(v.path)}>
                  {v.path === video ? "Chosen" : "Use"}
                </button>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </>
  );
}

/**
 * Downloading a video from a link with yt-dlp on the server, live. When a download this page saw
 * going is done, `onDone` gets it.
 */
function DownloadBox({ onDone }: { onDone: (download: Download) => void }) {
  const download = useDownload();
  const [url, setUrl] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Download["state"] | undefined>(undefined);

  useEffect(() => {
    if (seen.current === "downloading" && download?.state === "done") onDone(download);
    seen.current = download?.state;
  }, [download, onDone]);

  const going = download?.state === "downloading";
  async function start() {
    if (!url.trim() || going) return;
    setError(null);
    setSending(true);
    try {
      await startDownload(url.trim()); // its progress comes live through useDownload
      setUrl("");
    } catch (e) {
      setError(message(e));
    } finally {
      setSending(false);
    }
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault(); // Enter here downloads; it doesn't start the run
    void start();
  }

  const share = download ? partShare(download) : 0;
  return (
    <div style={{ marginTop: 18 }}>
      <div className="row bottom">
        <label className="field">
          Link to the video
          <input
            className="mono"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={onKey}
            placeholder="https://www.youtube.com/watch?v=…"
          />
        </label>
        <button type="button" className="btn" style={{ minHeight: 44 }} disabled={!url.trim() || going || sending} onClick={() => void start()}>
          {going ? "Downloading…" : sending ? "Starting…" : "Download"}
        </button>
      </div>
      <p className="small faint" style={{ margin: "8px 0 0" }}>
        YouTube and the other sites yt-dlp knows, up to 1080p. Only videos you have the rights to dub.
      </p>
      <ErrorText error={error} />
      {going && (
        <div className="download-card" aria-live="polite">
          <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
            <strong style={{ overflowWrap: "anywhere" }}>{download.title ?? download.url}</strong>
            {download.parts > 1 && download.part > 0 && (
              <span className="mono small muted">
                part {download.part} of {download.parts}
              </span>
            )}
          </div>
          <div className="bar air thick" role="progressbar" aria-label="Download" aria-valuenow={Math.round(share * 100)} aria-valuemin={0} aria-valuemax={100} style={{ marginTop: 12 }}>
            <span style={{ width: `${share * 100}%` }} />
          </div>
          <p className="mono small muted" style={{ margin: "10px 0 0" }}>
            {downloadText(download)}
          </p>
        </div>
      )}
      {download?.state === "done" && download.video && (
        <p className="small muted" style={{ margin: "12px 0 0" }}>
          Downloaded: <span className="mono">{download.video.name}</span>
        </p>
      )}
      {download?.state === "failed" && <p className="error">The download failed: {download.error}</p>}
    </div>
  );
}
