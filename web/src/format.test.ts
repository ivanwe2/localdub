import { describe, expect, it } from "vitest";

import { type Channel, fileUrl, type Job, type Language } from "./api";
import {
  approve,
  defaultTarget,
  dictionaryNote,
  findChannel,
  languageFields,
  likenessShare,
  setVoiceLine,
  stretch,
  voiceLine,
  candidates,
  formatBytes,
  formatSeconds,
  sparkline,
  progress,
  suggestName,
  toggleCandidate,
  uploadName,
  waitingText,
} from "./format";

const job = (over: Partial<Job>): Job => ({
  run: "market",
  state: "running",
  stages: ["prep", "separate", "transcribe", "mix"],
  done: [],
  current: null,
  waiting_for: null,
  error: null,
  calls: [],
  log: [],
  ...over,
});

describe("formatSeconds", () => {
  it("shows minutes and seconds, and hours when there are any", () => {
    expect(formatSeconds(0)).toBe("0:00");
    expect(formatSeconds(170)).toBe("2:50");
    expect(formatSeconds(1804.8)).toBe("30:05");
    expect(formatSeconds(3725)).toBe("1:02:05");
  });
});

describe("formatBytes", () => {
  it("shows gigabytes, megabytes or kilobytes", () => {
    expect(formatBytes(14_971_568_128)).toBe("15.0 GB");
    expect(formatBytes(5_000_000)).toBe("5 MB");
    expect(formatBytes(1_400)).toBe("1 kB");
  });
});

describe("sparkline", () => {
  it("draws the values left to right, the largest at the top", () => {
    expect(sparkline([0, 50, 100], 100, 20)).toBe("0.0,20.0 50.0,10.0 100.0,0.0");
  });

  it("needs two values, and copes with all zeros", () => {
    expect(sparkline([7], 100, 20)).toBe("");
    expect(sparkline([0, 0], 10, 10)).toBe("0.0,10.0 10.0,10.0");
  });
});

describe("progress", () => {
  it("is the finished share of the stages", () => {
    expect(progress(job({}))).toBe(0);
    expect(progress(job({ done: ["prep", "separate"] }))).toBe(0.5);
  });

  it("is complete when the job is done, and 0 before the stages are known", () => {
    expect(progress(job({ state: "done", done: ["prep"] }))).toBe(1);
    expect(progress(job({ stages: [] }))).toBe(0);
  });
});

describe("suggestName", () => {
  it("makes a safe run name from the video's file name", () => {
    expect(suggestName("/videos/My Video (1).mp4")).toBe("My-Video-1");
    expect(suggestName("C:\\clips\\market.en.mp4")).toBe("market.en");
    expect(suggestName("/root/dub/Пазар в София.mp4")).toBe("Пазар-в-София");
    expect(suggestName("/x/--weird--.mkv")).toBe("weird");
    expect(suggestName("")).toBe("");
  });

  it("keeps at most 64 characters", () => {
    expect(suggestName(`/v/${"a".repeat(100)}.mp4`)).toHaveLength(64);
  });
});

describe("waitingText", () => {
  it("says what the job waits for", () => {
    expect(waitingText(job({ waiting_for: { kind: "review1", file: "/r1.txt" } }))).toBe(
      "Review 1 of 2 (the transcript)",
    );
    expect(waitingText(job({ waiting_for: { kind: "shorts", file: "/c.txt" } }))).toBe(
      "Pick the Shorts",
    );
  });
});

describe("uploadName", () => {
  it("cleans the name like run names and keeps the extension in lower case", () => {
    expect(uploadName("My Video (1).MP4")).toBe("My-Video-1.mp4");
    expect(uploadName("Пазар в София.mkv")).toBe("Пазар-в-София.mkv");
    expect(uploadName("market.en.mp4")).toBe("market.en.mp4");
    expect(uploadName("(((.mp4")).toBe("video.mp4");
  });
});

describe("approve", () => {
  it("sets the status line to approved, wherever it is", () => {
    expect(approve("status: pending\n# notes\n[1] BG: x")).toBe("status: approved\n# notes\n[1] BG: x");
    expect(approve("\nStatus: Pending\nrest")).toBe("\nstatus: approved\nrest");
  });

  it("adds one if the file has none", () => {
    expect(approve("[1] BG: x")).toBe("status: approved\n[1] BG: x");
  });
});

