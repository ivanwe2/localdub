import { describe, expect, it } from "vitest";

import { fileUrl, type Job } from "./api";
import {
  approve,
  candidates,
  formatSeconds,
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
