# Sage account priorities

Sage does work for higher tiers first. Move accounts between tiers whenever your focus changes; the next run picks it up.
One account per line, written like `- Acme Corp | acme.com, ACME | renewal at risk this quarter` (name | aliases, comma separated | why it matters).
Anything not listed counts as Tier 3. Keep the "why" short and free of deal values: colleagues who can see your calendar can read it in the block's description.
Short aliases (3 letters or fewer) only match whole words in capitals, so `UC` won't fire on "launch".

How the order works: each to-do scores its task priority (P1 30, P2 20, P3 10) + its account tier (Tier 1 +30, Tier 2 +15) + deadline pressure (overdue +25, due by next working day +20, within 3 working days +10). Highest score gets time first. A score of 60 or more is a must-do: it may run up to an hour past the daily cap and shrink rather than slip a day. All of these numbers live in config.json.

## Tier 1

## Tier 2

## Tier 3
