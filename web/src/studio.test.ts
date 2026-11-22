import { describe, expect, it } from "vitest";

import type { Call, Job, RunSummary, Segment } from "./api";
import {
  at,
  callSpeed,
  candidateSentences,
  clips,
  clock,
  latestSpeed,
  ledClasses,
  ledsLit,
  marks,
  parseReview,
  parseWhen,
  peaksPath,
  replaceWord,
  rulerTicks,
  runState,
  setCandidateSentences,
  setGlossary,
  setReviewLine,
  sinePath,
  splitMarks,
  stageStrip,
  timecode,
} from "./studio";

const job = (over: Partial<Job>): Job => ({
  run: "market",
  state: "running",
  stages: ["prep", "translate", "review2", "voice", "mix"],
  done: [],
  current: null,
  waiting_for: null,
  error: null,
  calls: [],
  log: [],
  started_ms: 1000,
  ended_ms: null,
  stage_started_ms: null,
  timings: {},
  progress: null,
  ...over,
});

const run = (over: Partial<RunSummary>): RunSummary => ({
  name: "market",
  channel: "c",
  created: "2026-11-20T10:00:00",
  source: "/v.mp4",
  seconds: 170,
  done: [],
  timings: {},
  finished: false,
  mode: "dub",
  files: { video: null, subtitles: [], shorts: [], original: null, source_video: null },
  waiting_for: null,
  ...over,
});

const call = (over: Partial<Call>): Call => ({
  kind: "chat",
  model: "gemma4-31b",
  label: "x",
  seconds: 2,
  tokens: null,
  tok_per_s: null,
  prompt_tokens: null,
  prompt_per_s: null,
  chars: null,
  audio_seconds: null,
  ...over,
});

const segs: Segment[] = [
  { id: 1, start: 0, end: 10, src: "Едно.", tgt: "One." },
  { id: 2, start: 12, end: 20, src: "Две.", tgt: "Two." },
  { id: 3, start: 22, end: 40, src: "Три.", tgt: "Three." },
];

describe("clocks", () => {
  it("timecodes and short clocks", () => {
    expect(timecode(401)).toBe("00:06:41");
    expect(timecode(72, true)).toBe("01:12");
    expect(timecode(3725, true)).toBe("01:02:05");
    expect(clock(107)).toBe("1:47");
    expect(clock(3725)).toBe("1:02:05");
  });
});

describe("stage strips", () => {
  it("done stages show their time, reviews say approved, the current one its progress", () => {
    const strip = stageStrip(
      run({ done: ["prep", "translate", "review2"], timings: { prep: 1.2, translate: 47 } }),
      job({
        done: ["prep", "translate", "review2"],
        current: "voice",
        progress: { stage: "voice", label: "voicing, take 1", done: 31, total: 58, line: 31, at: 72 },
      }),
    );
    expect(strip.map((s) => [s.stage, s.cls, s.note])).toEqual([
      ["prep", "done", "0:01"],
      ["translate", "done", "0:47"],
      ["review2", "you", "approved"],
      ["voice", "now", "31 of 58"],
      ["mix", "todo", ""],
    ]);
  });

  it("without progress, the current stage counts its own time", () => {
    const strip = stageStrip(null, job({ current: "prep", stage_started_ms: 10_000 }), 75_000);
    expect(strip[0]).toMatchObject({ cls: "now", note: "1:05" });
  });

  it("a run waiting for a review, and a failed job", () => {
    const waiting = stageStrip(run({ done: ["prep", "separate", "transcribe", "spelling"], waiting_for: { kind: "review1", lang: null, file: "r" } }), null);
    expect(waiting.find((s) => s.stage === "review1")?.cls).toBe("wait");
    const failed = stageStrip(null, job({ state: "failed", current: "translate", done: ["prep"] }));
    expect(failed.find((s) => s.stage === "translate")?.cls).toBe("fail");
  });

  it("a Shorts-only run: the transcript, then its Shorts (waiting for the pick, then done)", () => {
    const checked = ["prep", "separate", "transcribe", "spelling", "review1"];
    const picking = stageStrip(run({ mode: "shorts", done: checked, waiting_for: { kind: "shorts", lang: "original", file: "f" } }), null);
    expect(picking.map((s) => s.stage)).toEqual([...checked, "shorts-original"]);
    expect(picking.at(-1)).toMatchObject({ name: "Shorts, original", cls: "wait" });
    const rendered = stageStrip(run({ mode: "shorts", done: checked, timings: { "shorts-original": 95 } }), null);
    expect(rendered.at(-1)).toMatchObject({ cls: "done", note: "1:35" });
    expect(runState(run({ mode: "shorts", waiting_for: { kind: "review1", lang: null, file: "r" } }), null).text).toBe("Your turn · the transcript");
  });
});

