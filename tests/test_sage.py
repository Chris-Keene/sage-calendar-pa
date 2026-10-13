"""Planner tests against a fake calendar modelled on a real Thu/Fri/Mon.

Run from the skill folder: /usr/bin/python3 -m unittest -v tests.test_sage
"""
import copy
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sage  # noqa: E402

TZ = ZoneInfo("Europe/London")
NOW = "2026-10-08T14:49:00+01:00"   # Thursday afternoon
WINDOW = ("2026-10-08T00:00:00+01:00", "2026-10-13T00:00:00+01:00")
ACCOUNTS = """# Sage account priorities
## Tier 1
- Globex | globex.com | churn risk
- Umbrella Corp | UC, umbrella.com | POC live
## Tier 2
- Initech | INI, initech.com |
## Tier 3
"""


def cfg(**over):
    c = copy.deepcopy(sage.DEFAULT_CONFIG)
    c.update(calendar_id="me@example.com", internal_domains=["example.com"])
    c.update(over)
    return c


def ev(eid, start, end, summary="Meeting", response="accepted", transparent=False, all_day=False, etype="default"):
    return {"id": eid, "summary": summary, "start": start, "end": end, "all_day": all_day,
            "transparent": transparent, "self_response": response, "event_type": etype}


def base_events():
    t = "2026-10-08T"
    f = "2026-10-09T"
    m = "2026-10-12T"
    return [
        ev("thu1", t + "14:30:00+01:00", t + "15:00:00+01:00", "Forecast call"),
        ev("thu2", t + "15:30:00+01:00", t + "16:00:00+01:00", "Interview"),
        ev("thu3", t + "16:00:00+01:00", t + "17:00:00+01:00", "Council"),
        ev("thu4", t + "17:00:00+01:00", t + "18:00:00+01:00", "Enablement"),
        ev("fri1", f + "09:30:00+01:00", f + "10:00:00+01:00", "Pricing sync", response="needsAction"),
        ev("fri2", f + "10:00:00+01:00", f + "11:00:00+01:00", "Customer catch-up"),
        ev("fri3", f + "11:00:00+01:00", f + "12:00:00+01:00", "Product demo"),
        ev("fri4", f + "12:00:00+01:00", f + "14:30:00+01:00", "Busy - Personal", response="none"),
        ev("mon1", m + "09:00:00+01:00", m + "09:30:00+01:00", "Pricing forecast"),
        ev("mon2", m + "10:30:00+01:00", m + "11:00:00+01:00", "Pre-call sync"),
    ]


def task(key, title, minutes=30, priority=2, **kw):
    return {"key": key, "title": title, "minutes": minutes, "priority": priority, "source": "gmail", **kw}


class FakeCal:
    """Applies plan ops the way the Google Calendar tools would."""

    def __init__(self, events):
        self.events = {e["id"]: copy.deepcopy(e) for e in events}
        self.n = 0

    def doc(self):
        return {"window_start": WINDOW[0], "window_end": WINDOW[1], "events": list(self.events.values())}

    def apply(self, plan):
        results = []
        for op in plan["ops"]:
            a = op["args"]
            self.assert_safe(a)
            if op["op"] == "create":
                self.n += 1
                eid = f"sage{self.n}"
                m = sage.MARKER_RE.search(a["description"])
                all_day = a.get("allDay", False)
                self.events[eid] = {
                    "id": eid, "summary": a["summary"],
                    "start": a["startTime"][:10] if all_day else a["startTime"],
                    "end": a["endTime"][:10] if all_day else a["endTime"],
                    "all_day": all_day, "transparent": a["availability"] == "AVAILABILITY_FREE",
                    "self_response": "none", "event_type": "default",
                    "sage_kind": m.group(1), "sage_keys": [k for k in m.group(2).split(",") if k],
                    "color_id": a["colorId"], "visibility": a["visibility"], "description": a["description"],
                }
                results.append({"op_id": op["op_id"], "ok": True, "event_id": eid})
            elif op["op"] == "update":
                e = self.events[op["event_id"]]
                for src, dst in (("summary", "summary"), ("startTime", "start"), ("endTime", "end"),
                                 ("colorId", "color_id"), ("visibility", "visibility"), ("description", "description")):
                    if src in a:
                        e[dst] = a[src]
                results.append({"op_id": op["op_id"], "ok": True})
            else:
                del self.events[op["event_id"]]
                results.append({"op_id": op["op_id"], "ok": True})
        return results

    @staticmethod
    def assert_safe(args):
        assert "attendees" not in args and "addedAttendees" not in args, "Sage must never invite anyone"
        assert args["notificationLevel"] == "NONE"


