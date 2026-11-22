// @vitest-environment jsdom
// The screens in a simulated browser (jsdom), against a fake server: what a person clicks, and
// what the page sends.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Channel, RunSummary } from "../api";
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
