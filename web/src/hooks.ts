// The page's live data and small pieces of state, as React hooks (C#: services the components
// subscribe to).

import { useCallback, useEffect, useState } from "react";

import {
  type Channel,
  type Download,
  followDownload,
  followJob,
  followStatus,
  type Job,
  listChannels,
  listRuns,
  type RunSummary,
  type Status,
} from "./api";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The server's current (or last) job, live. */
export function useJob(): Job | null {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => followJob(setJob), []);
  return job;
}

/** The models' and GPUs' status, live every couple of seconds. */
export function useStatus(): Status | null {
  const [status, setStatus] = useState<Status | null>(null);
  useEffect(() => followStatus(setStatus), []);
  return status;
}

/** The current (or last) download from a link, live. */
export function useDownload(): Download | null {
  const [download, setDownload] = useState<Download | null>(null);
  useEffect(() => followDownload(setDownload), []);
  return download;
}

/** The runs on disk, loaded again whenever the job starts, stops or changes stage. */
export function useRuns(job: Job | null): { runs: RunSummary[]; error: string | null; refresh: () => void } {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    listRuns()
      .then((list) => {
        setRuns(list);
        setError(null);
      })
      .catch((e) => setError(`Couldn't load the runs: ${message(e)}`));
  }, []);
  useEffect(refresh, [refresh, job?.run, job?.state, job?.done.length, job?.current]);
  return { runs, error, refresh };
}

/** The channels, loaded again when a job starts or ends (a first run makes one). */
export function useChannels(job: Job | null): { channels: Channel[]; refresh: () => void } {
  const [channels, setChannels] = useState<Channel[]>([]);
  const refresh = useCallback(() => {
    listChannels()
      .then(setChannels)
      .catch(() => setChannels([]));
  }, []);
  useEffect(refresh, [refresh, job?.run, job?.state]);
  return { channels, refresh };
}

/** Date.now(), again every `ms` while `active` (for clocks that tick). */
export function useNow(active: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [active, ms]);
  return now;
}

/** The screens, from the address's #: "#/", "#/new", "#/models", "#/review/market", "#/voices/market", "#/shorts/market". */
export type Route =
  | { page: "studio" }
  | { page: "new" }
  | { page: "models" }
  | { page: "review" | "voices" | "shorts"; run: string | null };

export function parseRoute(hash: string): Route {
  const [page, ...rest] = hash.replace(/^#\/?/, "").split("/");
  const run = rest.length ? decodeURIComponent(rest.join("/")) || null : null;
  if (page === "new") return { page: "new" };
  if (page === "models") return { page: "models" };
  if (page === "review" || page === "voices" || page === "shorts") return { page, run };
  return { page: "studio" };
}

export function routeHref(route: Route): string {
  if (route.page === "studio") return "#/";
  if (route.page === "new") return "#/new";
  if (route.page === "models") return "#/models";
  return `#/${route.page}${route.run ? `/${encodeURIComponent(route.run)}` : ""}`;
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const changed = () => {
      setRoute(parseRoute(window.location.hash));
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  return route;
}

export function go(route: Route): void {
  window.location.hash = routeHref(route);
}

export type Theme = "dark" | "light";

function storedTheme(): Theme | null {
  try {
    const t = localStorage.getItem("localdub.theme");
    return t === "dark" || t === "light" ? t : null;
  } catch {
    return null;
  }
}

/** Dark or light: the system's choice until one is picked here, then that one (remembered). */
export function useTheme(): [Theme, () => void] {
  const system = (): Theme => (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark");
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? system());
  useEffect(() => {
    if (storedTheme()) document.documentElement.dataset.theme = theme;
  }, [theme]);
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("localdub.theme", next);
    } catch {
      // no storage: it lasts until the page is reloaded
    }
    document.documentElement.dataset.theme = next;
    setTheme(next);
  };
  return [theme, toggle];
}
