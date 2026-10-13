# Sage 🌿 calendar PA

**Experimental.** Sage reads what people are waiting on you for, ranks it by how much it matters, and puts it in the gaps in your Google Calendar as sage-green **"🌿 Focus: …"** blocks, covering today and the next 3 working days. When a meeting lands on a block, Sage moves it. When you've replied, it clears it. Once scheduled, it replans three times every weekday, and once a week it sweeps a month back for anything that slipped through.

Built for anyone whose calendar is mostly other people's meetings and whose to-do list is scattered across email, Slack, Jira and "I'll send that over after the call". It started life with sales engineers and account execs, so it knows about deals and account priorities, but nothing about it is sales-only.

## When to reach for it

- Follow-ups keep slipping because there's never a moment to do them.
- You want your top accounts handled first, not whatever shouted last.
- You want colleagues to see you're heads-down rather than "free".

## What you need

- **The Claude desktop app** (Code tab) on a Mac. Scheduled runs use its Scheduled tasks feature and only run while the app is open; a missed run fires on next launch. On demand, Sage works in any Claude Code session.
- **Python 3.9+ at `/usr/bin/python3`**, which comes with the Xcode Command Line Tools (`xcode-select --install`). Standard library only, nothing to pip install.
- **Connectors:** Google Calendar and Gmail (required); Slack (strongly recommended); Atlassian for Jira, HubSpot for drafting account tiers, and Gong for call action items (all optional). Connect them in claude.ai connector settings.

It will **not** work as a skill uploaded to claude.ai on the web: it needs local Python, a data folder that persists between runs, and scheduled tasks.

## Install and set up