describe("run states", () => {
  it("in words, for the runs list", () => {
    expect(runState(run({}), job({ current: "voice" }))).toEqual({ text: "On air · voice", cls: "air" });
    expect(runState(run({ waiting_for: { kind: "review2", lang: null, file: "r" } }), null).cls).toBe("wait");
    expect(runState(run({ finished: true, files: { video: "v", subtitles: [], shorts: ["a", "b"], original: null, source_video: null } }), null).text).toBe("Done · 2 Shorts");
    expect(runState(run({ done: ["prep", "separate"] }), null)).toEqual({ text: "Stopped after separate", cls: "stop" });
    expect(runState(run({}), job({ state: "failed", error: "boom" })).cls).toBe("fail");
  });
});

describe("the timeline", () => {
  it("places lines by time and follows the voicing line by line", () => {
    const voicing = job({ done: ["translate"], current: "voice", progress: { stage: "voice", label: "voicing, take 1", done: 2, total: 3, line: 2, at: 12 } });
    const c = clips(segs, 40, run({ done: ["translate"] }), voicing);
    expect(c.map((x) => x.dub)).toEqual(["done", "now", "ghost"]);
    expect(c.map((x) => x.text)).toEqual(["done", "done", "done"]);
    expect(c[1].left).toBe("30.000%");
    expect(c[1].width).toBe("20.000%");
    expect(at(80, 40)).toBe("100.000%");
  });

  it("while translating, the lines after the latest one are still to come", () => {
    const translating = job({ current: "translate", progress: { stage: "translate", label: "translating", done: 1, total: 3, line: 1, at: 10 } });
    const untranslated = segs.map((s) => ({ ...s, tgt: null }));
    expect(clips(untranslated, 40, null, translating).map((x) => x.text)).toEqual(["done", "now", "ghost"]);
  });
});

