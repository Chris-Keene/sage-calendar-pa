#!/usr/bin/env python3
"""Sage: a calendar PA that turns open loops into sage-green to-do blocks.

This is the deterministic half. Claude reads the sources (Gmail, Slack, Jira,
calls, the calendar itself) and writes tasks.json; this script does the slot
maths, diffs against the blocks it placed last time, and emits the exact
calendar operations to run. Keeping placement in code means times are computed,
never guessed, and a re-run with nothing new produces zero changes.

Your data (config, accounts, ledger, runs) lives in a data folder, by default
~/Documents/Sage; pass --home before the command, or set SAGE_HOME, to use another.

Commands:
  init [--email ...]        create or update the data folder and config (first-time setup)
  begin                     start a run: prints the window and open tasks to re-check
  slim-events RAW [RAW...]  turn saved list_events output into runs/<id>/events.json
  plan                      read events.json + tasks.json + inbox.md, write plan.json
  commit RESULTS            record which ops succeeded and update the ledger
  status                    show open tasks and placed blocks
  done QUERY                mark a task done by key or title text
  clear                     plan deletion of every future Sage block (the undo button)
  peek FILE                 compact view of a big saved tool result (Gmail, calendar, Slack)
"""
import argparse
import copy
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

VERSION = "1.1.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_HOME = Path.home() / "Documents" / "Sage"


def set_home(path=None):
    """Data lives outside the installed skill, so reinstalling or updating the
    skill never touches anyone's config, priorities or ledger."""
    global HOME, RUNS, STATE_PATH, INBOX_PATH, ACCOUNTS_PATH, LOCK_PATH
    HOME = Path(path or os.environ.get("SAGE_HOME") or DEFAULT_HOME).expanduser().resolve()
    RUNS = HOME / "runs"
    STATE_PATH = HOME / "state.json"
    INBOX_PATH = HOME / "inbox.md"
    ACCOUNTS_PATH = HOME / "accounts.md"
    LOCK_PATH = RUNS / "LOCK"


set_home()
DONE_SUBJECT = "Sage done: "

# Every block Sage writes carries this line, so a lost ledger or a crashed run
# can be recovered by re-adopting blocks from the calendar itself.
MARKER_RE = re.compile(r"sage:v1 kind=(\w+) keys=([^\s<\"]*)")
SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9:_./#@-]")

DEFAULT_CONFIG = {
    "calendar_id": "primary",
    "timezone": "Europe/London",
    "internal_domains": [],
    "slack_user_id": "",
    "slack_workspace_url": "",
    "jira_cloud_id": "",
    "jira_site_url": "",
    "work_start": "09:00",
    "work_end": "17:30",
    "work_days": [0, 1, 2, 3, 4],
    "skip_dates": [],
    "horizon_working_days": 4,
    "deep_lookback_days": 30,
    "deep_sweep_every_days": 7,
    "incremental_lookback_days": 3,
    "lead_minutes": 10,
    "buffer_after_meetings": 5,
    "buffer_before_meetings": 0,
    "min_block": 25,
    "max_block": 90,
    "quick_task_max": 15,
    "batch_minutes": 30,
    "batch_max_items": 4,
    "lunch": {"window_start": "12:00", "window_end": "14:00", "minutes": 30, "ideal_start": "12:30"},
    "daily_cap_minutes": 180,
    "daily_cap_blocks": 6,
    "needs_action_blocks": True,
    "busy_policy": "urgent",
    "colour_id": "2",
    "done_colour_id": "8",
    "done_by_colour": True,
    "title_prefix": "\U0001F33F Focus: ",
    "banner_prefix": "\U0001F33F ",
    "visibility": "default",
    "banner_visibility": "private",
    "my_email": "",
    "mail_ticks": True,
    "done_url": "",
    "reminder_minutes": 0,
    "priority_points": {"1": 30, "2": 20, "3": 10},
    "tier_points": {"1": 30, "2": 15, "3": 0},
    "default_tier": 3,
    "urgency_points": {"overdue": 25, "next_working_day": 20, "three_working_days": 10},
    "cap_override_score": 60,
    "cap_override_minutes": 60,
    "snooze_working_days": 3,
    "stale_days": 10,
    "roll_warning": 3,
    "banner": True,
    "link_hosts": [
        "mail.google.com", "slack.com", "atlassian.net", "hubspot.com", "docs.google.com",
        "drive.google.com", "calendar.google.com", "gong.io", "zoom.us", "notion.so", "github.com",
    ],
}

EMPTY_STATE = {"version": 1, "last_run": None, "tasks": {}, "blocks": {}, "banner_dismissed": []}


# ---------------------------------------------------------------- utilities

