# Using Sage with other calendars, email, chat, trackers and CRMs

Sage never talks to any service itself. Claude does, through the user's own connectors, and the
engine only ever says *what* should change. So supporting another tool means telling Claude how that
tool says the same thing. That's what this file is for.

**Tested every day:** Google Calendar, Gmail, Slack, Jira, HubSpot, and call notes arriving as Gemini
or Gong emails. **Designed for, not yet tested:** everything else below. Setup's dry run writes nothing
until the user says yes, and a non-Google calendar gets a round-trip test first (SETUP.md, step 4b), so
a mismatch shows up before it can do any harm.

## What Sage needs from a calendar

- **Read:** every event in a window, with its id, title, start and end (or all-day date), the user's own
  response (accepted, declined, none), whether it shows them as free, whether anyone external is
  invited, its description, and its colour or category.
- **Write:** create, update and delete single events on the user's own calendar, with a title, start
  and end in a time zone, an all-day option, a description, a colour or category, private or normal,
  free or busy, and **no attendees**. If the tool can send notifications, they're off.
- **The description must round-trip.** Every Sage event carries `sage:v1 kind=<kind> keys=<a,b>` in its
  description, and that marker is how Sage tells its own blocks from the user's. If a calendar strips
  or rewrites descriptions, Sage can't run safely on it. Don't switch it on.

## The `change` on every op

Each op in `plan.json` has `args`, shaped exactly for the Google Calendar connector, and `change`,
which says the same thing in neutral terms. Any other calendar is driven from `change`. An update's
`change` lists only the fields that change.

| `change` field | Meaning | Google Calendar (tested) | Microsoft 365 / Outlook (untested) |
|---|---|---|---|
| `title` | Event title | `summary` | `subject` |
| `start`, `end`, `time_zone` | Timed event, ISO 8601 with offset | `startTime`, `endTime`, `timeZone` | `start` / `end` as `{dateTime, timeZone}` |
| `all_day`, `start_date`, `end_date` | All-day event; `end_date` is exclusive | `allDay` with UTC midnights (the `args` already do this) | `isAllDay`, with midnight `start` / `end` in the calendar's time zone |
| `description_html` | Description, HTML | `description` | `body` with `contentType: HTML` |
| `colour` | `sage` or `done` | `colorId` 2 / 8 | Categories: one called "Sage" (green) and one called "Sage done" (grey) |
| `private` | Hide details from colleagues | `visibility: private` / `default` | `sensitivity: private` / `normal` |
| `show_as` | `free` or `busy` | `availability` | `showAs: free` / `busy` |
| `reminder_minutes` | Popup minutes before; `null` = none | `overrideReminders` / `useDefaultReminders: false` | `isReminderOn` and `reminderMinutesBeforeStart` |

The Outlook column uses Microsoft Graph's event field names. A connector's tools may name or nest them
differently: map onto whatever the tool's own schema shows, and write down what you chose.

**No colours or categories?** Set `"done_by_colour": false` in `config.json`. Ticking still works by
putting ✓ in a block's title or sending its "Mark done" email.

## connectors.md in the data folder

When anything differs from the tested set, setup writes `<data_dir>/connectors.md`, and every run
follows it. Keep it short and exact:

```
# Sage connectors
calendar: Microsoft 365 (Outlook)
  list: <exact tool name> · create: <tool> · update: <tool> · delete: <tool>
  mapping: title -> subject; start/end/time_zone -> start/end {dateTime, timeZone}; ...
  colour: categories "Sage" / "Sage done"
  events.json: self_response from <field>; transparent when showAs is free; colour from categories
  round-trip test: passed <date> (marker, category, private and free all came back)
email: Outlook mail · search with <tool> · syntax notes · link = <field>
chat: Microsoft Teams · mentions <tool>, chats <tool>   (or: none)
tasks: Linear · assigned issues <tool> · link = issue URL
calls: Fireflies · action items <tool>
crm (setup only): Salesforce · open opportunities owned by or shared with the user
extra_link_hosts added: crm.example.com
```

## Email, chat, task trackers and call notes

The runbook's queries are written for the tested set. With anything else, do the same job with the
equivalent tool, translating the *intent* of each query into that tool's own search syntax (check the
tool's description for it), and key tasks `<tool>:<id>` with the item's own link.

| Job | Tested | Others |
|---|---|---|
| Who's waiting on the user, doc comments, their own promises, "Sage done" ticks | Gmail | Outlook mail or any mail connector with search: key `outlook:<id>`, link its web URL |
| Mentions and direct messages | Slack | Microsoft Teams, or skip chat entirely |
| Work assigned to the user | Jira | Linear, Asana, ClickUp, Monday, GitHub issues: assigned to them, not done, updated since the look-back or due within 7 days |
| Call action items | Gong, plus Gemini and Gong emails | Fireflies, Granola, Otter, Zoom or Teams recaps: only items that name the user |

## CRM, for account tiers (setup only)

Sage reads a CRM once, at setup, to draft the account tiers. It never writes to it.

- **HubSpot:** tested; SETUP.md step 5.
- **Salesforce:** open opportunities where the user is the owner or on the opportunity team, with close
  date, stage and forecast category.
- **Pipedrive, Attio or any other CRM:** open deals the user owns, with close date and stage.
- **No CRM:** ask for their top three to five accounts and roughly where the rest sit.

## Links

The engine keeps only links on hosts it knows, so a block can't link somewhere a message tried to send
the user. The built-in list covers Google, Microsoft 365, Slack, Atlassian, HubSpot, Salesforce,
Linear, Asana, ClickUp, Monday, Pipedrive, Attio, Gong, Fireflies, Granola, Otter, Zoom, Notion and
GitHub. Add anything else (a self-hosted Jira, your own CRM's domain) to `extra_link_hosts` in
`config.json`.

## One-click ticks

`tick-webapp/` is a Google Apps Script, so it needs a Google account. On any other setup the "Mark
done" link is an email the user sends to themselves, which works with any mail provider.
