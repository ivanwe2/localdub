// @vitest-environment jsdom
// The screens in a simulated browser (jsdom), against a fake server: what a person clicks, and
// what the page sends.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Channel, Job, RunSummary, Segment } from "../api";
import { useNotices } from "../hooks";
import Header from "./Header";
import LineFixer from "./LineFixer";
import ModelsScreen from "./ModelsScreen";
import NewRun from "./NewRun";
import ReviewScreen from "./ReviewScreen";
import ShortsScreen from "./ShortsScreen";
import VoicePicker from "./VoicePicker";

type Sent = { method: string; url: string; body: string | null };

/** A fake server: `routes` maps "GET /api/…" to a JSON answer (or a status); every request is recorded. */
function serve(routes: Record<string, unknown>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const url = String(input);
      sent.push({ method, url, body: typeof init?.body === "string" ? init.body : null });
      const key = `${method} ${url}`;
      if (!(key in routes)) return new Response("not here", { status: 404 });
      const answer = routes[key];
      return new Response(typeof answer === "string" ? answer : JSON.stringify(answer), { status: 200 });
    }),
  );
  return sent;
}

class FakeEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null;
  close() {}
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const run = (over: Partial<RunSummary>): RunSummary => ({
  name: "market",
  channel: "studio",
  created: "2026-11-20T10:00:00",
  source: "/v.mp4",
  seconds: 40,
  done: [],
  timings: {},
  finished: false,
  mode: "dub",
  files: { video: null, subtitles: [], shorts: [], original: null, source_video: null },
  waiting_for: null,
  ...over,
});

const channels: Channel[] = [
  { name: "studio", source: "bg", target: "en", source_name: "Bulgarian", target_name: "English", speaker: null, has_voice: true },
];

