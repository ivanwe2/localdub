// The README's screenshots in headless Chromium: node shots.mjs <base url> <images dir> <work dir>
// First the screens while nothing is on air, then it starts the demo run and records the studio.
import { execFileSync } from "node:child_process";
import { chromium } from "playwright";

const [base, out, work] = process.argv.slice(2);
const browser = await chromium.launch();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shot(name, hash, { theme = "dark", width = 1440, height = 900, full = true, wait = 1500, act } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${base}/${hash}`);
  if (act) await act(page);
  await sleep(wait);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: full });
  console.log(name, errors.length ? `errors: ${errors.join(" | ")}` : "ok");
  await context.close();
}

await shot("new-run", "#/new");
await shot("review", "#/review/village-bakery");
await shot("voices", "#/voices/market-day-ep12");
await shot("shorts", "#/shorts/cheese-tour", { wait: 2500 });
await shot("models", "#/models", { wait: 2000, full: false, height: 1000 });

// on air: the studio recorded while the demo engine voices its lines (one every 0.7 s)
const size = { width: 1440, height: 680 };
const context = await browser.newContext({ viewport: size, colorScheme: "dark", recordVideo: { dir: work, size } });
const page = await context.newPage();
const opened = Date.now();
await page.goto(`${base}/#/`);
await sleep(1200);
await page.evaluate(() => fetch("/api/runs/market-day-ep12/continue", { method: "POST" }));
const started = (Date.now() - opened) / 1000;
await sleep(5600);
await shot("studio", "#/", { height: 1240, full: false, wait: 1200 });
await shot("studio-light", "#/", { theme: "light", height: 1240, full: false, wait: 1200 });
await shot("phone", "#/", { width: 390, height: 844, full: false, wait: 1200 });
await sleep(1000);
await context.close();
const webm = await page.video().path();
await browser.close();

// the GIF: the run on air, its lines filling in; cropped to the content column, 900 px wide
execFileSync("ffmpeg", ["-v", "error", "-y", "-ss", String(started + 0.6), "-t", "9", "-i", webm, "-vf",
  "crop=1240:612:100:22,fps=10,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];" +
  "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", `${out}/studio.gif`]);
console.log("studio.gif ok");