class Harness:
    def __init__(self, test, events=None, config=None):
        self.t = test
        self.cfg = config or cfg()
        self.cal = FakeCal(events if events is not None else base_events())
        self.state = copy.deepcopy(sage.EMPTY_STATE)
        self.accounts = sage.parse_accounts(ACCOUNTS)

    def plan(self, tasks, now=NOW, inbox=None, sources=None):
        p = sage.make_plan(self.cfg, self.state, self.cal.doc(), {"tasks": tasks, "sources": sources or {}},
                           inbox or [], sage.parse_dt(now, TZ), self.accounts)
        return p

    def run(self, tasks, now=NOW, **kw):
        p = self.plan(tasks, now, **kw)
        results = self.cal.apply(p)
        self.state, failed = sage.apply_results(p, results)
        self.t.assertEqual(failed, [])
        return p

    def sage_events(self, kind=None):
        return sorted((e for e in self.cal.events.values() if e.get("sage_kind") and
                       (kind is None or e["sage_kind"] == kind)), key=lambda e: e["start"])


def timed(ops):
    return [o for o in ops if not o["args"].get("allDay") and "banner" not in o["describe"]]


class PlannerTests(unittest.TestCase):

    def test_first_task_lands_in_todays_only_gap_after_buffer(self):
        h = Harness(self)
        p = h.run([task("gmail:a", "Send Acme pricing", 30, 1)])
        blocks = h.sage_events("task")
        self.assertEqual(len(blocks), 1)
        # 14:30-15:00 meeting + 5 min buffer, next meeting 15:30: a 25-minute squeeze.
        self.assertEqual(blocks[0]["start"], "2026-10-08T15:05:00+01:00")
        self.assertEqual(blocks[0]["end"], "2026-10-08T15:30:00+01:00")
        self.assertEqual(blocks[0]["color_id"], "2")
        self.assertEqual(blocks[0]["visibility"], "default")
        self.assertTrue(blocks[0]["summary"].startswith("\U0001F33F Focus: Send Acme"))
        self.assertEqual(len(h.sage_events("banner")), 1)
        self.assertEqual(p["counts"]["create"], 2)

    def test_rerun_with_nothing_new_changes_nothing(self):
        h = Harness(self)
        tasks = [task("gmail:a", "Send Acme pricing", 30, 1), task("slack:b", "Answer Sam", 10, 2),
                 task("jira:c", "Review TICKET-12", 60, 2)]
        h.run(tasks)
        again = h.plan(tasks, now="2026-10-08T14:52:00+01:00")
        self.assertEqual(again["ops"], [], again["summary"])

    def test_new_meeting_moves_the_block_instead_of_duplicating(self):
        h = Harness(self)
        h.run([task("gmail:a", "Send Acme pricing", 30, 1)])
        first = h.sage_events("task")[0]
        h.cal.events["new"] = ev("new", "2026-10-08T15:00:00+01:00", "2026-10-08T15:30:00+01:00", "Surprise call")
        p = h.run([task("gmail:a", "Send Acme pricing", 30, 1)])
        moves = [o for o in p["ops"] if o["op"] == "update" and "startTime" in o["args"]]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["event_id"], first["id"])
        self.assertEqual(len(h.sage_events("task")), 1)
        self.assertEqual(h.sage_events("task")[0]["start"], "2026-10-09T09:00:00+01:00")

    def test_block_you_moved_stays_put(self):
        h = Harness(self)
        tasks = [task("gmail:a", "Send Acme pricing", 30, 1)]
        h.run(tasks)
        blk = h.sage_events("task")[0]
        blk["start"], blk["end"] = "2026-10-12T15:00:00+01:00", "2026-10-12T15:30:00+01:00"
        p = h.run(tasks)
        self.assertEqual(timed(p["ops"]), [])
        self.assertTrue(h.state["blocks"][blk["id"]]["pinned"])
        self.assertEqual(timed(h.plan(tasks)["ops"]), [])

    def test_block_you_deleted_snoozes_the_task(self):
        h = Harness(self)
        tasks = [task("gmail:a", "Send Acme pricing", 30, 1)]
        h.run(tasks)
        del h.cal.events[h.sage_events("task")[0]["id"]]
        p = h.run(tasks)
        self.assertEqual([o for o in p["ops"] if o["op"] == "create" and "banner" not in o["describe"]], [])
        self.assertEqual(h.state["tasks"]["gmail:a"]["status"], "snoozed")
        # Snooze ends after 3 working days: Thu -> Tue 13 Oct.
        self.assertTrue(h.state["tasks"]["gmail:a"]["snoozed_until"].startswith("2026-10-13T09:00"))

    def test_tick_in_calendar_marks_done_and_greys_the_block(self):
        h = Harness(self)
        tasks = [task("gmail:a", "Send Acme pricing", 30, 1)]
        h.run(tasks)
        blk = h.sage_events("task")[0]
        blk["summary"] = "✓ " + blk["summary"]
        p = h.run(tasks)
        self.assertEqual(h.state["tasks"]["gmail:a"]["status"], "done")
        greys = [o for o in p["ops"] if o["args"].get("colorId") == "8"]
        self.assertEqual(len(greys), 1)
        self.assertNotIn("summary", greys[0]["args"])   # already ticked, so the title is left alone
        self.assertIn(blk["id"], h.cal.events)

    def test_task_done_at_source_frees_the_future_block(self):
        h = Harness(self)
        h.run([task("gmail:a", "Send Acme pricing", 30, 1)])
        p = h.run([task("gmail:a", "Send Acme pricing", 30, 1, status="done")])
        self.assertEqual([o["op"] for o in timed(p["ops"])], ["delete"])
        self.assertEqual(h.sage_events("task"), [])

    def test_quick_tasks_share_one_block(self):
        h = Harness(self)
        h.run([task("slack:1", "Reply to Sam", 10), task("slack:2", "Answer Alex", 5),
               task("mail:3", "Confirm the Hooli slot", 10)])
        batches = h.sage_events("batch")
        self.assertEqual(len(batches), 1)
        self.assertIn("Quick hits (3)", batches[0]["summary"])
        self.assertEqual(h.sage_events("task"), [])

    def test_dated_quick_task_does_not_drag_its_batch_into_overflow(self):
        h = Harness(self)
        h.run([task("slack:1", "Reply to Sam", 10), task("slack:2", "Answer Alex", 5),
               task("globex", "Globex Friday update", 10, 2, due="2026-10-08T15:10:00+01:00")])
        self.assertEqual(len(h.sage_events("batch")), 1)
        self.assertIn("Quick hits (2)", h.sage_events("batch")[0]["summary"])
        self.assertEqual(h.sage_events("task"), [])   # nothing fits 25 min before 15:10
        self.assertIn("Globex Friday update", h.plan([])["summary"])

    def test_not_before_waits_for_the_meeting(self):
        h = Harness(self)
        h.run([task("rec", "Send EPI the demo recording", 15, 2, not_before="2026-10-09T12:00:00+01:00")])
        self.assertEqual(h.sage_events("task")[0]["start"], "2026-10-09T14:35:00+01:00")

    def test_lunch_is_protected_on_an_open_day(self):
        h = Harness(self, events=[])
        tasks = [task(f"t{i}", f"Task {i}", 60, 2) for i in range(4)]
        h.run(tasks, now="2026-10-12T08:00:00+01:00")
        for e in h.sage_events("task"):
            s, en = sage.parse_dt(e["start"], TZ), sage.parse_dt(e["end"], TZ)
            lunch = (sage.parse_dt("2026-10-12T12:30:00+01:00", TZ), sage.parse_dt("2026-10-12T13:00:00+01:00", TZ))
            self.assertFalse(s < lunch[1] and lunch[0] < en, f"{e['summary']} eats lunch")

    def test_prep_lands_before_the_meeting(self):
        h = Harness(self)
        prep = task("prep:fri3", "Prep the product demo", 30, 2,
                    prep_for={"event_id": "fri3", "start": "2026-10-09T11:00:00+01:00", "title": "Product demo"})
        filler = task("gmail:x", "Big P1 report", 30, 1)
        h.run([filler, prep])
        starts = {e["sage_keys"][0]: e["start"] for e in h.sage_events("task")}
        # Deadline pressure (+20) lifts the P2 prep (40) above the undated P1 (30),
        # and either way the prep lands before the 11:00 meeting.
        self.assertEqual(starts["prep:fri3"], "2026-10-08T15:05:00+01:00")
        self.assertEqual(starts["gmail:x"], "2026-10-09T09:00:00+01:00")

    def test_urgent_p1_bumps_a_p3_out_of_the_gap(self):
        h = Harness(self)
        h.run([task("p3", "Tidy notes", 30, 3)])
        self.assertEqual(h.sage_events("task")[0]["start"], "2026-10-08T15:05:00+01:00")
        tasks = [task("p3", "Tidy notes", 30, 3), task("p1", "Globex plan", 30, 1, due="2026-10-08T17:00:00+01:00")]
        h.run(tasks, now="2026-10-08T14:50:00+01:00")
        starts = {e["sage_keys"][0]: e["start"] for e in h.sage_events("task")}
        self.assertEqual(starts["p1"], "2026-10-08T15:05:00+01:00")
        self.assertEqual(starts["p3"], "2026-10-09T09:00:00+01:00")
        urgent = next(e for e in h.sage_events("task") if e["sage_keys"] == ["p1"])
        self.assertFalse(urgent["transparent"], "an urgent P1 block should show as busy")

    def test_declined_and_transparent_events_do_not_block(self):
        events = [ev("d", "2026-10-12T09:00:00+01:00", "2026-10-12T17:30:00+01:00", "Declined", response="declined"),
                  ev("o", "2026-10-12T09:00:00+01:00", "2026-10-12T17:30:00+01:00", "FYI", transparent=True,
                     response="needsAction")]
        h = Harness(self, events=events)
        h.run([task("a", "Write it", 60)], now="2026-10-12T08:00:00+01:00")
        self.assertEqual(h.sage_events("task")[0]["start"], "2026-10-12T09:00:00+01:00")

    def test_out_of_office_day_is_skipped(self):
        events = [ev("ooo", "2026-10-12", "2026-10-13", "OOO", all_day=True, etype="outofoffice")]
        h = Harness(self, events=events)
        h.run([task("a", "Write it", 60)], now="2026-10-12T08:00:00+01:00")
        self.assertTrue(h.sage_events("task")[0]["start"].startswith("2026-10-13"))

    def test_daily_cap_spills_to_next_day(self):
        h = Harness(self, events=[], config=cfg(daily_cap_minutes=120))
        h.run([task(f"t{i}", f"Task {i}", 60) for i in range(3)], now="2026-10-12T08:00:00+01:00")
        days = [e["start"][:10] for e in h.sage_events("task")]
        self.assertEqual(days, ["2026-10-12", "2026-10-12", "2026-10-13"])

    def test_lost_ledger_readopts_blocks_instead_of_duplicating(self):
        h = Harness(self)
        tasks = [task("gmail:a", "Send Acme pricing", 30, 1)]
        h.run(tasks)
        h.state = copy.deepcopy(sage.EMPTY_STATE)
        p = h.run(tasks)
        self.assertEqual([o for o in p["ops"] if o["op"] == "create"], [])
        self.assertEqual(len(h.sage_events("task")), 1)

    def test_untrusted_content_is_escaped_and_links_filtered(self):
        h = Harness(self)
        evil = task("gmail:z", "<script>alert(1)</script> Reply", 30, 1, context="<a href='x'>click</a>",
                    links=[{"label": "Thread", "url": "https://mail.google.com/mail/u/0/#all/1"},
                           {"label": "Phish", "url": "https://evil.example/login"},
                           {"label": "JS", "url": "javascript:alert(1)"}])
        p = h.plan([evil])
        desc = next(o for o in p["ops"] if "allDay" not in o["args"])["args"]["description"]
        self.assertNotIn("<script>", desc)
        self.assertNotIn("evil.example", desc)
        self.assertNotIn("javascript:", desc)
        self.assertIn("https://mail.google.com/mail/u/0/#all/1", desc)

    def test_rollover_counts_and_warns(self):
        h = Harness(self, config=cfg(roll_warning=1))
        tasks = [task("gmail:a", "Send Acme pricing", 30, 1)]
        h.run(tasks)
        p = h.run(tasks, now="2026-10-08T16:00:00+01:00")   # block passed, task still open
        self.assertEqual(h.state["tasks"]["gmail:a"]["rolls"], 1)
        self.assertIn("rolled over", p["summary"])
        self.assertEqual(h.sage_events("task")[-1]["start"], "2026-10-09T09:00:00+01:00")

    def test_banner_is_sent_as_the_right_utc_date(self):
        # Regression: local midnight (+01:00) became the previous day in the calendar tool.
        p = Harness(self).plan([task("a", "Thing", 30)])
        banner = next(o for o in p["ops"] if o["args"].get("allDay"))
        self.assertEqual(banner["args"]["startTime"], "2026-10-08T00:00:00Z")
        self.assertEqual(banner["args"]["endTime"], "2026-10-09T00:00:00Z")

    def test_slim_event_reads_all_day_dates_as_dates(self):
        raw = {"id": "x", "summary": "OOO", "eventType": "OUT_OF_OFFICE",
               "start": {"date": "2026-10-12T00:00:00Z"}, "end": {"date": "2026-10-13T00:00:00Z"}}
        e = sage.slim_event(raw, cfg())
        self.assertEqual((e["start"], e["end"], e["all_day"], e["event_type"]),
                         ("2026-10-12", "2026-10-13", True, "outofoffice"))

    def test_peek_flags_gmail_threads_and_sorts_by_date(self):
        search = {"resultCountEstimate": "3", "threads": [
            {"id": "t1", "viewUrl": "https://mail.google.com/x", "messages": [
                {"date": "2026-10-08T08:00:00Z", "sender": "sam@globex.example", "subject": "Questions", "snippet": "Can you&#39;"},
                {"date": "2026-10-07T08:00:00Z", "sender": "me@example.com", "subject": "Questions", "snippet": "old"}]},
            {"id": "t2", "messages": [{"date": "2026-10-08T09:00:00Z", "sender": "jira@example.atlassian.net",
                                       "subject": "[JIRA]", "snippet": "x"}]},
            {"id": "t3", "messages": [{"date": "2026-10-08T09:00:00Z", "sender": "me@example.com",
                                       "subject": "Re: hi", "snippet": "done"}]}]}
        rows = sage.peek_lines(search, "me@example.com")
        self.assertTrue(rows[2].startswith("     t1 | 2026-10-08") and "sam@globex.example" in rows[2] and "Can you'" in rows[2])
        self.assertTrue(rows[3].startswith("AUTO t2"))
        self.assertTrue(rows[4].startswith("ME   t3"))
        thread = {"id": "t1", "messages": [
            {"date": "2026-10-08T08:00:00Z", "sender": "sam@globex.example", "plaintextBody": "New ask\nOn Mon, 5 Oct someone wrote:\n> old"},
            {"date": "2026-09-01T08:00:00Z", "sender": "a@b.c", "plaintextBody": "ancient"}]}
        rows = sage.peek_lines(thread, "me@example.com")
        self.assertEqual(rows[-1], "New ask")

    # ---- v1.1: account priorities

    def test_accounts_parse(self):
        accts = sage.parse_accounts(ACCOUNTS)
        self.assertEqual([(a["name"], a["tier"]) for a in accts],
                         [("Globex", 1), ("Umbrella Corp", 1), ("Initech", 2)])
        self.assertEqual(accts[1]["aliases"], ["UC", "umbrella.com"])

    def test_account_resolution(self):
        accts, c = sage.parse_accounts(ACCOUNTS), cfg()
        self.assertEqual(sage.resolve_account({"account": "globex"}, accts, c)["name"], "Globex")
        self.assertEqual(sage.resolve_account({"title": "UC tooling: reinstall"}, accts, c)["name"], "Umbrella Corp")
        # Short aliases are case-sensitive and whole-word: "ucla" and "uc" don't count.
        self.assertIsNone(sage.resolve_account({"title": "Call the ucla team about uc stuff"}, accts, c)["name"])
        unknown = sage.resolve_account({"account": "Hooli"}, accts, c)
        self.assertEqual((unknown["name"], unknown["tier"]), ("Hooli", 3))

    def test_tier_one_goes_before_untiered_priority(self):
        h = Harness(self)
        h.run([task("x", "Generic P1 ask", 30, 1), task("t", "Tidy the Globex notes", 30, 3, account="Globex")])
        starts = {e["sage_keys"][0]: e["start"] for e in h.sage_events("task")}
        self.assertEqual(starts["t"], "2026-10-08T15:05:00+01:00")   # Tier 1 P3 = 40 beats untiered P1 = 30
        self.assertEqual(starts["x"], "2026-10-09T09:00:00+01:00")

    def test_a_hard_deadline_still_beats_tier(self):
        h = Harness(self)
        h.run([task("t", "Tidy the Globex notes", 30, 3, account="Globex"),
               task("x", "Generic P1 due tomorrow", 30, 1, due="2026-10-09T17:00:00+01:00")])
        starts = {e["sage_keys"][0]: e["start"] for e in h.sage_events("task")}
        self.assertEqual(starts["x"], "2026-10-08T15:05:00+01:00")   # 30 + 20 urgency = 50 beats 40

    def test_why_line_explains_the_rank(self):
        p = Harness(self).plan([task("t", "Reply to Jo", 30, 1, account="Globex")])
        desc = next(o for o in p["ops"] if not o["args"].get("allDay"))["args"]["description"]
        self.assertIn("Why it's here: Tier 1 · Globex (churn risk) · P1", desc)
        self.assertIn("Priority order:", p["summary"])
        dated = Harness(self).plan([task("d", "Answer Umbrella", 30, 1, account="UC", due="2026-10-09T17:00:00+01:00")])
        desc = next(o for o in dated["ops"] if not o["args"].get("allDay"))["args"]["description"]
        self.assertIn("· P1 · due Fri 9 Oct 17:00 · from gmail", desc)   # the deadline appears exactly once
        self.assertEqual(desc.count("due "), 1)

    def test_must_do_runs_past_the_daily_cap(self):
        h = Harness(self, events=[], config=cfg(daily_cap_minutes=60, daily_cap_blocks=2))
        now = "2026-10-12T08:00:00+01:00"
        h.run([task("a", "Normal one", 30), task("b", "Normal two", 30)], now=now)
        for e in h.sage_events("task"):   # you pin both, so they count against Monday's cap
            e["start"] = e["start"].replace(":00:00", ":05:00").replace(":30:00", ":35:00")
            e["end"] = e["end"].replace(":30:00", ":35:00").replace(":00:00", ":05:00")
        h.run([task("a", "Normal one", 30), task("b", "Normal two", 30),
               task("m", "Globex must-do", 30, 1, account="Globex"), task("c", "Normal three", 30)], now=now)
        days = {e["sage_keys"][0]: e["start"][:10] for e in h.sage_events("task")}
        self.assertEqual(days["m"], "2026-10-12")   # score 60 = must-do, cap stretches
        self.assertEqual(days["c"], "2026-10-13")   # the ordinary one waits

    def test_back_to_back_blocks_run_highest_score_first(self):
        h = Harness(self, events=[ev("m", "2026-10-12T11:00:00+01:00", "2026-10-12T17:30:00+01:00", "All afternoon")])
        now = "2026-10-12T08:00:00+01:00"
        h.run([task("low", "Low thing", 30, 3), task("mid", "Mid thing", 60, 2)], now=now)
        # Then something urgent arrives; packing would leave it in the last free gap.
        h.run([task("low", "Low thing", 30, 3), task("mid", "Mid thing", 60, 2),
               task("top", "Globex overdue update", 15, 1, account="Globex", due="2026-10-09T12:00:00+01:00")], now=now)
        order = [e["sage_keys"][0] for e in h.sage_events() if e["sage_kind"] != "banner"
                 and e["start"].startswith("2026-10-12")]
        self.assertEqual(order, ["top", "mid", "low"])

    def test_resequencing_never_breaks_a_deadline(self):
        h = Harness(self, events=[ev("m", "2026-10-12T10:30:00+01:00", "2026-10-12T17:30:00+01:00", "Busy")])
        now = "2026-10-12T08:00:00+01:00"
        due_early = task("a", "Due 09:30", 30, 3, due="2026-10-12T09:30:00+01:00")
        h.run([due_early], now=now)
        h.run([due_early, task("b", "Bigger but later", 30, 1, account="Globex")], now=now)
        starts = {e["sage_keys"][0]: e["start"] for e in h.sage_events("task")}
        self.assertEqual(starts["a"], "2026-10-12T09:00:00+01:00")   # swapping would miss its 09:30 deadline
        self.assertEqual(starts["b"], "2026-10-12T09:30:00+01:00")

    def test_must_do_shrinks_rather_than_vanishes(self):
        # Only 40 free minutes before the demo; the 60-minute prep normally needs 45.
        events = [ev("busy1", "2026-10-12T09:40:00+01:00", "2026-10-12T17:30:00+01:00", "Wall to wall")]
        h = Harness(self, events=events)
        h.run([task("p", "Build the exec demo", 60, 1, account="Globex",
                    prep_for={"event_id": "x", "start": "2026-10-12T11:00:00+01:00", "title": "Demo"})],
              now="2026-10-12T08:50:00+01:00")
        blk = h.sage_events("task")
        self.assertEqual((blk[0]["start"], blk[0]["end"]), ("2026-10-12T09:00:00+01:00", "2026-10-12T09:40:00+01:00"))

    def test_ten_minute_task_gets_a_25_minute_block(self):
        h = Harness(self)
        h.run([task("t", "Post the Globex update", 10, 1, due="2026-10-09T12:00:00+01:00")])
        blk = h.sage_events("task")[0]
        self.assertEqual((blk["start"], blk["end"]), ("2026-10-08T15:05:00+01:00", "2026-10-08T15:30:00+01:00"))

    # ---- v1.1: visible focus time

    def test_blocks_follow_calendar_sharing_but_private_tasks_stay_private(self):
        h = Harness(self)
        h.run([task("a", "Send Acme pricing", 30, 1), task("h", "Schedule a candidate interview", 30, 2, private=True)])
        vis = {e["sage_keys"][0]: e["visibility"] for e in h.sage_events("task")}
        self.assertEqual(vis, {"a": "default", "h": "private"})
        self.assertEqual(h.sage_events("banner")[0]["visibility"], "private")

    def test_private_quick_tasks_batch_separately(self):
        h = Harness(self, events=[])
        h.run([task("q1", "Answer Lee", 10), task("q2", "Book a candidate interview", 10, private=True),
               task("q3", "Confirm the slot", 10)], now="2026-10-12T08:00:00+01:00")
        vis = sorted((tuple(e["sage_keys"]), e["visibility"]) for e in h.sage_events("batch"))
        self.assertEqual(vis, [(("q1", "q3"), "default"), (("q2",), "private")])

    def test_old_private_blocks_get_shared_once(self):
        h = Harness(self)
        h.run([task("a", "Send Acme pricing", 30, 1)])
        eid = h.sage_events("task")[0]["id"]
        h.state["blocks"][eid].pop("visibility")   # what a v1.0 ledger looks like
        h.cal.events[eid]["visibility"] = "private"
        p = h.run([task("a", "Send Acme pricing", 30, 1)])
        self.assertEqual([o["args"].get("visibility") for o in timed(p["ops"])], ["default"])
        self.assertEqual(timed(h.plan([task("a", "Send Acme pricing", 30, 1)])["ops"]), [])

    # ---- v1.1: easier ticking off

    def test_greying_a_block_marks_it_done(self):
        h = Harness(self)
        tasks = [task("a", "Send Acme pricing", 30, 1)]
        h.run(tasks)
        blk = h.sage_events("task")[0]
        blk["color_id"] = "8"
        p = h.run(tasks)
        self.assertEqual(h.state["tasks"]["a"]["status"], "done")
        self.assertEqual(h.state["tasks"]["a"]["done_reason"], "greyed out in the calendar")
        self.assertIn(blk["id"], h.cal.events)   # kept as a record, not deleted
        self.assertTrue(any(o["args"].get("summary", "").startswith("✓ ") for o in p["ops"]))

    def test_mark_done_link_is_a_mail_to_yourself(self):
        h = Harness(self, config=cfg(my_email="me@example.com"))
        p = h.plan([task("slack:C1/123.45", "Answer Alex", 10), task("b", "Answer Sam", 10)])
        desc = next(o for o in p["ops"] if not o["args"].get("allDay"))["args"]["description"]
        self.assertIn('href="mailto:me@example.com?subject=Sage%20done%3A%20slack%3AC1%2F123.45&amp;body=', desc)
        self.assertEqual(desc.count("✓ Mark done</a>"), 2)   # one per item, even inside quick hits

    def test_mark_done_link_uses_the_web_app_when_configured(self):
        url = "https://script.google.com/a/macros/example.com/s/ABC/exec"
        h = Harness(self, config=cfg(my_email="me@example.com", done_url=url))
        p = h.plan([task("slack:C1/123.45", "Answer Alex & co", 10), task("b", "Answer Sam", 10)])
        desc = next(o for o in p["ops"] if not o["args"].get("allDay"))["args"]["description"]
        self.assertIn(f'href="{url}?k=slack%3AC1%2F123.45&amp;t=Answer%20Alex%20%26%20co"', desc)
        self.assertNotIn("mailto:", desc)
        self.assertEqual(desc.count("✓ Mark done</a>"), 2)

    def test_mark_done_link_ignores_a_non_apps_script_url(self):
        h = Harness(self, config=cfg(my_email="me@example.com", done_url="https://evil.example/exec"))
        p = h.plan([task("a", "Answer Alex", 30, 1)])
        desc = next(o for o in p["ops"] if not o["args"].get("allDay"))["args"]["description"]
        self.assertIn('href="mailto:me@example.com?subject=Sage%20done%3A%20a&amp;', desc)
        self.assertNotIn("evil.example", desc)

    def test_deep_sweep_runs_weekly_or_on_request(self):
        c, now = cfg(), sage.parse_dt("2026-10-13T08:15:00+01:00", TZ)
        mode, since = sage.sweep_plan({}, now, c, TZ)
        self.assertEqual((mode, since.date().isoformat()), ("deep", "2026-09-13"))   # never swept: go a month back
        recent = {"last_deep_sweep": "2026-10-12T08:15:00+01:00", "last_run": "2026-10-12T15:30:00+01:00"}
        mode, since = sage.sweep_plan(recent, now, c, TZ)
        self.assertEqual((mode, since.isoformat()), ("incremental", "2026-10-12T14:30:00+01:00"))
        self.assertEqual(sage.sweep_plan(recent, now, c, TZ, force_deep=True)[0], "deep")
        week_old = dict(recent, last_deep_sweep="2026-10-06T08:15:00+01:00")
        self.assertEqual(sage.sweep_plan(week_old, now, c, TZ)[0], "deep")
        stale_run = {"last_deep_sweep": "2026-10-12T08:15:00+01:00", "last_run": "2026-10-01T09:00:00+01:00"}
        self.assertEqual(sage.sweep_plan(stale_run, now, c, TZ)[1].date().isoformat(), "2026-10-10")

    def test_planning_window_defaults_to_today_plus_three_working_days(self):
        days = sage.planning_days(sage.parse_dt("2026-10-15T08:15:00+01:00", TZ), cfg(), TZ)
        self.assertEqual([d.isoformat() for d in days], ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"])

    def test_truncated_copy_of_a_title_is_not_a_rename(self):
        long = "\U0001F33F Focus: Quick hits (3): Reply to Jo (Globex): book the…; Answer Sam's comment on the…"
        self.assertTrue(sage.same_title(long, long[:60] + "…"))
        self.assertFalse(sage.same_title(long, "\U0001F33F Focus: My own words"))

    def test_inbox_lines_parse(self):
        got = sage.parse_inbox("- [ ] Draft Q4 plan ~60m !1 due:2026-10-10\n- [x] Old thing\nnot a task")
        self.assertEqual(len(got), 2)
        self.assertEqual((got[0]["title"], got[0]["minutes"], got[0]["priority"], got[0]["due"]),
                         ("Draft Q4 plan", 60, 1, "2026-10-10"))
        self.assertEqual(got[1]["status"], "done")

    def test_prep_for_a_meeting_that_started_goes_stale(self):
        h = Harness(self)
        prep = task("prep:thu1", "Prep forecast", 15, 2,
                    prep_for={"event_id": "thu1", "start": "2026-10-08T14:30:00+01:00", "title": "Forecast call"})
        h.run([prep])
        self.assertEqual(h.state["tasks"]["prep:thu1"]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