describe("drawing", () => {
  it("background lines loop, waveforms stroke each step, rulers pick a round step", () => {
    const line = sinePath(360, 26);
    expect(line.startsWith("M0 100 Q90 48 180 100 T360 100")).toBe(true);
    expect(line.endsWith("T2880 100")).toBe(true);
    expect(peaksPath([0, 100])).toBe("M250.0 49.5V50.5M750.0 4.0V96.0");
    expect(peaksPath([])).toBe("");
    expect(rulerTicks(170).map((t) => t.label)).toEqual(["00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "02:50"]);
    expect(rulerTicks(1804).map((t) => t.label)).toEqual(["00:00", "05:00", "10:00", "15:00", "20:00", "25:00", "30:04"]);
    expect(rulerTicks(0)).toEqual([]);
  });
});

describe("meters", () => {
  it("light LEDs on a log or straight scale", () => {
    expect(ledsLit(104, 300, 24, true)).toBe(20);
    expect(ledsLit(3, 6)).toBe(12);
    expect(ledsLit(null, 6)).toBe(0);
    expect(ledsLit(900, 300, 24, true)).toBe(24);
    expect(ledClasses(22).slice(14, 24)).toEqual(["on", "on", "warm", "warm", "warm", "warm", "warm", "hot", "", ""]);
  });

  it("read the latest speeds from the calls", () => {
    const calls = [
      call({ tok_per_s: 98 }),
      call({ kind: "tts", model: "qwen3-tts", seconds: 2, audio_seconds: 6 }),
      call({ tok_per_s: 104 }),
    ];
    expect(latestSpeed(calls, "chat")).toEqual({ value: 104, model: "gemma4-31b" });
    expect(latestSpeed(calls, "tts")).toEqual({ value: 3, model: "qwen3-tts" });
    expect(latestSpeed(calls, "asr")).toBeNull();
    expect(callSpeed(calls[1])).toBe("3.0×");
    expect(callSpeed(calls[2])).toBe("104 tok/s");
  });
});

const REVIEW1 = [
  "status: pending",
  "# Review 1 of 2: the transcript.",
  "voice: auto",
  "",
  "[9] 01:14   name? Кирчу -> Кирчо (not applied)   unknown: ютубъри",
  "BG: Хлебарят Кирчу поздравява ютубъри.",
  "",
  "[12] 01:46   fixed: найемите -> наемите",
  "BG: После минаваме покрай наемите сергии.",
  "",
].join("\n");

const REVIEW2 = [
  "status: pending",
  "# Review 2 of 2: the translation.",
  "",
  "glossary:",
  "сирене = cheese",
  "",
  "[1] 00:02   slot 6.5s   41/45 chars",
  "BG: Добро утро от пазара.",
  "EN: Good morning from the market.",
  "",
  "[2] 00:10   slot 3.1s   52/40 chars   OVER   [overlaps music]",
  "BG: Днес търсим най-хубавото сирене.",
  "EN: Today we are looking for the very best cheese there is.",
  "",
].join("\n");

describe("reviews", () => {
  it("review 1: blocks, notes and the voice line", () => {
    const r = parseReview(REVIEW1);
    expect(r.status).toBe("pending");
    expect(r.voice).toBe("auto");
    expect(r.glossary).toBeNull();
    expect(r.blocks.map((b) => [b.id, b.at])).toEqual([[9, "01:14"], [12, "01:46"]]);
    expect(r.blocks[0].notes).toEqual([
      { kind: "name", pairs: [["Кирчу", "Кирчо"]] },
      { kind: "unknown", words: ["ютубъри"] },
    ]);
    expect(r.blocks[1].lines).toEqual([{ index: 8, label: "BG", text: "После минаваме покрай наемите сергии." }]);
    expect(marks(r.blocks[0])).toEqual(["Кирчу", "ютубъри"]);
  });

  it("review 2: the glossary, budgets and both lines", () => {
    const r = parseReview(REVIEW2);
    expect(r.glossary).toEqual([["сирене", "cheese"]]);
    expect(r.blocks[1].notes).toEqual([
      { kind: "budget", slot: 3.1, chars: 52, budget: 40, over: true },
      { kind: "note", text: "overlaps music" },
    ]);
    expect(r.blocks[1].lines.map((l) => l.label)).toEqual(["BG", "EN"]);
  });

  it("edits go back into the file, line for line", () => {
    const r = parseReview(REVIEW1);
    const line = r.blocks[0].lines[0];
    const fixed = setReviewLine(REVIEW1, line, replaceWord(line.text, "Кирчу", "Кирчо"));
    expect(fixed.split("\n")[5]).toBe("BG: Хлебарят Кирчо поздравява ютубъри.");
    expect(fixed.split("\n").length).toBe(REVIEW1.split("\n").length);
    expect(replaceWord("кирчу Кирчу Кирчуто", "Кирчу", "X")).toBe("кирчу X Кирчуто");
    const glossary = setGlossary(REVIEW2, [["сирене", "cheese"], ["мед", "honey"], ["", "x"]]);
    expect(parseReview(glossary).glossary).toEqual([["сирене", "cheese"], ["мед", "honey"]]);
    expect(parseReview(glossary).blocks).toHaveLength(2);
  });

  it("marks words in any alphabet, whole words only", () => {
    expect(splitMarks("Кирчу и Кирчуто", ["Кирчу"])).toEqual([
      { text: "Кирчу", mark: true },
      { text: " и Кирчуто", mark: false },
    ]);
    expect(splitMarks("plain", [])).toEqual([{ text: "plain", mark: false }]);
  });
});

describe("Shorts", () => {
  const FILE = ['status: pending', '[ ] 1   00:00-00:20 (20s)   sentences 1-2   "Morning"', "    hook:   One."].join("\n");

  it("reads a candidate's times and sentences, and moves its cut by whole sentences", () => {
    expect(parseWhen("00:10-00:40 (30s)")).toEqual([10, 40]);
    expect(candidateSentences(FILE, 1)).toEqual([1, 2]);
    const longer = setCandidateSentences(FILE, 1, 1, 3, segs);
    expect(longer.split("\n")[1]).toBe('[ ] 1   00:00-00:40 (40s)   sentences 1-3   "Morning"');
    expect(setCandidateSentences(FILE, 1, 3, 1, segs)).toBe(FILE); // not backwards
  });
});