// as the engine writes it (src/localdub/shorts.py, write_candidates)
const CANDIDATES = `status: pending
# Shorts candidates, strongest first. Mark the ones to render with an x, like [x] (3 is a good number),
# set the first line to 'status: approved', save, and run 'localdub shorts market --lang dub' again.

[ ] 1   00:10-00:40 (30s)   sentences 3-9   "The Price is a Joke"
    hook:   a surprising price
    why:    stands alone
    screen: market stalls

[x] 2   01:05-01:47 (42s)   sentences 12-20   "Don't Touch the Outlets!"
    hook:   a warning
`;

describe("candidates", () => {
  it("lists the candidates with their marks, titles and details", () => {
    const found = candidates(CANDIDATES);

    expect(found.map((c) => [c.n, c.picked, c.title, c.when])).toEqual([
      [1, false, "The Price is a Joke", "00:10-00:40 (30s)"],
      [2, true, "Don't Touch the Outlets!", "01:05-01:47 (42s)"],
    ]);
    expect(found[0].details).toEqual(["hook:   a surprising price", "why:    stands alone", "screen: market stalls"]);
  });

  it("switches a candidate's mark and nothing else", () => {
    const [first, second] = candidates(CANDIDATES);
    const toggled = toggleCandidate(toggleCandidate(CANDIDATES, first.line), second.line);

    expect(candidates(toggled).map((c) => c.picked)).toEqual([true, false]);
    expect(toggled.replace("[x] 1", "[ ] 1").replace("[ ] 2", "[x] 2")).toBe(CANDIDATES);
  });
});

describe("fileUrl", () => {
  it("encodes the run's name and each part of the path", () => {
    expect(fileUrl("market", "shorts/dub/short-1-the price.mp4")).toBe(
      "/files/market/shorts/dub/short-1-the%20price.mp4",
    );
    expect(fileUrl("пазар", "пазар.en.mp4")).toBe(
      `/files/${encodeURIComponent("пазар")}/${encodeURIComponent("пазар.en.mp4")}`,
    );
  });
});

describe("a new channel's languages", () => {
  const channels: Channel[] = [
    { name: "friend", source: "bg", target: "en", source_name: "Bulgarian", target_name: "English", speaker: null, has_voice: true },
  ];
  const language = (code: string, dub: boolean, installed = true): Language => ({
    code,
    name: code.toUpperCase(),
    dub,
    dictionary: `${code}_XX`,
    dictionary_installed: installed,
    apt: `hunspell-${code}`,
  });

  it("are sent only for a channel that doesn't exist yet", () => {
    expect(languageFields(" friend ", channels, "de", "es", "woman")).toEqual({});
    expect(languageFields("market", channels, "de", "es", "")).toEqual({ source: "de", target: "es" });
    expect(languageFields("market", channels, "de", "es", "woman")).toEqual({ source: "de", target: "es", speaker: "woman" });
    expect(findChannel("friend", channels)?.target_name).toBe("English");
  });

  it("start with English as the dub language if the voice model speaks it", () => {
    expect(defaultTarget([language("bg", false), language("de", true), language("en", true)])).toBe("en");
    expect(defaultTarget([language("bg", false), language("de", true)])).toBe("de");
    expect(defaultTarget([])).toBe("");
  });

  it("say when a dictionary is missing and how to install it", () => {
    expect(dictionaryNote(language("de", true))).toBe("");
    expect(dictionaryNote(language("de", true, false))).toBe(" (dictionary missing: apt-get install hunspell-de)");
    expect(dictionaryNote({ ...language("xx", false, false), dictionary: null })).toBe(" (no spelling check)");
  });
});

describe("the voice picker", () => {
  const REVIEW = "status: pending\n# Voice sample: candidates…\n#   2. 00:40-00:52\nvoice: auto\n\n[3] 00:12   unknown: x\nBG: …\n";

  it("reads and sets review 1's voice line, and nothing else", () => {
    expect(voiceLine(REVIEW)).toBe("auto");
    const set = setVoiceLine(REVIEW, "2");
    expect(voiceLine(set)).toBe("2");
    expect(set.replace("voice: 2", "voice: auto")).toBe(REVIEW);
    expect(voiceLine("status: pending\n[1] BG: x\n")).toBeNull();
    expect(setVoiceLine("status: pending\n", "2")).toBe("status: pending\n");
  });

  it("spreads likeness scores over the bar and writes stretches as timecodes", () => {
    expect([0.7, 0.8, 0.9, 0.97, 1.2].map(likenessShare).map((x) => Math.round(x * 100))).toEqual([0, 0, 50, 85, 100]);
    expect(stretch(80.2, 97.6)).toBe("01:20–01:38");
  });
});