def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        if default is not None:
            return copy.deepcopy(default)
        raise


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def load_config(path=None):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = Path(path) if path else HOME / "config.json"
    if p.exists():
        for k, v in json.loads(p.read_text()).items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def parse_dt(value, tz):
    """ISO string -> aware datetime in tz. Date-only strings become local midnight."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(tz)
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return dt.datetime.combine(dt.date.fromisoformat(s), dt.time(0), tz)
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.astimezone(tz)


def iso(d, tz):
    return d.astimezone(tz).isoformat(timespec="seconds")


def hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def at(day, t, tz):
    return dt.datetime.combine(day, t, tz)


def mins(td):
    return int(td.total_seconds() // 60)


def minutes(n):
    return dt.timedelta(minutes=n)


def ceil5(d):
    base = d.replace(second=0, microsecond=0)
    if base < d:
        base += minutes(1)
    r = base.minute % 5
    return base if r == 0 else base + minutes(5 - r)


def floor5(d):
    d = d.replace(second=0, microsecond=0)
    return d - minutes(d.minute % 5)


def subtract(intervals, cuts):
    out = []
    for s, e in intervals:
        pieces = [(s, e)]
        for cs, ce in cuts:
            nxt = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:
                    nxt.append((ps, pe))
                    continue
                if cs > ps:
                    nxt.append((ps, cs))
                if ce < pe:
                    nxt.append((ce, pe))
            pieces = nxt
        out.extend(pieces)
    return sorted(out)


def covers(intervals, s, e):
    return any(a <= s and e <= b for a, b in intervals)


def fmt_day(d):
    return f"{d:%a} {d.day} {d:%b}"


def fmt_when(d):
    return f"{fmt_day(d)} {d:%H:%M}"


def fmt_span(s, e):
    return f"{s:%H:%M}–{e:%H:%M}"


def short(text, n):
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def esc(text):
    return html.escape(str(text), quote=True)


def is_done_title(title):
    return bool(re.search("[✓✔✅]", title or "")) or bool(
        re.match(r"^\s*(\U0001F33F\s*)?(done\b|\[x\])", title or "", re.IGNORECASE))


def same_title(a, b):
    """Treat a truncated copy of a title as the same title. A hand-copied events.json
    once shortened a quick-hits title and Sage mistook it for the user renaming the block."""
    a = re.sub(r"\s+", " ", a or "").strip().rstrip("…").strip()
    b = re.sub(r"\s+", " ", b or "").strip().rstrip("…").strip()
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 20 and longer.startswith(shorter)


def working_days_after(day, n, cfg):
    d, out = day, []
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() in cfg["work_days"] and d.isoformat() not in cfg["skip_dates"]:
            out.append(d)
    return out


def previous_working_day(day, cfg):
    d = day - dt.timedelta(days=1)
    while d.weekday() not in cfg["work_days"] or d.isoformat() in cfg["skip_dates"]:
        d -= dt.timedelta(days=1)
    return d


def planning_days(now, cfg, tz):
    days = []
    today = now.date()
    if (today.weekday() in cfg["work_days"] and today.isoformat() not in cfg["skip_dates"]
            and now < at(today, hm(cfg["work_end"]), tz)):
        days.append(today)
    days += working_days_after(today, cfg["horizon_working_days"] - len(days), cfg)
    return days


# ---------------------------------------------------------------- events

def _norm_type(t):
    return re.sub(r"[^a-z]", "", str(t or "default").lower())


def slim_event(raw, cfg):
    if raw.get("status") == "cancelled":
        return None
    start, end = raw.get("start") or {}, raw.get("end") or {}
    all_day = "dateTime" not in start
    attendees = raw.get("attendees") or []
    me = next((a for a in attendees if a.get("self")), None)
    organizer_self = bool((raw.get("organizer") or {}).get("self"))
    if me:
        resp = me.get("responseStatus", "needsAction")
    elif attendees and not organizer_self:
        resp = "needsAction"
    elif attendees:
        resp = "accepted"
    else:
        resp = "none"
    internal = tuple(d.lower() for d in cfg["internal_domains"])
    external = any(
        not a.get("resource") and "@" in a.get("email", "")
        and not a["email"].lower().endswith("resource.calendar.google.com")
        and internal and not a["email"].lower().endswith(internal)
        for a in attendees)
    m = MARKER_RE.search(raw.get("description") or "")
    return {
        "id": raw["id"],
        "summary": raw.get("summary", ""),
        # All-day dates come back as "2026-10-08T00:00:00Z"; keep just the date so it
        # means local midnight rather than 01:00 BST.
        "start": start.get("dateTime") or (start.get("date") or "")[:10],
        "end": end.get("dateTime") or (end.get("date") or "")[:10],
        "all_day": all_day,
        "transparent": raw.get("transparency") == "transparent",
        "self_response": resp,
        "event_type": _norm_type(raw.get("eventType")),
        "external": external,
        "organizer_self": organizer_self,
        "attendee_count": len(attendees),
        "color_id": raw.get("colorId"),
        "sage_kind": m.group(1) if m else None,
        "sage_keys": [k for k in m.group(2).split(",") if k] if m else [],
    }


def unwrap_raw(data):
    """Accept list_events output however it was saved: dict, list, or text-wrapped."""
    if isinstance(data, list) and data and isinstance(data[0], dict) and "text" in data[0]:
        out = []
        for part in data:
            out += unwrap_raw(json.loads(part["text"]))
        return out
    if isinstance(data, dict):
        return data.get("events") or data.get("items") or []
    if isinstance(data, list):
        return data
    return []


# ---------------------------------------------------------------- inbox

INBOX_LINE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")


def parse_inbox(text):
    """Manual capture: '- [ ] Draft Q4 plan ~60m !1 due:2026-10-10'. Tick [x] to finish."""
    tasks = []
    for line in text.splitlines():
        m = INBOX_LINE.match(line)
        if not m:
            continue
        body = m.group(2)
        mins_m = re.search(r"~(\d+)\s*m\b", body)
        prio_m = re.search(r"(?<!\S)!([123])\b", body)
        due_m = re.search(r"\bdue:(\d{4}-\d{2}-\d{2})\b", body)
        title = body
        for mm in (mins_m, prio_m, due_m):
            if mm:
                title = title.replace(mm.group(0), " ")
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        tasks.append({
            "key": "inbox:" + hashlib.sha1(title.lower().encode()).hexdigest()[:10],
            "title": short(title, 70),
            "context": "From your Sage inbox (inbox.md)",
            "minutes": int(mins_m.group(1)) if mins_m else 30,
            "priority": int(prio_m.group(1)) if prio_m else 2,
            "due": due_m.group(1) if due_m else None,
            "source": "inbox",
            "status": "done" if m.group(1).lower() == "x" else "open",
        })
    return tasks


# ---------------------------------------------------------------- accounts

TIER_HEAD = re.compile(r"^\s*#+\s*tier\s*([123])\b", re.IGNORECASE)
ACCOUNT_LINE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


def parse_accounts(text):
    """accounts.md: '## Tier 1' headings, then '- Name | alias, alias | why it matters' lines."""
    accounts, tier = [], None
    for line in text.splitlines():
        head = TIER_HEAD.match(line)
        if head:
            tier = int(head.group(1))
            continue
        m = ACCOUNT_LINE.match(line)
        if not m or tier is None:
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        if not parts[0]:
            continue
        aliases = [a.strip() for a in (parts[1].split(",") if len(parts) > 1 else []) if a.strip()]
        accounts.append({"name": parts[0], "tier": tier, "aliases": aliases,
                         "why": parts[2] if len(parts) > 2 else ""})
    return accounts


def _alias_hit(alias, text):
    # Short aliases are case-sensitive so "BA" doesn't match "bank" or "ba" inside words.
    flags = 0 if len(alias) <= 3 else re.IGNORECASE
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", text, flags) is not None


def resolve_account(task, accounts, cfg):
    """The account a task belongs to. Claude names it in tasks.json; failing that, a
    listed name or alias in the title or context decides. Unlisted = default tier."""
    named = (task.get("account") or "").strip()
    if named:
        for a in accounts:
            if named.lower() == a["name"].lower() or named.lower() in (x.lower() for x in a["aliases"]):
                return a
    text = named or f"{task.get('title', '')} {task.get('context', '')}"
    for a in sorted(accounts, key=lambda a: a["tier"]):
        if any(_alias_hit(x, text) for x in [a["name"], *a["aliases"]]):
            return a
    return {"name": named or None, "tier": cfg["default_tier"], "aliases": [], "why": ""}


# ---------------------------------------------------------------- planner

class Unit:
    """One thing to place on the calendar: a single task or a batch of quick ones."""

    def __init__(self, kind, keys, priority, deadline, not_before, dur, min_dur, order, score=0, must=False):
        self.kind, self.keys, self.priority = kind, keys, priority
        self.deadline, self.not_before = deadline, not_before
        self.dur, self.min_dur, self.order = dur, min_dur, order
        # Must-dos (high score) may run past the daily cap rather than slip a day.
        self.score, self.must = score, must


def quantize(m, cfg):
    # A 10-minute task gets the smallest usable block, not 30: on a packed day those
    # spare minutes are what squeezes out something important.
    if m <= cfg["quick_task_max"]:
        return cfg["min_block"]
    for q in (30, 45, 60, 90):
        if m <= q:
            return min(q, cfg["max_block"])
    return cfg["max_block"]


def safe_links(task, cfg):
    out = []
    for link in task.get("links") or []:
        url = str(link.get("url", ""))
        host = (urlparse(url).hostname or "").lower()
        # Links come from email/Slack content, so only known work hosts make it in.
        if url.startswith("https://") and any(host == h or host.endswith("." + h) for h in cfg["link_hosts"]):
            out.append((short(link.get("label") or host, 40), url))
    return out


def done_link(key, cfg, title=""):
    """The tick link. With done_url set it opens your own Apps Script web app
    (tick-webapp/), which sends the 'Sage done' mail for you: one click, no mail client.
    Otherwise it's a mailto you send yourself. Either way only mail *from* you counts,
    so a colleague clicking it changes nothing."""
    if not (cfg.get("mail_ticks") and cfg.get("my_email")):
        return ""
    url = (cfg.get("done_url") or "").strip()
    # Only an Apps Script deployment is trusted here; anything else falls back to mailto.
    if url.startswith("https://script.google.com/"):
        q = "k=" + quote(key, safe="") + ("&amp;t=" + quote(short(title, 80), safe="") if title else "")
        return f'<a href="{esc(url)}?{q}">✓ Mark done</a>'
    subject = quote(DONE_SUBJECT + key, safe="")
    body = quote("Just hit send. Sage clears this from your calendar on its next run.", safe="")
    return f'<a href="mailto:{esc(cfg["my_email"])}?subject={subject}&amp;body={body}">✓ Mark done</a>'


def task_html(t, cfg, tz, key=None, why=None, heading=True):
    parts = []
    if heading:
        parts.append(f"<b>{esc(t['title'])}</b>")
    if t.get("context"):
        parts.append(esc(short(t["context"], 300)))
    meta = [f"Why it's here: {esc(why)}" if why else f"Priority {t.get('priority', 2)}"]
    if t.get("prep_for"):
        meta.append(f"prep for {esc(short(t['prep_for'].get('title', 'meeting'), 60))} at "
                    f"{fmt_when(parse_dt(t['prep_for']['start'], tz))}")
    elif t.get("due") and not why:   # the why line already carries the deadline
        meta.append(f"due {fmt_when(due_dt(t, cfg, tz))}")
    meta.append(f"from {esc(t.get('source', '?'))}")
    parts.append(" · ".join(meta))
    links = [f'<a href="{esc(u)}">{esc(lbl)}</a>' for lbl, u in safe_links(t, cfg)]
    tick = done_link(key, cfg, t.get("title", "")) if key else ""
    if tick:
        links.append(tick)
    if links:
        parts.append(" · ".join(links))
    return "<br>".join(parts)


FOOTER = ("<i>Done? Change the block's colour to Graphite (grey), tap ✓ Mark done, or put ✓ in the title. "
          "Not now? Delete it and Sage snoozes it for {snooze} working days. "
          "Moved it? Sage leaves it where you put it.</i>")


def block_html(kind, tasks, keys, cfg, tz, why=None):
    why = why or {}
    if kind == "task" or len(keys) == 1:
        body = task_html(tasks[keys[0]], cfg, tz, keys[0], why.get(keys[0]))
    else:
        items = "".join(f"<li>{task_html(tasks[k], cfg, tz, k, why.get(k))}</li>" for k in keys)
        body = f"<b>Quick hits</b>: knock these out in one go.<ol>{items}</ol>"
    footer = FOOTER.format(snooze=cfg["snooze_working_days"])
    return f"{body}<br><br>{footer}<br><br><small>sage:v1 kind={kind} keys={','.join(keys)}</small>"


def block_title(kind, tasks, keys, cfg):
    if kind == "task" or len(keys) == 1:
        return cfg["title_prefix"] + short(tasks[keys[0]]["title"], 80)
    names = "; ".join(short(tasks[k]["title"], 32) for k in keys)
    return cfg["title_prefix"] + short(f"Quick hits ({len(keys)}): {names}", 90)


def due_dt(t, cfg, tz):
    if not t.get("due"):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t["due"])):
        return at(dt.date.fromisoformat(t["due"]), hm(cfg["work_end"]), tz)
    return parse_dt(t["due"], tz)


def digest(text):
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def merge_tasks(tasks, incoming, now, cfg, tz, notes):
    seen = set()
    inbox_keys = set()
    for t in incoming:
        key = SAFE_KEY_RE.sub("_", str(t.get("key", "")).strip())
        if not key or not t.get("title"):
            notes.append(f"Skipped a task with no key or title: {short(json.dumps(t), 80)}")
            continue
        seen.add(key)
        if t.get("source") == "inbox":
            inbox_keys.add(key)
        cur = tasks.get(key)
        if cur is None:
            cur = tasks[key] = {"title": short(t["title"], 80), "first_seen": iso(now, tz),
                                "rolls": 0, "status": "open"}
        # The first title sticks, so blocks don't get retitled every run.
        for f in ("context", "due", "not_before", "prep_for", "links", "source", "account", "private"):
            if f in t:
                cur[f] = t[f]
        cur["minutes"] = max(5, min(240, int(t.get("minutes") or cur.get("minutes") or 30)))
        cur["priority"] = max(1, min(3, int(t.get("priority") or cur.get("priority") or 2)))
        cur["last_seen"] = iso(now, tz)
        cur.pop("unverified", None)
        if t.get("status") == "done" and cur["status"] != "done":
            cur.update(status="done", done_at=iso(now, tz),
                       done_reason=short(t.get("done_reason") or "source shows it handled", 120))
        elif t.get("status", "open") == "open" and cur["status"] == "stale":
            cur["status"] = "open"
    for key, cur in tasks.items():
        if cur["status"] == "snoozed" and parse_dt(cur.get("snoozed_until"), tz) <= now:
            cur["status"] = "open"
        if cur["status"] != "open" or key in seen:
            continue
        if cur.get("source") == "inbox" and key not in inbox_keys:
            cur.update(status="done", done_at=iso(now, tz), done_reason="removed from inbox.md")
            continue
        cur["unverified"] = True
        last = parse_dt(cur.get("last_seen") or cur["first_seen"], tz)
        if (now - last).days >= cfg["stale_days"]:
            cur.update(status="stale", done_at=iso(now, tz),
                       done_reason=f"not re-confirmed for {cfg['stale_days']} days")
    for cur in tasks.values():
        prep = cur.get("prep_for")
        if cur["status"] == "open" and prep and parse_dt(prep.get("start"), tz) <= now:
            cur.update(status="stale", done_at=iso(now, tz), done_reason="the meeting it preps for has started")


def snooze(task, now, cfg, tz):
    until = working_days_after(now.date(), cfg["snooze_working_days"], cfg)[-1]
    task.update(status="snoozed", snoozed_until=iso(at(until, hm(cfg["work_start"]), tz), tz))
    return until


def make_plan(cfg, state, events_doc, tasks_doc, inbox_tasks, now, accounts=None):
    tz = ZoneInfo(cfg["timezone"])
    now = now.astimezone(tz)
    ws = parse_dt(events_doc["window_start"], tz)
    we = parse_dt(events_doc["window_end"], tz)
    lead = now + minutes(cfg["lead_minutes"])
    events = events_doc["events"]
    by_id = {e["id"]: e for e in events}
    before = copy.deepcopy(state)
    st = copy.deepcopy(state)
    st.setdefault("banner_dismissed", [])
    tasks, blocks = st["tasks"], st["blocks"]
    notes, heads_up, ops = [], [], []
    days = planning_days(now, cfg, tz)
    today = now.date()

    def add_op(op, describe, **kw):
        kw.update(op=op, op_id=f"op{len(ops) + 1}", describe=describe)
        ops.append(kw)
        return kw["op_id"]

    def cal_args(**kw):
        return {"calendarId": cfg["calendar_id"], **kw, "notificationLevel": "NONE"}

    merge_tasks(tasks, list(tasks_doc.get("tasks", [])) + inbox_tasks, now, cfg, tz, notes)

    # Recover Sage blocks the ledger doesn't know about (crashed run, lost state.json).
    for e in events:
        e.setdefault("summary", "")
        if not e.get("sage_kind") or e["id"] in blocks:
            continue
        if e["sage_kind"] == "banner":
            blocks[e["id"]] = {"kind": "banner", "date": parse_dt(e["start"], tz).date().isoformat(),
                               "task_keys": [], "start": e["start"], "end": e["end"], "title": e["summary"],
                               "desc_hash": ""}
            continue
        known = [k for k in e.get("sage_keys", []) if k in tasks]
        blocks[e["id"]] = {"kind": e["sage_kind"], "task_keys": known, "start": e["start"],
                           "end": e["end"], "title": e["summary"], "adopted": True,
                           "pinned": not known, "desc_hash": "", "availability": "?"}
        notes.append(f"Re-adopted '{short(e['summary'], 50)}' from the calendar.")

    # ---- what happened to the blocks Sage placed last time?
    occupied = []          # Sage blocks this run won't move
    movable = {}           # event id -> (start, end)
    for eid, blk in list(blocks.items()):
        bs, be = parse_dt(blk["start"], tz), parse_dt(blk["end"], tz)
        ev = by_id.get(eid)
        if blk["kind"] == "banner":
            if ev is None and ws <= bs < we:
                del blocks[eid]
                if blk.get("date") == today.isoformat() and blk.get("date") not in st["banner_dismissed"]:
                    st["banner_dismissed"].append(blk["date"])
            continue
        if ev is None:
            if not (ws <= bs and be <= we):
                continue  # outside the window we looked at, so we know nothing new
            del blocks[eid]
            open_keys = [k for k in blk["task_keys"] if tasks.get(k, {}).get("status") == "open"]
            if be > now and open_keys:
                for k in open_keys:
                    until = snooze(tasks[k], now, cfg, tz)
                notes.append(f"You deleted '{short(blk['title'], 50)}', so it's snoozed until {fmt_day(until)}.")
            continue
        live_s, live_e = parse_dt(ev["start"], tz), parse_dt(ev["end"], tz)
        ticked = is_done_title(ev["summary"])
        greyed = cfg["done_by_colour"] and ev.get("color_id") == cfg["done_colour_id"]
        if (ticked or greyed) and not blk.get("done_marked"):
            # You touched it, so it stays as a record rather than vanishing.
            blk["pinned"] = True
            reason = "ticked ✓ in the calendar" if ticked else "greyed out in the calendar"
            for k in blk["task_keys"]:
                if k in tasks and tasks[k]["status"] != "done":
                    tasks[k].update(status="done", done_at=iso(now, tz), done_reason=reason)
        if not same_title(ev["summary"], blk["title"]):
            if not ticked and not blk.get("title_pinned"):
                blk["title_pinned"] = True
                notes.append(f"You renamed a block to '{short(ev['summary'], 50)}'; Sage will keep your title.")
            blk["title"] = ev["summary"]
        if (live_s, live_e) != (bs, be):
            if not blk.get("pinned"):
                notes.append(f"You moved '{short(blk['title'], 50)}' to {fmt_when(live_s)}; Sage will leave it there.")
            blk["pinned"] = True
            blk["start"], blk["end"] = iso(live_s, tz), iso(live_e, tz)
            bs, be = live_s, live_e
        keys_open = [k for k in blk["task_keys"] if tasks.get(k, {}).get("status") == "open"]
        all_done = blk["task_keys"] and all(tasks.get(k, {}).get("status") == "done" for k in blk["task_keys"])
        if be <= now:
            if keys_open and not blk.get("rolled"):
                for k in keys_open:
                    tasks[k]["rolls"] = tasks[k].get("rolls", 0) + 1
                blk["rolled"] = True
        # Blocks you placed or already sat through turn grey when their work is done;
        # unpinned future ones are simply deleted further down to free the time.
        if (be <= now or bs < lead or blk.get("pinned")) and all_done and not blk.get("done_marked"):
            args = cal_args(eventId=eid, colorId=cfg["done_colour_id"], availability="AVAILABILITY_FREE")
            if not is_done_title(ev["summary"]):
                args["summary"] = "✓ " + ev["summary"]
                blk["title"] = args["summary"]
            add_op("update", f"Mark done: {short(ev['summary'], 60)}", event_id=eid, args=args)
            blk.update(done_marked=True, availability="AVAILABILITY_FREE")
        if be <= now:
            continue
        if bs < lead or blk.get("pinned") or not blk["task_keys"]:
            occupied.append((bs, be))
            continue
        movable[eid] = (bs, be)

    fixed_ids = [eid for eid, b in blocks.items() if b["kind"] != "banner" and eid not in movable
                 and parse_dt(b["end"], tz) > now]

    # ---- busy time from everyone else's events
    busy, meetings = [], []
    sage_ids = set(blocks) | {e["id"] for e in events if e.get("sage_kind")}
    after_buf, before_buf = minutes(cfg["buffer_after_meetings"]), minutes(cfg["buffer_before_meetings"])
    for e in events:
        if e["id"] in sage_ids:
            continue
        s, en = parse_dt(e["start"], tz), parse_dt(e["end"], tz)
        if e.get("all_day"):
            if e.get("event_type") == "outofoffice":
                busy.append((s, en))
            continue
        if e.get("event_type") in ("workinglocation", "birthday"):
            continue
        r = e.get("self_response", "none")
        if r == "declined" or (e.get("transparent") and r != "accepted"):
            continue
        if r == "needsAction" and not cfg["needs_action_blocks"]:
            continue
        busy.append((s - before_buf, en + after_buf))
        meetings.append((s, en))

    # ---- free time per planning day, with lunch protected
    free, lunch_at = {}, {}
    L = cfg["lunch"]
    for day in days:
        w0 = max(at(day, hm(cfg["work_start"]), tz), ceil5(lead))
        w1 = at(day, hm(cfg["work_end"]), tz)
        if w0 >= w1:
            free[day] = []
            continue
        f = [(ceil5(a), floor5(b)) for a, b in subtract([(w0, w1)], busy + occupied)]
        f = [(a, b) for a, b in f if b > a]
        l0, l1 = at(day, hm(L["window_start"]), tz), at(day, hm(L["window_end"]), tz)
        need, ideal = minutes(L["minutes"]), at(day, hm(L["ideal_start"]), tz)
        best = None
        for a, b in f:
            lo, hi = max(a, l0), min(b, l1)
            if hi - lo >= need:
                s = min(max(ideal, lo), hi - need)
                cand = (abs(s - ideal), s)
                best = cand if best is None or cand < best else best
        if best:
            reserve = (best[1], best[1] + need)
            lunch_at[day] = reserve
        else:
            reserve = (l0, l1)  # no room for a real lunch, so at least keep Sage out of it
        f = subtract(f, [reserve])
        free[day] = [(a, b) for a, b in f if mins(b - a) >= cfg["min_block"]]

    # ---- turn open tasks into units (tasks sitting in fixed blocks are already handled)
    placed_elsewhere = {k for eid in fixed_ids for k in blocks[eid]["task_keys"]}
    far = at(today + dt.timedelta(days=3650), dt.time(0), tz)

    def sort_key(k):
        t = tasks[k]
        dl = deadline_of(t)
        return (-rank[k]["score"], dl or far, t["first_seen"], k)

    def deadline_of(t):
        if t.get("prep_for"):
            return parse_dt(t["prep_for"]["start"], tz) - minutes(5)
        return due_dt(t, cfg, tz)

    def not_before_of(t):
        bounds = []
        if t.get("prep_for"):
            day = parse_dt(t["prep_for"]["start"], tz).date()
            bounds.append(at(previous_working_day(day, cfg), hm(cfg["work_start"]), tz))
        if t.get("not_before"):
            bounds.append(parse_dt(t["not_before"], tz))
        return max(bounds) if bounds else None

    # ---- rank: account tier + the task's own priority + deadline pressure. Points live
    # in config.json so the balance can be tuned without touching code.
    accounts = accounts or []
    next_wd = working_days_after(today, 1, cfg)[0]
    third_wd = working_days_after(today, 3, cfg)[-1]
    pp, tp, up = cfg["priority_points"], cfg["tier_points"], cfg["urgency_points"]

    def urgency(t):
        dl = deadline_of(t)
        if dl is None:
            return 0, ""
        # Prep blocks already say which meeting they're for, so no separate due label.
        when = f"today {dl:%H:%M}" if dl.date() == today else fmt_when(dl)
        label = "" if t.get("prep_for") else f"due {when}"
        if dl < now:
            return up["overdue"], f"overdue (was due {when})"
        if dl <= at(next_wd, hm(cfg["work_end"]), tz):
            return up["next_working_day"], label
        if dl <= at(third_wd, hm(cfg["work_end"]), tz):
            return up["three_working_days"], label
        return 0, label

    rank = {}
    for k, t in tasks.items():
        if t["status"] != "open":
            continue
        acct = resolve_account(t, accounts, cfg)
        u_pts, u_label = urgency(t)
        score = pp.get(str(t["priority"]), 0) + tp.get(str(acct["tier"]), 0) + u_pts
        if acct["name"]:
            label = f"Tier {acct['tier']} · {acct['name']}" + (f" ({acct['why']})" if acct["why"] else "")
        else:
            label = "no account"
        label += f" · P{t['priority']}" + (f" · {u_label}" if u_label else "")
        rank[k] = {"score": score, "why": label, "tier": acct["tier"], "account": acct["name"]}

    open_keys = sorted((k for k, t in tasks.items() if t["status"] == "open" and k not in placed_elsewhere),
                       key=sort_key)
    units, quick = [], []
    for k in open_keys:
        t = tasks[k]
        # Only undated quick tasks batch: one deadline would otherwise drag the whole
        # batch into overflow with it.
        if t["minutes"] <= cfg["quick_task_max"] and not (t.get("prep_for") or t.get("due") or t.get("not_before")):
            quick.append(k)
            continue
        dur = quantize(t["minutes"], cfg)
        # Estimates are rough, so a block may shrink by up to a fifth to fit a gap.
        min_dur = max(cfg["min_block"], int(dur * 0.8) // 5 * 5)
        score = rank[k]["score"]
        units.append(Unit("task", [k], t["priority"], deadline_of(t), not_before_of(t), dur, min_dur, sort_key(k),
                          score, score >= cfg["cap_override_score"]))
    # Private and shareable quick tasks batch separately, so one private item
    # doesn't hide a whole block of ordinary work from colleagues.
    for private in (False, True):
        batch = []
        for k in (x for x in quick if bool(tasks[x].get("private")) == private):
            load = sum(max(10, tasks[x]["minutes"]) for x in batch)
            if batch and (load + max(10, tasks[k]["minutes"]) > cfg["batch_minutes"] or len(batch) >= cfg["batch_max_items"]):
                quick_unit(units, batch, tasks, sort_key, deadline_of, cfg, rank)
                batch = []
            batch.append(k)
        if batch:
            quick_unit(units, batch, tasks, sort_key, deadline_of, cfg, rank)
    units.sort(key=lambda u: u.order)

    # ---- place units: keep existing blocks where they still work, otherwise earliest fit
    claimed = []
    load = {d: {"minutes": 0, "blocks": 0} for d in days}
    for eid in fixed_ids:
        bs, be = parse_dt(blocks[eid]["start"], tz), parse_dt(blocks[eid]["end"], tz)
        if bs.date() in load:
            load[bs.date()]["minutes"] += mins(be - bs)
            load[bs.date()]["blocks"] += 1
    unclaimed = dict(movable)
    task_block = {blocks[eid]["task_keys"][0]: eid for eid in movable if blocks[eid]["kind"] == "task"}
    batch_blocks = sorted((eid for eid in movable if blocks[eid]["kind"] == "batch"), key=lambda x: movable[x])

    def caps(u):
        if u.must:
            return cfg["daily_cap_minutes"] + cfg["cap_override_minutes"], cfg["daily_cap_blocks"] + 1
        return cfg["daily_cap_minutes"], cfg["daily_cap_blocks"]

    def fits(u, s, e):
        day = s.date()
        if day not in load or e.date() != day or s < lead:
            return False
        if not (u.min_dur <= mins(e - s) <= u.dur):
            return False
        cap_minutes, cap_blocks = caps(u)
        if load[day]["minutes"] + mins(e - s) > cap_minutes or load[day]["blocks"] >= cap_blocks:
            return False
        if u.not_before and s < u.not_before:
            return False
        if u.deadline and u.deadline > lead and e > u.deadline:
            return False
        return covers(subtract(free[day], claimed), s, e)

    def find_slot(u, avoid):
        for cuts in (claimed + avoid, claimed):
            for day in days:
                cap_minutes, cap_blocks = caps(u)
                room = cap_minutes - load[day]["minutes"]
                if room < u.min_dur or load[day]["blocks"] >= cap_blocks:
                    continue
                for a, b in subtract(free[day], cuts):
                    s = max(a, ceil5(u.not_before)) if u.not_before else a
                    limit = b
                    if u.deadline and u.deadline > lead:
                        limit = min(limit, floor5(u.deadline))
                    length = min(u.dur, mins(limit - s), room)
                    if length >= u.min_dur:
                        return s, s + minutes(length)
            if not avoid:
                break
        return None

    placements, overflow = [], []
    for u in units:
        own = [task_block.get(u.keys[0])] if u.kind == "task" else [x for x in batch_blocks if x in unclaimed]
        own = [x for x in own if x and x in unclaimed]
        spot = None
        for eid in own[:1] if u.kind == "task" else own:
            bs, be = unclaimed[eid]
            for s, e in ((bs, bs + minutes(u.dur)), (bs, be)):
                if fits(u, s, e):
                    spot = (s, e, eid)
                    break
            if spot:
                break
        if spot is None:
            found = find_slot(u, [iv for x, iv in unclaimed.items() if x not in own])
            if not found and u.must:
                # Better a shorter block than none: a must-do may shrink to half before giving up.
                u.min_dur = max(cfg["min_block"], u.dur // 2 // 5 * 5)
                found = find_slot(u, [iv for x, iv in unclaimed.items() if x not in own])
                if found:
                    notes.append(f"Squeezed '{short(tasks[u.keys[0]]['title'], 50)}' into "
                                 f"{mins(found[1] - found[0])} min rather than drop it.")
            if found:
                spot = (found[0], found[1], own[0] if own else None)
        if spot is None:
            overflow.append(u)
            continue
        s, e, eid = spot
        unclaimed.pop(eid, None)
        claimed.append((s, e))
        load[s.date()]["minutes"] += mins(e - s)
        load[s.date()]["blocks"] += 1
        placements.append((u, s, e, eid))

    # ---- within each unbroken run of Sage blocks, put the highest-ranked work first.
    # Packing decides which work gets time; this decides the order you meet it in.
    # (Letting top items bump others outright was tried: it fits less work overall.)
    by_day = {}
    for i, (u, s, e, eid) in enumerate(placements):
        by_day.setdefault(s.date(), []).append(i)
    for idxs in by_day.values():
        idxs.sort(key=lambda i: placements[i][1])
        chains = [[idxs[0]]]
        for i in idxs[1:]:
            if placements[i][1] == placements[chains[-1][-1]][2]:
                chains[-1].append(i)
            else:
                chains.append([i])
        for chain in chains:
            order = sorted(chain, key=lambda i: placements[i][0].order)
            if len(chain) < 2 or order == chain:
                continue
            t, new = placements[chain[0]][1], {}
            for i in order:
                u, s, e, eid = placements[i]
                ns, ne = t, t + (e - s)
                if (u.not_before and ns < u.not_before) or (u.deadline and u.deadline > lead and ne > u.deadline):
                    new = None
                    break
                new[i], t = (ns, ne), ne
            for i, (ns, ne) in (new or {}).items():
                u, _, _, eid = placements[i]
                placements[i] = (u, ns, ne, eid)

    # ---- blocks nobody claimed: their tasks are done or no longer fit
    for eid in unclaimed:
        blk = blocks.pop(eid)
        why = "task done" if not any(tasks.get(k, {}).get("status") == "open" for k in blk["task_keys"]) else "no longer fits"
        add_op("delete", f"Remove {short(blk['title'], 60)} ({why})", event_id=eid, args=cal_args(eventId=eid))

    # ---- creates and updates
    def visibility_for(keys):
        # Hiring, people matters and anything Claude flagged private never goes on show.
        return "private" if any(tasks.get(k, {}).get("private") for k in keys) else cfg["visibility"]

    for u, s, e, eid in placements:
        title = block_title(u.kind, tasks, u.keys, cfg)
        desc = block_html(u.kind, tasks, u.keys, cfg, tz, {k: rank[k]["why"] for k in u.keys})
        urgent = u.priority == 1 and u.deadline is not None and u.deadline - now <= dt.timedelta(hours=30)
        busy_flag = cfg["busy_policy"] == "all" or (cfg["busy_policy"] == "urgent" and urgent)
        availability = "AVAILABILITY_BUSY" if busy_flag else "AVAILABILITY_FREE"
        visibility = visibility_for(u.keys)
        record = {"kind": u.kind, "task_keys": u.keys, "start": iso(s, tz), "end": iso(e, tz), "title": title,
                  "desc_hash": digest(desc), "availability": availability, "visibility": visibility,
                  "pinned": False, "created": iso(now, tz)}
        if eid:
            blk = blocks[eid]
            args = {}
            if (parse_dt(blk["start"], tz), parse_dt(blk["end"], tz)) != (s, e):
                args.update(startTime=iso(s, tz), endTime=iso(e, tz), timeZone=cfg["timezone"])
            if title != blk["title"] and not blk.get("title_pinned"):
                args["summary"] = title
            if record["desc_hash"] != blk.get("desc_hash"):
                args["description"] = desc
            if availability != blk.get("availability"):
                args["availability"] = availability
            # Blocks from before v1.1 were all private.
            if visibility != blk.get("visibility", "private"):
                args["visibility"] = visibility
            record.update(created=blk.get("created", record["created"]), title_pinned=blk.get("title_pinned", False))
            if blk.get("title_pinned"):
                record["title"] = blk["title"]
            blocks[eid] = record
            if args:
                verb = "Move" if "startTime" in args else "Update"
                add_op("update", f"{verb}: {short(record['title'], 60)} → {fmt_when(s)}", event_id=eid,
                       args=cal_args(eventId=eid, **args))
        else:
            args = cal_args(summary=title, startTime=iso(s, tz), endTime=iso(e, tz), timeZone=cfg["timezone"],
                            colorId=cfg["colour_id"], visibility=visibility, availability=availability,
                            description=desc)
            if cfg["reminder_minutes"] is None:
                args["useDefaultReminders"] = False
            else:
                args["overrideReminders"] = [{"method": "popup", "minutes": cfg["reminder_minutes"]}]
            op_id = add_op("create", f"Add: {short(title, 60)} at {fmt_when(s)}", args=args)
            blocks[f"pending:{op_id}"] = record

    # Blocks Sage isn't moving (you placed them, or they're under way) still follow the
    # sharing setting, without touching their time or your title.
    for eid in fixed_ids:
        blk = blocks.get(eid)
        if not blk or not blk["task_keys"]:
            continue
        visibility = visibility_for(blk["task_keys"])
        if visibility != blk.get("visibility", "private"):
            add_op("update", f"Share as focus time: {short(blk['title'], 60)}", event_id=eid,
                   args=cal_args(eventId=eid, visibility=visibility))
            blk["visibility"] = visibility

    # ---- heads-ups worth surfacing
    for u in overflow:
        t = tasks[u.keys[0]]
        if u.kind == "batch":
            heads_up.append(f"{len(u.keys)} quick hits didn't fit in the next {len(days)} working days.")
        elif u.deadline and u.deadline <= (at(days[-1], hm(cfg["work_end"]), tz) if days else now):
            heads_up.append(f"No room before the deadline for '{short(t['title'], 60)}' "
                            f"(needs {u.min_dur}+ min by {fmt_when(u.deadline)}).")
    for k, t in tasks.items():
        if t["status"] == "open" and t.get("rolls", 0) >= cfg["roll_warning"]:
            heads_up.append(f"'{short(t['title'], 60)}' has rolled over {t['rolls']} times: drop it, "
                            f"hand it off, or make it the first thing you do?")
        dl = deadline_of(t)
        if t["status"] == "open" and dl and dl < now and not t.get("prep_for"):
            heads_up.append(f"Overdue since {fmt_when(dl)}: '{short(t['title'], 60)}'.")
    for eid, blk in blocks.items():
        if blk.get("pinned") and blk["kind"] != "banner" and parse_dt(blk["end"], tz) > now:
            bs, be = parse_dt(blk["start"], tz), parse_dt(blk["end"], tz)
            if any(a < be and bs < b for a, b in meetings):
                heads_up.append(f"'{short(blk['title'], 50)}' (you placed it) now clashes with a meeting.")

    # ---- the daily banner: today's list plus everything that didn't fit
    if cfg["banner"] and days and days[0] == today and today.isoformat() not in st["banner_dismissed"]:
        plan_banner(st, tasks, blocks, overflow, heads_up, tasks_doc, cfg, tz, now, add_op, cal_args, rank)

    # ---- tidy the ledger
    horizon = now - dt.timedelta(days=14)
    for eid in [x for x, b in blocks.items() if parse_dt(b["end"], tz) < horizon]:
        del blocks[eid]
    month = now - dt.timedelta(days=30)
    for k in [k for k, t in tasks.items() if t["status"] in ("done", "stale") and parse_dt(t.get("done_at"), tz) and parse_dt(t["done_at"], tz) < month]:
        del tasks[k]
    st["banner_dismissed"] = [d for d in st["banner_dismissed"] if d >= (today - dt.timedelta(days=7)).isoformat()]

    counts = {"create": 0, "update": 0, "delete": 0}
    for op in ops:
        counts[op["op"]] += 1
    summary = render_summary(cfg, tz, now, ops, st, overflow, heads_up, notes, tasks_doc, rank)
    return {"version": VERSION, "now": iso(now, tz), "ops": ops, "counts": counts, "summary": summary,
            "ledger_before": before, "ledger_after": st}


def quick_unit(units, batch, tasks, sort_key, deadline_of, cfg, rank):
    deadlines = [d for d in (deadline_of(tasks[k]) for k in batch) if d]
    order = min(sort_key(k) for k in batch)
    score = max(rank[k]["score"] for k in batch)
    units.append(Unit("batch", list(batch), min(tasks[k]["priority"] for k in batch),
                      min(deadlines) if deadlines else None, None, cfg["batch_minutes"], cfg["min_block"], order,
                      score, score >= cfg["cap_override_score"]))


def plain_title(title, cfg):
    for prefix in (cfg["title_prefix"], cfg["banner_prefix"]):
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


def priority_order(tasks, rank, limit=None):
    keys = sorted(rank, key=lambda k: (-rank[k]["score"], tasks[k]["first_seen"], k))
    return keys[:limit] if limit else keys


def plan_banner(st, tasks, blocks, overflow, heads_up, tasks_doc, cfg, tz, now, add_op, cal_args, rank):
    today = now.date()
    timed = sorted(((parse_dt(b["start"], tz), parse_dt(b["end"], tz), b) for b in blocks.values()
                    if b["kind"] != "banner" and parse_dt(b["end"], tz) > now), key=lambda x: x[0])
    todays = [x for x in timed if x[0].date() == today]
    later = [x for x in timed if x[0].date() > today]
    over_keys = [k for u in overflow for k in u.keys]
    rows = lambda xs: "".join(f"<li>{fmt_span(s, e)} {esc(plain_title(b['title'], cfg))}</li>" for s, e, b in xs)
    parts = [f"<b>Today</b><ul>{rows(todays) or '<li>Nothing left today: the gaps are full or gone.</li>'}</ul>"]
    if later:
        parts.append("<b>Coming up</b><ul>" + "".join(
            f"<li>{fmt_day(s)} {fmt_span(s, e)} {esc(plain_title(b['title'], cfg))}</li>"
            for s, e, b in later) + "</ul>")
    top = priority_order(tasks, rank, 10)
    if top:
        parts.append("<b>Priority order</b><ol>" + "".join(
            f"<li>{esc(tasks[k]['title'])} <small>({esc(rank[k]['why'])})</small></li>" for k in top) + "</ol>")
    if over_keys:
        parts.append("<b>Didn't fit</b><ul>" + "".join(
            f"<li>P{tasks[k]['priority']} {esc(tasks[k]['title'])}</li>" for k in over_keys) + "</ul>")
    if heads_up:
        parts.append("<b>Heads-up</b><ul>" + "".join(f"<li>{esc(h)}</li>" for h in heads_up) + "</ul>")
    src = tasks_doc.get("sources") or {}
    if src:
        parts.append("<small>Sources: " + esc(" · ".join(f"{k} {v}" for k, v in sorted(src.items()))) + "</small>")
    parts.append("<small>Delete this banner to hide it for today. sage:v1 kind=banner keys=</small>")
    desc = "".join(parts)
    if todays:
        headline = f"{len(todays)} block{'s' if len(todays) != 1 else ''} today"
    elif later:
        headline = f"next block {fmt_when(later[0][0])}"
    else:
        headline = "nothing planned"
    title = f"{cfg['banner_prefix']}Sage: {headline}" + (f" · {len(over_keys)} didn't fit" if over_keys else "")
    day0 = at(today, dt.time(0), tz)
    day1 = at(today + dt.timedelta(days=1), dt.time(0), tz)
    existing = [eid for eid, b in blocks.items() if b["kind"] == "banner" and b.get("date") == today.isoformat()]
    record = {"kind": "banner", "date": today.isoformat(), "task_keys": [], "start": iso(day0, tz),
              "end": iso(day1, tz), "title": title, "desc_hash": digest(desc)}
    if existing:
        eid = existing[0]
        old = blocks[eid]
        args = {}
        if old["title"] != title:
            args["summary"] = title
        if old.get("desc_hash") != record["desc_hash"]:
            args["description"] = desc
        blocks[eid] = record
        if args:
            add_op("update", "Refresh today's Sage banner", event_id=eid, args=cal_args(eventId=eid, **args))
    else:
        # The calendar tool turns an all-day start into a UTC date, so local midnight
        # (+01:00) would land on the previous day. Send UTC midnight of the right date.
        op_id = add_op("create", "Add today's Sage banner", args=cal_args(
            summary=title, allDay=True, startTime=f"{today.isoformat()}T00:00:00Z",
            endTime=f"{(today + dt.timedelta(days=1)).isoformat()}T00:00:00Z",
            colorId=cfg["colour_id"], visibility=cfg["banner_visibility"], availability="AVAILABILITY_FREE",
            description=desc, useDefaultReminders=False))
        blocks[f"pending:{op_id}"] = record


def render_summary(cfg, tz, now, ops, st, overflow, heads_up, notes, tasks_doc, rank):
    lines = [f"Sage v{VERSION} · plan at {fmt_when(now)}"]
    if ops:
        lines.append("Changes:")
        lines += [f"  - {op['describe']}" for op in ops]
    else:
        lines.append("No calendar changes needed.")
    top = priority_order(st["tasks"], rank, 10)
    if top:
        lines.append("Priority order:")
        lines += [f"  {i}. [{rank[k]['score']}] {st['tasks'][k]['title']}  ({rank[k]['why']})"
                  for i, k in enumerate(top, 1)]
    blocks = sorted(((parse_dt(b["start"], tz), parse_dt(b["end"], tz), b) for b in st["blocks"].values()
                     if b["kind"] != "banner" and parse_dt(b["end"], tz) > now), key=lambda x: x[0])
    if blocks:
        lines.append("Planned:")
        lines += [f"  - {fmt_day(s)} {fmt_span(s, e)}  {b['title']}" for s, e, b in blocks]
    if overflow:
        lines.append("Didn't fit:")
        lines += [f"  - P{st['tasks'][k]['priority']} {st['tasks'][k]['title']}" for u in overflow for k in u.keys]
    if heads_up:
        lines.append("Heads-up:")
        lines += [f"  - {h}" for h in heads_up]
    if notes:
        lines.append("Noticed:")
        lines += [f"  - {n}" for n in notes]
    src = tasks_doc.get("sources") or {}
    if src:
        lines.append("Sources: " + " · ".join(f"{k} {v}" for k, v in sorted(src.items())))
    return "\n".join(lines)


# ---------------------------------------------------------------- commands

def run_dir_for(args):
    rid = getattr(args, "run", None) or (RUNS / "LATEST").read_text().strip()
    return RUNS / rid


def now_for(args, tz):
    return parse_dt(args.now, tz) if getattr(args, "now", None) else dt.datetime.now(tz)


def check_lock(now, tz, force=False):
    """One run at a time: two runs committing over each other would lose ledger changes."""
    if LOCK_PATH.exists() and not force:
        held = load_json(LOCK_PATH)
        if now - parse_dt(held["started"], tz) < dt.timedelta(minutes=30):
            sys.exit(f"Another Sage run ({held['run']}) started at {held['started']}. "
                     f"Wait for it, or pass --force if it crashed.")


def new_run(now, cfg, tz, force=False, suffix=""):
    RUNS.mkdir(exist_ok=True)
    check_lock(now, tz, force)
    days = planning_days(now, cfg, tz)
    window_start = at(min(now.date(), days[0]), dt.time(0), tz)
    window_end = at(days[-1] + dt.timedelta(days=1), dt.time(0), tz)
    run_id = now.strftime("%Y%m%d-%H%M%S") + suffix
    rd = RUNS / run_id
    rd.mkdir(exist_ok=True)
    meta = {"run": run_id, "now": iso(now, tz), "window_start": iso(window_start, tz),
            "window_end": iso(window_end, tz), "planning_days": [d.isoformat() for d in days]}
    save_json(rd / "meta.json", meta)
    (RUNS / "LATEST").write_text(run_id)
    save_json(LOCK_PATH, {"run": run_id, "started": iso(now, tz)})
    return rd, meta


IDENTITY_KEYS = ("my_email", "calendar_id", "timezone", "internal_domains", "slack_user_id",
                 "slack_workspace_url", "jira_cloud_id", "jira_site_url")
INIT_FLAGS = {"email": "my_email", "calendar": "calendar_id", "timezone": "timezone",
              "slack_user": "slack_user_id", "slack_workspace": "slack_workspace_url",
              "jira_cloud_id": "jira_cloud_id", "jira_site": "jira_site_url",
              "work_start": "work_start", "work_end": "work_end", "visibility": "visibility",
              "busy_policy": "busy_policy"}


def cmd_init(args):
    """First-time setup: create the data folder from the templates, then fill in
    whatever identity the setup flow discovered. Safe to re-run; it only fills in."""
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "runs").mkdir(exist_ok=True)
    templates = SKILL_DIR / "templates"
    for name in ("accounts.md", "inbox.md"):
        if not (HOME / name).exists():
            shutil.copy(templates / name, HOME / name)
    cfg_path = HOME / "config.json"
    cfg = load_json(cfg_path) if cfg_path.exists() else load_json(templates / "config.json")
    for flag, key in INIT_FLAGS.items():
        value = getattr(args, flag)
        if value:
            cfg[key] = value
    if args.internal_domain:
        cfg["internal_domains"] = args.internal_domain
    if cfg.get("my_email") and cfg.get("calendar_id") in (None, "", "primary"):
        cfg["calendar_id"] = cfg["my_email"]
    if cfg.get("my_email") and not cfg.get("internal_domains"):
        cfg["internal_domains"] = [cfg["my_email"].split("@")[-1]]
    ZoneInfo(cfg["timezone"])   # fail now, not on the first scheduled run
    save_json(cfg_path, cfg)
    missing = [k for k in IDENTITY_KEYS if not cfg.get(k)]
    print(json.dumps({"data_dir": str(HOME), "config": str(cfg_path), "accounts": str(HOME / "accounts.md"),
                      "inbox": str(HOME / "inbox.md"), "you": {k: cfg.get(k) for k in IDENTITY_KEYS},
                      "not_set": missing}, indent=2))


def sweep_plan(state, now, cfg, tz, force_deep=False):
    """How far back this run reads sources. A month-deep sweep runs at most weekly
    (or on request): re-reading a month of inbox every run is slow, and anything it
    finds lives in the ledger afterwards. Other runs only read since the last run."""
    last_deep = parse_dt(state.get("last_deep_sweep"), tz)
    if force_deep or last_deep is None or now - last_deep >= dt.timedelta(days=cfg["deep_sweep_every_days"]):
        return "deep", now - dt.timedelta(days=cfg["deep_lookback_days"])
    last_run = parse_dt(state.get("last_run"), tz)
    floor = now - dt.timedelta(days=cfg["incremental_lookback_days"])
    if last_run is None or last_run < floor:
        return "incremental", floor
    return "incremental", last_run - dt.timedelta(hours=1)


def cmd_begin(args):
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    now = now_for(args, tz)
    rd, meta = new_run(now, cfg, tz, args.force)
    state = load_json(STATE_PATH, EMPTY_STATE)
    sweep, since = sweep_plan(state, now, cfg, tz, args.deep)
    meta.update(sweep=sweep, look_back_from=iso(since, tz))
    save_json(rd / "meta.json", meta)
    open_tasks = [{"key": k, "title": t["title"], "source": t.get("source"), "links": t.get("links", []),
                   "first_seen": t["first_seen"], "status": t["status"]}
                  for k, t in state["tasks"].items() if t["status"] in ("open", "snoozed") and t.get("source") != "inbox"]
    accounts = parse_accounts(ACCOUNTS_PATH.read_text()) if ACCOUNTS_PATH.exists() else []
    identity = {k: cfg.get(k) for k in IDENTITY_KEYS}
    print(json.dumps({**meta, "data_dir": str(HOME), "you": identity,
                      "last_run": state.get("last_run"), "run_dir": str(rd),
                      "accounts_by_tier": {f"tier {n}": [a["name"] for a in accounts if a["tier"] == n] for n in (1, 2, 3)},
                      "recheck_these_open_tasks": open_tasks}, indent=2, ensure_ascii=False))


def cmd_slim_events(args):
    cfg = load_config()
    rd = run_dir_for(args)
    meta = load_json(rd / "meta.json")
    seen = {}
    for path in args.raw:
        for raw in unwrap_raw(json.loads(Path(path).read_text())):
            ev = slim_event(raw, cfg)
            if ev:
                seen[ev["id"]] = ev
    out = {"window_start": meta["window_start"], "window_end": meta["window_end"], "events": list(seen.values())}
    save_json(rd / "events.json", out)
    print(f"Wrote {len(seen)} events to {rd / 'events.json'}")


AUTOMATED_SENDER = re.compile(
    r"noreply|no-reply|do-not-reply|notifications?@|jira@|confluence@|calendar|drive-shares|comments-noreply|"
    r"careers|coralogix|hubspot|github|@e\.atlassian|members@|info@|news|enews|marketing", re.IGNORECASE)


def _plain(text, n):
    text = html.unescape(str(text or ""))
    # Drop quoted history so a reply reads as just the new message.
    text = re.split(r"\n(?:>|On .{5,80} wrote:|From: |-----Original Message)", text)[0]
    return short(text, n)


def peek_lines(data, me):
    """Compact, readable rows from a saved tool result, so unattended runs never need jq or cat."""
    if isinstance(data, list) and data and isinstance(data[0], dict) and "text" in data[0]:
        try:
            data = json.loads("".join(p.get("text", "") for p in data))
        except ValueError:
            return [short("".join(p.get("text", "") for p in data), 20000)]
    if isinstance(data, dict) and "threads" in data:
        rows = [f"GMAIL SEARCH: {len(data['threads'])} threads shown (estimate {data.get('resultCountEstimate', '?')}), "
                f"nextPageToken={data.get('nextPageToken', 'none')}",
                "flags: ME = latest message is from you (probably handled) · AUTO = automated sender"]
        for t in data["threads"]:
            msgs = sorted(t.get("messages") or [], key=lambda m: m.get("date", ""))
            if not msgs:
                continue
            last = msgs[-1]
            sender = last.get("sender", "")
            flag = "ME  " if me and me.lower() in sender.lower() else ("AUTO" if AUTOMATED_SENDER.search(sender) else "    ")
            rows.append(f"{flag} {t.get('id')} | {last.get('date', '')[:10]} | {len(msgs)} msg | {short(sender, 40)} | "
                        f"{short(html.unescape(last.get('subject', '')), 80)} | {_plain(last.get('snippet'), 200)} | "
                        f"{t.get('viewUrl', '')}")
        return rows
    if isinstance(data, dict) and "messages" in data:
        msgs = sorted(data["messages"], key=lambda m: m.get("date", ""))
        rows = [f"GMAIL THREAD {data.get('id')}: {len(msgs)} messages, latest 4 shown, quoted history trimmed. "
                f"{data.get('viewUrl', '')}"]
        for m in msgs[-4:]:
            rows.append(f"--- {m.get('date', '')} from {m.get('sender', '')} to {', '.join(m.get('toRecipients') or [])}")
            rows.append(_plain(m.get("plaintextBody") or m.get("snippet"), 3000))
        return rows
    if isinstance(data, dict) and "events" in data:
        rows = [f"CALENDAR: {len(data['events'])} events (use slim-events to build events.json)"]
        for e in data["events"]:
            s = e.get("start") or {}
            rows.append(f"{e.get('id')} | {s.get('dateTime') or s.get('date')} | {short(e.get('summary', ''), 60)}")
        return rows
    if isinstance(data, dict) and isinstance(data.get("results"), str):
        return [short(data["results"], 20000)]
    return [short(json.dumps(data, ensure_ascii=False), 20000)]


def cmd_peek(args):
    cfg = load_config()
    raw = Path(args.file).read_text()
    try:
        data = json.loads(raw)
    except ValueError:
        print(short(raw, 20000))
        return
    print("\n".join(peek_lines(data, cfg.get("my_email", ""))))


def validate_events(doc, tz):
    problems = []
    for e in doc.get("events", []):
        try:
            s, en = parse_dt(e["start"], tz), parse_dt(e["end"], tz)
            if en < s:
                problems.append(f"{e['id']}: ends before it starts")
        except Exception as exc:  # noqa: BLE001 - report every bad row, not just the first
            problems.append(f"{e.get('id', '?')}: {exc}")
    return problems


def cmd_plan(args):
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    rd = run_dir_for(args)
    events_doc = load_json(rd / "events.json")
    problems = validate_events(events_doc, tz)
    if problems:
        sys.exit("events.json has problems:\n  " + "\n  ".join(problems))
    tasks_doc = load_json(rd / "tasks.json", {"tasks": []})
    inbox = parse_inbox(INBOX_PATH.read_text()) if INBOX_PATH.exists() else []
    accounts = parse_accounts(ACCOUNTS_PATH.read_text()) if ACCOUNTS_PATH.exists() else []
    state = load_json(STATE_PATH, EMPTY_STATE)
    plan = make_plan(cfg, state, events_doc, tasks_doc, inbox, now_for(args, tz), accounts)
    save_json(rd / "plan.json", plan)
    (rd / "summary.md").write_text(plan["summary"] + "\n")
    print(plan["summary"])
    print()
    print(f"{len(plan['ops'])} op(s) in {rd / 'plan.json'}. Execute each one's args with the matching "
          f"calendar tool, then write results.json and run: python3 sage.py commit {rd / 'results.json'}")


def apply_results(plan, raw):
    """Fold executed ops back into the ledger. Failed ops keep the pre-plan record,
    so the ledger always matches what is actually on the calendar."""
    results = {r["op_id"]: r for r in raw} if isinstance(raw, list) else raw
    before, after = plan["ledger_before"], copy.deepcopy(plan["ledger_after"])
    failed = []
    for op in plan["ops"]:
        r = results.get(op["op_id"]) or {}
        ok = bool(r.get("ok"))
        if op["op"] == "create":
            rec = after["blocks"].pop(f"pending:{op['op_id']}", None)
            if ok and r.get("event_id") and rec:
                after["blocks"][r["event_id"]] = rec
        elif not ok and op["event_id"] in before["blocks"]:
            after["blocks"][op["event_id"]] = before["blocks"][op["event_id"]]
        if not ok:
            failed.append(f"{op['op_id']} {op['describe']}: {r.get('error', 'no result recorded')}")
    for k in [k for k in after["blocks"] if k.startswith("pending:")]:
        del after["blocks"][k]
    return after, failed


def cmd_commit(args):
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    rd = run_dir_for(args)
    plan = load_json(rd / "plan.json")
    raw = load_json(args.results)
    after, failed = apply_results(plan, raw)
    after["last_run"] = iso(now_for(args, tz), tz)
    if load_json(rd / "meta.json", {}).get("sweep") == "deep":
        after["last_deep_sweep"] = after["last_run"]
    save_json(STATE_PATH, after)
    if LOCK_PATH.exists():
        LOCK_PATH.unlink()
    done = len(plan["ops"]) - len(failed)
    print(f"Committed {done}/{len(plan['ops'])} op(s).")
    for f in failed:
        print(f"  FAILED {f}")


def cmd_status(args):
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    st = load_json(STATE_PATH, EMPTY_STATE)
    now = now_for(args, tz)
    print(f"Last run: {st.get('last_run')}")
    for status in ("open", "snoozed", "done", "stale"):
        rows = [(k, t) for k, t in st["tasks"].items() if t["status"] == status]
        if rows:
            print(f"\n{status.upper()} ({len(rows)})")
            for k, t in sorted(rows, key=lambda r: (r[1].get("priority", 2), r[1]["first_seen"])):
                extra = f" until {t['snoozed_until'][:10]}" if status == "snoozed" else ""
                print(f"  P{t.get('priority', 2)} {t['title']}{extra}  [{k}]")
    blocks = sorted(((parse_dt(b["start"], tz), b) for b in st["blocks"].values()
                     if parse_dt(b["end"], tz) > now and b["kind"] != "banner"), key=lambda x: x[0])
    print(f"\nUPCOMING BLOCKS ({len(blocks)})")
    for s, b in blocks:
        print(f"  {fmt_when(s)}  {b['title']}{'  (pinned)' if b.get('pinned') else ''}")


def cmd_done(args):
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    check_lock(now_for(args, tz), tz)
    st = load_json(STATE_PATH, EMPTY_STATE)
    q = args.query.lower()
    hits = [k for k, t in st["tasks"].items() if t["status"] in ("open", "snoozed")
            and (k.lower() == q or q in t["title"].lower())]
    if not hits:
        sys.exit(f"No open task matches '{args.query}'.")
    if len(hits) > 1 and not args.all:
        sys.exit("Several tasks match; be more specific or pass --all:\n  " +
                 "\n  ".join(f"{st['tasks'][k]['title']} [{k}]" for k in hits))
    for k in hits:
        st["tasks"][k].update(status="done", done_at=iso(now_for(args, tz), tz), done_reason="marked done by you")
        print(f"Done: {st['tasks'][k]['title']}")
    save_json(STATE_PATH, st)
    print("The next plan removes its future blocks.")


def cmd_clear(args):
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    now = now_for(args, tz)
    rd, _ = new_run(now, cfg, tz, suffix="-clear")
    st = load_json(STATE_PATH, EMPTY_STATE)
    after = copy.deepcopy(st)
    ops = []
    for eid, b in st["blocks"].items():
        if parse_dt(b["end"], tz) > now:
            ops.append({"op": "delete", "op_id": f"op{len(ops) + 1}", "event_id": eid,
                        "describe": f"Remove {short(b['title'], 60)}",
                        "args": {"calendarId": cfg["calendar_id"], "eventId": eid, "notificationLevel": "NONE"}})
            del after["blocks"][eid]
    summary = (f"Clear: {len(ops)} future Sage block(s) to delete. Tasks stay in the ledger. "
               f"Execute the ops, then commit {rd / 'results.json'}.")
    save_json(rd / "plan.json", {"version": VERSION, "now": iso(now, tz), "ops": ops, "summary": summary,
                                 "counts": {"delete": len(ops)}, "ledger_before": st, "ledger_after": after})
    print(summary)
    for op in ops:
        print(f"  - {op['describe']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Sage calendar PA engine")
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--home", help=f"data folder (default: $SAGE_HOME or {DEFAULT_HOME})")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("init")
    for flag in INIT_FLAGS:
        sp.add_argument("--" + flag.replace("_", "-"), dest=flag)
    sp.add_argument("--internal-domain", action="append", dest="internal_domain")
    for name in ("begin", "plan", "status", "clear"):
        sp = sub.add_parser(name)
        sp.add_argument("--now")
        sp.add_argument("--run")
        if name == "begin":
            sp.add_argument("--force", action="store_true")
            sp.add_argument("--deep", action="store_true", help="read a month back this run")
    sp = sub.add_parser("slim-events")
    sp.add_argument("raw", nargs="+")
    sp.add_argument("--run")
    sp = sub.add_parser("commit")
    sp.add_argument("results")
    sp.add_argument("--now")
    sp.add_argument("--run")
    sp = sub.add_parser("peek")
    sp.add_argument("file")
    sp = sub.add_parser("done")
    sp.add_argument("query")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--now")
    args = p.parse_args(argv)
    set_home(args.home)
    if args.cmd not in ("init", "peek") and not (HOME / "config.json").exists():
        sys.exit(f"Sage isn't set up at {HOME} yet. Follow references/SETUP.md "
                 f"(it starts with: sage.py init --email you@company.com).")
    {"init": cmd_init, "begin": cmd_begin, "slim-events": cmd_slim_events, "plan": cmd_plan, "commit": cmd_commit,
     "status": cmd_status, "done": cmd_done, "clear": cmd_clear, "peek": cmd_peek}[args.cmd](args)


if __name__ == "__main__":
    main()
