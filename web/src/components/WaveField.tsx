// The background: thin sound-wave lines behind the top of the page. They speak (move and swell)
// while a run is on air, breathe while one waits for you, and lie flat when nothing runs. Pure
// CSS animation, so nothing redraws in JavaScript; still under "reduce motion".

import { sinePath } from "../studio";

export type WaveMode = "speaking" | "calm" | "still";

const LINES = [
  { cls: "d1", top: 70, opacity: 0.55, length: 360, amp: 26, width: 1.4 },
  { cls: "d2", top: 110, opacity: 0.35, length: 240, amp: 18, width: 1.2 },
  { cls: "d3", top: 40, opacity: 0.25, length: 480, amp: 34, width: 1 },
  { cls: "d4", top: 150, opacity: 0.2, length: 288, amp: 14, width: 1 },
];

const PATHS = LINES.map((l) => sinePath(l.length, l.amp));

export default function WaveField({ mode }: { mode: WaveMode }) {
  return (
    <div className={`waves ${mode}`} aria-hidden="true">
      {LINES.map((l, i) => (
        <div key={l.cls} className={`drift ${l.cls}`} style={{ top: l.top, opacity: l.opacity }}>
          <svg viewBox="0 0 2880 200" preserveAspectRatio="none">
            <path
              className="wave-line"
              d={PATHS[i]}
              fill="none"
              stroke="currentColor"
              strokeWidth={l.width}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        </div>
      ))}
    </div>
  );
}