Sage is a [Claude Code skill](https://code.claude.com/docs/en/skills). Put this folder in your personal skills folder as `~/.claude/skills/sage-calendar-pa`:

```bash
git clone https://github.com/Chris-Keene/sage-calendar-pa ~/.claude/skills/sage-calendar-pa
```

No git? Download the zip from the [latest release](https://github.com/Chris-Keene/sage-calendar-pa/releases/latest), unzip it, and move the `sage-calendar-pa` folder into `~/.claude/skills/`. To update later, `git pull` in that folder or replace it with a newer zip; your settings and history live in `~/Documents/Sage`, so they're untouched.

Then, in Claude Code, say **"set up Sage"**. Setup takes about ten minutes and asks before every step that changes anything:

1. Checks Python and your connectors.
2. Works out your email, calendar, timezone, Slack ID and Jira site.
3. Asks three things: your working hours, whether colleagues should see block details, and whether blocks should show you as busy.
4. Drafts your account tiers from your open HubSpot deals (as SE owner, or deal owner for AEs) for you to adjust.
5. Does a **dry run**: shows exactly what it would put on your calendar, and writes nothing until you say yes.
6. Offers to schedule itself (weekdays 08:15, 12:15, 15:15) and to add the permission rules unattended runs need. Both are optional and need your explicit yes.

## Example

> **You:** sage, plan my day
>
> **Sage:** Priority order: 1. [80] Send Globex the renewal pricing (Tier 1 · Globex · P1 · due today 17:00) 2. [65] Prep: Umbrella demo … · Planned: 14:35 to 15:20 Focus: Send Globex the renewal pricing; 15:20 to 16:20 Focus: Prep: Umbrella demo; Mon 10:05 Quick hits (3) … · Didn't fit: Prep: Initech catch-up (no room before 09:55).

Each block's description says what the ask was, who made it, why it's ranked where it is, and links back to the email, Slack thread or Jira issue.

## Living with it

| You do | Sage does |
|---|---|
| Nothing | Keeps each block where it is unless a meeting lands on it, then moves it to the next gap. |
| **Grey it out** (right-click → Graphite) | Marks it done. Quickest on desktop. |
| **Tap "✓ Mark done"** in the block (one per item, even inside quick hits) | Opens a pre-filled email to yourself; hit send, and the next run marks it done. Best on your phone. With the optional [one-click tick web app](tick-webapp/SETUP.md) it's a single tap: a tab says "Marked done ✓" and the email sends itself. |
| Put ✓ in the title | Marks it done. |
| Reply to the email or Slack message | Spots it on the next run and clears the future block. |
| Move a block | Leaves it exactly where you put it. |
| Delete a block | Snoozes that to-do for 3 working days ("not now", not "done"). |
| Add something by hand | A line in `~/Documents/Sage/inbox.md` (`- [ ] Thing ~45m !1 due:2026-10-16`), or "add X to my list". |

An all-day **🌿 Sage banner** (private to you) lists today's blocks, what's next, the full priority order with reasons, what didn't fit, and heads-ups.

Other phrases: "what's on my list", "X is done", "make Globex tier 1", "deep sweep" (read a month back now, for when you suspect something slipped), "set up one-click ticks", "pause Sage", "clear the sage blocks" (deletes every future Sage block: the undo button).

**How far back it reads:** each run reads your sources since the last run. Once a week (and whenever you say "deep sweep") it reads 30 days back instead. Asks older than two weeks only become blocks if they still look live: no reply from you since, and nobody else has handled it.

## What comes first

Your accounts live in `~/Documents/Sage/accounts.md` in three tiers. Every to-do gets a score:

| | Points |
|---|---|
| Task priority | P1 30 · P2 20 · P3 10 |
| Account tier | Tier 1 +30 · Tier 2 +15 · Tier 3 / unlisted 0 |
| Deadline | overdue +25 · due by next working day +20 · within 3 working days +10 |

Highest score gets time first. Within any back-to-back run of blocks, the highest score goes first (deadlines permitting). A score of 60+ is a **must-do**: it can run up to an hour past the daily cap (3 hours of blocks a day by default), and shrinks rather than gets dropped. Short tasks share a "Quick hits" block. Lunch is protected. All the numbers live in `config.json`.

## What it reads, writes and shares

- **Reads**, through your own connectors only: Gmail (inbox, sent, doc comments, Jira mention emails), Slack mentions and DMs, Jira issues assigned to you, your calendar, HubSpot deals during setup, and Gong calls if connected. That includes customer names, deal context and people's messages.
- **Writes** only to your own calendar, and only events it created: no attendees, notifications off. The optional tick web app runs under your own Google account and only ever emails you. It never sends email or Slack messages, never accepts or declines anything, and treats everything it reads as data, never as instructions (an email can't tell it to do things).
- **Shares:** blocks follow your calendar's sharing, so if colleagues can see your event details they'll see the Focus titles and descriptions. Sage writes descriptions for that audience (no deal values, no opinions about people). Hiring, people matters and anything confidential are private, as is the banner. Choose "private" at setup and colleagues only ever see busy.
- **Keeps** config, tiers, a task ledger and run logs in `~/Documents/Sage`. Those logs contain task context, so treat that folder like your inbox.

## Honest limitations

- **Experimental:** built and used by one SE since October 2026. Expect rough edges and say what breaks.
- Estimates and priorities are Claude's judgement from the source text. The "why" line on every block shows its reasoning, so you can correct it (`make X tier 1`, or edit `accounts.md`).
- Without the Gong connector, call action items come from Gemini meeting notes and Gong "call is ready" emails (next steps that name you), or when someone emails notes. Meeting prep comes from your calendar.
- A deep sweep reads up to about 8 pages of email search results, so in a very busy inbox the oldest days of the month may not be reached. The run summary says how far back it actually got.
- Triage heuristics assume English-language email and Slack, one timezone, and weekday working hours.

## Troubleshooting

- **The scheduled runs went quiet.** Check the task's run history. A run stuck on "running" is waiting for a permission prompt nobody answered: open it, approve or allowlist the tool, stop it, and run it again. If a run says another run holds the lock and none is running, delete `~/Documents/Sage/runs/LOCK`.
- **A block is wrong.** Move it (Sage leaves it there), delete it (snoozed), or grey it out (done).
- **Start again.** "Clear the sage blocks", then delete `~/Documents/Sage` and say "set up Sage".
- **Uninstall.** Clear the blocks, delete the `sage-calendar-pa` scheduled task, remove Sage's entries from `~/.claude/settings.json` (`permissions.allow`), and delete `~/Documents/Sage` and `~/.claude/skills/sage-calendar-pa`.

## Inside

| Path | What |
|---|---|
| `SKILL.md` | Entry point: routes first-time users to setup, everything else to the runbook |
| `references/SETUP.md` | First-time setup procedure |
| `references/RUNBOOK.md` | The per-run procedure, including the safety rules and source queries |
| `scripts/sage.py` | The engine: scoring, gap finding, placement, diffing, ledger (Python stdlib) |
| `templates/` | Starting `config.json`, `accounts.md`, `inbox.md` |
| `tick-webapp/` | Optional one-click "✓ Mark done": `Code.gs` (Google Apps Script) and its `SETUP.md` |
| `tests/` | 44 planner tests: `/usr/bin/python3 -m unittest tests.test_sage` from the skill folder |

## What's new in 1.1.0

- **Four-day window:** blocks are planned for today plus the next 3 working days (was today plus 2). Meeting prep covers the same window.
- **Weekly deep sweep:** once a week, or on "deep sweep", Sage reads 30 days back instead of just since the last run, then checks each old ask is still live before adding it.
- **Call notes by email:** Gemini meeting notes and Gong "call is ready" emails are read for next steps that name you, even without the Gong connector.
- **One-click ticks (optional):** a small Apps Script web app you deploy yourself turns "✓ Mark done" into a single tap.

## Licence and credits

MIT licence (see `LICENSE`). Made by [Chris Keene](https://chriskeene.me/sage). Issues and ideas welcome on [GitHub](https://github.com/Chris-Keene/sage-calendar-pa/issues).