describe("New run", () => {
  it("a new channel asks for its languages and sends them with the run", async () => {
    const sent = serve({
      "GET /api/videos": [],
      "GET /api/languages": [
        { code: "bg", name: "Bulgarian", dub: false, dictionary: "bg_BG", dictionary_installed: true, apt: "hunspell-bg" },
        { code: "en", name: "English", dub: true, dictionary: "en_US", dictionary_installed: true, apt: "hunspell-en-us" },
        { code: "de", name: "German", dub: true, dictionary: "de_DE", dictionary_installed: false, apt: "hunspell-de-de" },
      ],
      "POST /api/runs": { run: "x" },
    });
    render(<NewRun busy={false} channels={channels} onStarted={() => {}} />);

    fireEvent.change(screen.getByLabelText(/^Channel/), { target: { value: "studio" } });
    expect(screen.getByText(/dubs Bulgarian into English/)).toBeTruthy();
    expect(screen.queryByLabelText("Video language")).toBeNull();

    fireEvent.change(screen.getByLabelText(/^Channel/), { target: { value: "brand-new" } });
    const from = await screen.findByLabelText("Video language");
    await waitFor(() => expect(screen.getByRole("option", { name: /German \(dictionary missing/ })).toBeTruthy());
    fireEvent.change(from, { target: { value: "bg" } });
    fireEvent.change(screen.getByLabelText("Dub language"), { target: { value: "de" } });
    fireEvent.change(screen.getByLabelText(/^Speaker/), { target: { value: "woman" } });
    fireEvent.click(screen.getByRole("button", { name: "On the box" }));
    fireEvent.change(screen.getByLabelText(/A video already on the box/), { target: { value: "/root/dub/videos/market.mp4" } });
    fireEvent.click(screen.getByRole("button", { name: "Start the run" }));

    await waitFor(() => expect(sent.some((s) => s.method === "POST")).toBe(true));
    const body = JSON.parse(sent.find((s) => s.method === "POST")!.body!);
    // a channel's first video is reviewed unless switched off
    expect(body).toMatchObject({ channel: "brand-new", name: "market", source: "bg", target: "de", speaker: "woman", no_review: false });
  });

  it("Shorts only asks a new channel for its video language alone; a known channel isn't reviewed by default", async () => {
    const sent = serve({
      "GET /api/videos": [],
      "GET /api/languages": [
        { code: "bg", name: "Bulgarian", dub: false, dictionary: "bg_BG", dictionary_installed: true, apt: "hunspell-bg" },
        { code: "en", name: "English", dub: true, dictionary: "en_US", dictionary_installed: true, apt: "hunspell-en-us" },
      ],
      "POST /api/runs": { run: "x" },
    });
    render(<NewRun busy={false} channels={channels} onStarted={() => {}} />);

    fireEvent.change(screen.getByLabelText(/^Channel/), { target: { value: "studio" } });
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    fireEvent.change(screen.getByLabelText(/^Channel/), { target: { value: "clips" } });
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Shorts only" }));
    fireEvent.change(await screen.findByLabelText("Video language"), { target: { value: "bg" } });
    expect(screen.queryByLabelText("Dub language")).toBeNull();
    expect(screen.queryByLabelText(/^Speaker/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More Shorts" }));
    fireEvent.click(screen.getByRole("checkbox")); // straight through
    fireEvent.click(screen.getByRole("button", { name: "On the box" }));
    fireEvent.change(screen.getByLabelText(/A video already on the box/), { target: { value: "/root/dub/videos/talk.mp4" } });
    fireEvent.click(screen.getByRole("button", { name: "Start the run" }));

    await waitFor(() => expect(sent.some((s) => s.method === "POST")).toBe(true));
    const body = JSON.parse(sent.find((s) => s.method === "POST")!.body!);
    expect(body).toMatchObject({ channel: "clips", shorts_only: true, shorts: 1, source: "bg", no_review: true });
    expect(body.target).toBeUndefined();
  });
});

const SAMPLES = {
  lang: "en",
  lines: ["One.", "Two.", "Three.", "Four."],
  scored: true,
  candidates: [
    { n: 1, start: 80, end: 98, sim: 0.966, path: "voice-candidates/candidate-1.wav", tests: ["t/1-1.wav", "t/1-2.wav", "t/1-3.wav", "t/1-4.wav"] },
    { n: 2, start: 242, end: 259, sim: 0.957, path: "voice-candidates/candidate-2.wav", tests: ["t/2-1.wav", "t/2-2.wav", "t/2-3.wav", "t/2-4.wav"] },
  ],
  current: { from: "cheese-tour, candidate 2", sim: 0.956, path: "voice-candidates/current.wav", tests: ["t/c-1.wav", "t/c-2.wav", "t/c-3.wav", "t/c-4.wav"] },
  picked: null,
};

describe("Voices", () => {
  it("plays one clip at a time, picks a voice and offers to re-dub", async () => {
    const sent = serve({ "GET /api/runs/market/voice": SAMPLES, "POST /api/runs/market/voice/pick": { run: "market" } });
    render(<VoicePicker run="market" busy={false} finished />);

    const pads = await screen.findAllByRole("button", { name: /Play test sentence 2: Two\./ });
    expect(pads).toHaveLength(3); // the voice in use and two candidates
    fireEvent.click(pads[1]);
    expect(screen.getAllByRole("button", { name: /Stop test sentence 2/ })).toHaveLength(1);
    fireEvent.click(screen.getAllByRole("button", { name: /Play test sentence 3/ })[0]);
    expect(screen.queryAllByRole("button", { name: /Stop test sentence 2/ })).toHaveLength(0); // one at a time

    fireEvent.click(screen.getAllByRole("button", { name: "Use this voice" })[1]);
    await screen.findByText(/Candidate 2 is the channel’s voice now/);
    const pick = sent.find((s) => s.url.endsWith("/voice/pick"));
    expect(JSON.parse(pick!.body!)).toEqual({ pick: 2 });
    expect(screen.getByRole("button", { name: "Re-dub this run with it" })).toBeTruthy();
  });
});

const CANDIDATES = [
  "status: pending",
  "# Shorts candidates, strongest first.",
  "",
  '[ ] 1   00:00-00:20 (20s)   sentences 1-2   "Morning"',
  "    hook:   Good morning.",
  "    starts: One.",
  "",
  '[ ] 2   00:22-00:40 (18s)   sentences 3-3   "Cheese"',
  "    hook:   The cheese.",
  "",
].join("\n");

const SEGMENTS = [
  { id: 1, start: 0, end: 10, src: "Едно.", tgt: "One." },
  { id: 2, start: 12, end: 20, src: "Две.", tgt: "Two." },
  { id: 3, start: 22, end: 40, src: "Три.", tgt: "Three." },
];

describe("Shorts", () => {
  it("ticks Shorts, moves a cut by a sentence and renders them", async () => {
    const waiting = run({ finished: true, waiting_for: { kind: "shorts", lang: "dub", file: "shorts/dub/candidates.txt" }, files: { video: "market.en.mp4", subtitles: [], shorts: [], original: null, source_video: null } });
    const sent = serve({
      "GET /api/runs/market/review": { kind: "shorts", lang: "dub", file: "shorts/dub/candidates.txt", text: CANDIDATES },
      "GET /files/market/segments.json": SEGMENTS,
      "GET /api/runs/market/waveform": { seconds: 40, speech: [10, 90], music: null },
      "PUT /api/runs/market/review": "",
      "POST /api/runs/market/continue": { run: "market" },
    });
    render(<ShortsScreen name="market" runs={[waiting]} channels={channels} busy={false} onChange={() => {}} />);

    await screen.findByRole("heading", { name: "Morning" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Render Short 1" }));
    fireEvent.click(screen.getByRole("button", { name: "End one sentence later" }));
    expect(screen.getByText(/sentences 1–3/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Render 1 Short" }));

    await waitFor(() => expect(sent.some((s) => s.method === "POST" && s.url.endsWith("/continue"))).toBe(true));
    const saved = sent.find((s) => s.method === "PUT")!.body!;
    expect(saved.startsWith("status: approved")).toBe(true);
    expect(saved).toContain('[x] 1   00:00-00:40 (40s)   sentences 1-3   "Morning"');
  });
});

const REVIEW1 = [
  "status: pending",
  "# Review 1 of 2: the transcript.",
  "",
  "[9] 01:14.6   name? Кирчу -> Кирчо (not applied)",
  "BG: Хлебарят Кирчу ни дава питка.",
  "",
].join("\n");

describe("Review 1", () => {
  it("a suggested name goes into the line with one click, and approving sends the file", async () => {
    const waiting = run({ done: ["prep", "separate", "transcribe", "spelling"], waiting_for: { kind: "review1", lang: null, file: "review-1-transcript.txt" } });
    const sent = serve({
      "GET /api/runs/market/review": { kind: "review1", lang: null, file: "review-1-transcript.txt", text: REVIEW1 },
      "PUT /api/runs/market/review": "",
      "POST /api/runs/market/continue": { run: "market" },
    });
    render(<ReviewScreen name="market" runs={[waiting]} channels={channels} busy={false} onChange={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "It is Кирчо" }));
    expect(screen.getByText("Кирчу → Кирчо")).toBeTruthy(); // what the channel learns
    fireEvent.click(screen.getByRole("button", { name: "Approve and translate" }));

    await waitFor(() => expect(sent.some((s) => s.method === "POST")).toBe(true));
    const saved = sent.find((s) => s.method === "PUT")!.body!;
    expect(saved.split("\n")[0]).toBe("status: approved");
    expect(saved).toContain("BG: Хлебарят Кирчо ни дава питка.");
  });
});

const OVERVIEW = {
  servers: [
    {
      name: "main", url: "http://127.0.0.1:9876", kind: "llama-swap", key_set: true, key_source: "LLAMA_API_KEY", remote: false,
      reachable: true, error: null,
      models: [
        { id: "gemma4-31b", name: "Gemma 4 31B", description: "text for localdub" },
        { id: "qwen38", name: null, description: null },
        { id: "qwen3-tts", name: null, description: null },
      ],
    },
    { name: "cloud", url: "https://api.example.com", kind: "openai", key_set: true, key_source: "stored", remote: true, reachable: false, error: "offline", models: [] },
  ],
  tasks: [
    { task: "text", title: "Text", what: "Translates.", used_by: ["translate"], needs: "A chat model.", examples: ["Gemma"], snippet: "gemma4-31b:\n", server: "main", model: "gemma4-31b", listed: true },
    { task: "voice", title: "Voice", what: "Speaks.", used_by: ["voice"], needs: "Qwen3-TTS.", examples: ["Qwen3-TTS"], snippet: "qwen3-tts:\n", server: "main", model: "qwen3-tts", listed: true },
  ],
  efforts: { spelling: "none", translate: "medium", shorten: "none", shorts: "low" },
  effort_levels: ["default", "none", "low", "medium", "high"],
  kinds: ["llama-swap", "openai"],
};

describe("Models", () => {
  it("moves a task to another model, changes how hard it thinks, and tries the voice", async () => {
    const sent = serve({
      "GET /api/models": OVERVIEW,
      "PUT /api/models": "",
      "POST /api/models/test": {
        task: "voice", server: "main", model: "qwen3-tts", ok: true, seconds: 2.1, detail: 'said "Hi everyone"', tok_per_s: null,
        file: "/api/models/test/voice.wav",
      },
    });
    render(<ModelsScreen busy={false} />);
    const puts = () => sent.filter((s) => s.method === "PUT").map((s) => JSON.parse(s.body!));

    const text = await screen.findByRole("region", { name: "Text" });
    fireEvent.change(within(text).getByLabelText(/^Model/), { target: { value: "qwen38" } });
    fireEvent.click(within(text).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]).toEqual({ tasks: { text: { server: "main", model: "qwen38" } } });

    fireEvent.change(within(text).getByLabelText("Translation"), { target: { value: "low" } });
    fireEvent.click(within(text).getByRole("button", { name: "Save how it thinks" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1]).toEqual({ efforts: { translate: "low" } });

    const voice = screen.getByRole("region", { name: "Voice" });
    fireEvent.click(within(voice).getByRole("button", { name: "Try it" }));
    expect(await within(voice).findByText("Works")).toBeTruthy();
    expect(voice.querySelector("audio")?.getAttribute("src")).toMatch(/^\/api\/models\/test\/voice\.wav\?t=/);
    const servers = screen.getByRole("region", { name: "Servers" });
    expect(within(servers).getByText(/off your network/)).toBeTruthy();
    expect(within(servers).getByText("not reachable")).toBeTruthy();
  });
});

describe("Fixing a line", () => {
  const take = { ok: true, why: "", heard: "One.", fit: "fits", seconds: 1.2, tries: 1 };
  const lines: Segment[] = [
    { id: 1, start: 0, end: 2, src: "Едно.", tgt: "One.", take, placed: 0.1, overlap: 0 },
    { id: 2, start: 3, end: 5, src: "Две.", tgt: "Two.", take: { ...take, ok: false, why: "last word 'two' missing", heard: "To", tries: 3 }, placed: 3 },
  ];

  it("lists the lines worth a listen, plays both sides and sends new words", async () => {
    const sent = serve({ "POST /api/runs/market/lines/2": { run: "market", state: "running" } });
    const started = vi.fn();
    const { rerender } = render(
      <LineFixer name="market" segments={lines} picked={null} onPick={() => {}} busy={false} src="BG" tgt="EN" version="1" onStarted={started} />,
    );
    expect(screen.getByRole("button", { name: /Worth a listen · 1/ })).toBeTruthy();
    expect(screen.queryByText("One.")).toBeNull(); // it passed: only under "All"
    rerender(<LineFixer name="market" segments={lines} picked={2} onPick={() => {}} busy={false} src="BG" tgt="EN" version="1" onStarted={started} />);

    fireEvent.click(screen.getByRole("button", { name: "Original" }));
    const audio = (HTMLMediaElement.prototype.pause as ReturnType<typeof vi.fn>).mock.contexts.at(-1) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe("/files/market/audio44.wav?v=1");
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Another take" })).toHaveProperty("disabled", false);

    fireEvent.change(screen.getByLabelText("EN · dub"), { target: { value: "  Two,\n please. " } });
    expect(screen.getByRole("button", { name: "Another take" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Voice the new words" }));
    await waitFor(() => expect(started).toHaveBeenCalled());
    const post = sent.find((s) => s.method === "POST")!;
    expect([post.url, JSON.parse(post.body!)]).toEqual(["/api/runs/market/lines/2", { text: "Two, please." }]);
  });
});

describe("Notifications", () => {
  it("the bell asks the browser once, then tells when the run on air waits for you", async () => {
    const shown: { title: string; body?: string }[] = [];
    class FakeNotification {
      static permission: NotificationPermission = "default";
      static requestPermission = vi.fn(async () => (FakeNotification.permission = "granted"));
      onclick: (() => void) | null = null;
      constructor(title: string, options?: NotificationOptions) {
        shown.push({ title, body: options?.body });
      }
      close() {}
    }
    vi.stubGlobal("Notification", FakeNotification);
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    const base: Job = {
      run: "market", state: "running", stages: ["prep"], done: [], current: "prep", waiting_for: null, error: null, calls: [],
      log: [], started_ms: 1, ended_ms: null, stage_started_ms: null, timings: {}, progress: null,
    };
    function Bell({ job }: { job: Job }) {
      const notices = useNotices(job);
      return <Header job={job} status={null} route={{ page: "studio" }} runs={[]} theme="dark" onTheme={() => {}} notices={notices} />;
    }
    const { rerender } = render(<Bell job={base} />);

    fireEvent.click(screen.getByRole("button", { name: /Tell me when a run needs me/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Notifications on/ })).toBeTruthy());
    rerender(<Bell job={{ ...base, state: "waiting", waiting_for: { kind: "review1", file: "r" } }} />);

    expect(shown).toEqual([{ title: "market: your turn", body: "Review 1 of 2 (the transcript)" }]);
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
  });
});
