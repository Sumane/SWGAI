# T07 — Review logged anomalies

When a skill sees something its walkthrough doesn't expect, it backs out
(ESC a few times), carries on, and leaves the evidence behind:

    data/live/anomalies/index.jsonl                 one JSON line per anomaly
    data/live/anomalies/<time>_<skill>_<reason>/
        anomaly.json                                what was expected, what was seen
        NN_<step>.jpg / NN_<step>.json              every screenshot of that run + its OCR boxes

So far only `clean_waypoints` writes these. Reasons it can log:

| reason | means |
|---|---|
| datapad_not_found | Ctrl+D didn't show a window titled "Datapad" |
| confirm_mismatch | the "Really destroy …?" box was missing or named another waypoint |
| confirm_still_open | Yes was clicked but the box stayed up |
| still_listed | the box closed but the waypoint is still in the grid |
| label_came_back | the same waypoint was picked twice in one run |
| could_not_close | the datapad was still open after the toggle key and 3 ESCs |
| exception | the skill crashed (error in anomaly.json) |

## Your job

For each new line in index.jsonl (keep a `reviewed.jsonl` of the ones done):

1. Open the step images in order, with their OCR json.
2. Say which step went wrong and why: wrong thing clicked (compare click
   target with where the button really is), OCR misread, a window in the
   way, game lag, etc.
3. Write `review.md` in the anomaly folder: what happened, the evidence
   (file + pixel coordinates), and a proposed fix (code change or a
   setting), with your confidence.
4. Do not change code yourself; Marc or Claude apply fixes.

Example (7 Nov, before this logging existed): the bot clicked a "yes" at
(522, 473) — the datapad's "Active: Yes" detail — instead of the dialog's
Yes at (1330, 730), and retried the same waypoint six times.
