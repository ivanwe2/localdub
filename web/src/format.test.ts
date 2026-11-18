import { describe, expect, it } from "vitest";

import { fileUrl, type Job } from "./api";
import { formatSeconds, progress, suggestName, waitingText } from "./format";

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
