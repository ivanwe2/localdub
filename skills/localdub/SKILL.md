---
name: localdub
description: Dub a video into another language in the creator's own cloned voice, and cut vertical Shorts from it, with the localdub command-line tool (runs on local GPUs). Use when asked to dub or translate a video's speech, make Shorts or Reels from a video, approve or edit localdub's transcript, translation or Shorts reviews, pick or change a channel's voice, or choose which models localdub uses.
---

# localdub

localdub is a command-line tool: `localdub run VIDEO --channel NAME --json` dubs a video and stops
(exit code 3) whenever a person has to check something; `localdub run NAME --json` continues.

Before anything else, read the full guide. It has the loop, every command, the formats of the files
a run waits on, and the events:

```
localdub guide
```

The essentials:

- Run `localdub check --json` first; fix what fails.
- A new channel needs `--from LANG --to LANG` (`localdub languages --json` lists them); for Shorts
  without a dub use `--shorts-only` and only `--from`.
- Exit codes: `0` done, `3` waiting for a review or a pick (`result.waiting_for.file` says which
  file; edit it, set `status: approved`, continue), `1` error (`result.error`; fix, then continue).
- One run at a time. Never put an API key on a command line (`localdub models --server NAME
  --key-stdin` reads it from stdin).
- Only clone voices of people who agreed, and only process videos the person has the rights to.

To install this skill for Claude Code: `cp -r skills/localdub ~/.claude/skills/` (other agents can
read this file and `localdub guide` the same way).
