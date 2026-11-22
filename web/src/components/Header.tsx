// The top bar: the name, the on-air lamp, the screens, and the AI box at a glance.

import type { Job, RunSummary, Status } from "../api";
import { type Route, routeHref, type Theme } from "../hooks";
import { MoonIcon, SunIcon } from "./common";

/** ON AIR while a run works, YOUR TURN while one waits for you, else OFF AIR. */
export function Lamp({ job, waiting }: { job: Job | null; waiting: boolean }) {
  const on = job?.state === "running";
  const wait = !on && (job?.state === "waiting" || waiting);
  return (
    <span className={`lamp${on ? " on" : wait ? " wait" : ""}`} role="status">
      <span className="dot" />
      {on ? "ON AIR" : wait ? "YOUR TURN" : "OFF AIR"}
    </span>
  );
}

/** Where the menu's per-run screens lead: the run that waits for it, else the most fitting one. */
export function targets(runs: RunSummary[], job: Job | null) {
  const waits = (kinds: string[]) => runs.filter((r) => r.waiting_for && kinds.includes(r.waiting_for.kind));
  const reviews = waits(["review1", "review2"]);
  const voices = waits(["voice"]);
  const shorts = waits(["shorts"]);
  const transcribed = runs.find((r) => r.name === job?.run && r.done.includes("spelling")) ?? runs.find((r) => r.done.includes("spelling"));
  return {
    review: { run: reviews[0]?.name ?? null, count: reviews.length },
    voices: { run: voices[0]?.name ?? transcribed?.name ?? null, count: voices.length },
    shorts: { run: shorts[0]?.name ?? runs.find((r) => r.finished)?.name ?? null, count: shorts.length },
  };
}

type HeaderProps = {
  job: Job | null;
  status: Status | null;
  route: Route;
  runs: RunSummary[];
  theme: Theme;
  onTheme: () => void;
};

export default function Header({ job, status, route, runs, theme, onTheme }: HeaderProps) {
  const t = targets(runs, job);
  const waiting = runs.some((r) => r.waiting_for);
  const items: { label: string; route: Route; count?: number }[] = [
    { label: "Studio", route: { page: "studio" } },
    { label: "New run", route: { page: "new" } },
    { label: "Review", route: { page: "review", run: t.review.run }, count: t.review.count },
    { label: "Voices", route: { page: "voices", run: t.voices.run }, count: t.voices.count },
    { label: "Shorts", route: { page: "shorts", run: t.shorts.run }, count: t.shorts.count },
    { label: "Models", route: { page: "models" } },
  ];
  const models = status?.models.length ?? 0;
  const gpus = status?.gpus.length ?? 0;
  return (
    <header className="topbar">
      <div className="brand">
        <a className="wordmark" href="#/">
          localdub
        </a>
        <Lamp job={job} waiting={waiting} />
      </div>
      <nav className="nav" aria-label="Screens">
        {items.map((item) => (
          <a key={item.label} href={routeHref(item.route)} aria-current={route.page === item.route.page ? "page" : undefined}>
            {item.label}
            {item.count ? (
              <span className="count" aria-label={`${item.count} waiting`}>
                {item.count}
              </span>
            ) : null}
          </a>
        ))}
      </nav>
      <div className="system">
        <span>
          {status?.error
            ? "models: no answer"
            : status
              ? `llama-swap · ${models} model${models === 1 ? "" : "s"} · ${gpus} GPU${gpus === 1 ? "" : "s"}`
              : "llama-swap · …"}
        </span>
        <button
          type="button"
          className="icon-btn"
          onClick={onTheme}
          aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}
          title={theme === "dark" ? "Light theme" : "Dark theme"}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
    </header>
  );
}
