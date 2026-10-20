#!/usr/bin/env python3
"""Berenjak Soho table watcher (SevenRooms).
Sends an ntfy push when a watched dinner slot becomes bookable."""

import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------- CONFIG ----------------
VENUE = "berenjakjks"            # SevenRooms id for Berenjak Soho
PARTY_SIZE = 2
WATCH_WEEKDAYS = {4, 5}          # Mon=0 ... Sun=6 -> Friday & Saturday
EARLIEST = "18:30"               # first start time to watch
LATEST = "21:00"                 # last start time to watch
WEEKS_AHEAD = 8
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = Path(__file__).with_name("berenjak_state.json")
# ----------------------------------------

API = ("https://www.sevenrooms.com/api-yoa/availability/ng/widget/range?venue={venue}"
       "&party_size={party}&halo_size_interval=100&start_date={d}&num_days=1"
       "&channel=SEVENROOMS_WIDGET&exclude_pdr=true")
BOOK_URL = ("https://www.sevenrooms.com/explore/{venue}/reservations/create/search/"
            "?party_size={party}&date={d}&start_time={t}&venues={venue}")
LONDON = ZoneInfo("Europe/London")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": f"https://www.sevenrooms.com/explore/{VENUE}/reservations/create/search/",
}


def fetch_times(d):
    url = API.format(venue=VENUE, party=PARTY_SIZE, d=d.isoformat())
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as r:
        j = json.load(r)
    shifts = ((j.get("data") or {}).get("availability") or {}).get(d.isoformat(), []) or []
    return [t for s in shifts if not s.get("is_closed") for t in (s.get("times") or [])]


def main():
    now = datetime.now(LONDON)
    try:
        previous = set(json.loads(STATE_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        previous = set()

    avail, failed, checked = {}, [], 0
    for i in range(WEEKS_AHEAD * 7):
        d = now.date() + timedelta(days=i)
        if d.weekday() not in WATCH_WEEKDAYS:
            continue
        checked += 1
        try:
            times = fetch_times(d)
        except Exception as e:
            print(f"WARN {d}: {e}", file=sys.stderr)
            failed.append(f"{d.isoformat()}|")
            continue
        for t in times:
            hhmm = t.get("time", "")
            if t.get("type") != "book" or not (EARLIEST <= hhmm <= LATEST):
                continue
            if datetime.fromisoformat(f"{d.isoformat()} {hhmm}").replace(tzinfo=LONDON) <= now:
                continue
            avail[f"{d.isoformat()}|{hhmm}"] = {
                "date": d.isoformat(), "time": hhmm,
                "area": t.get("public_time_slot_description") or "",
            }
        time.sleep(0.5)

    keep = {k for k in previous if any(k.startswith(f) for f in failed)}
    new = [avail[k] for k in sorted(set(avail) - previous)]
    print(f"[{now:%Y-%m-%d %H:%M}] bookable: {len(avail)}, newly opened: {len(new)}")

    if new:
        by_day = {}
        for x in new:
            by_day.setdefault(x["date"], []).append(x)
        lines = []
        for ds, xs in sorted(by_day.items()):
            areas = sorted({x["area"] for x in xs if x["area"]})
            area = f" ({', '.join(areas)})" if areas else ""
            lines.append(f"{date.fromisoformat(ds).strftime('%a %d %b')}: "
                         f"{', '.join(x['time'] for x in xs)}{area}")
        body = "\n".join(lines)
        title = f"Berenjak Soho: table for {PARTY_SIZE} just opened"
        click = BOOK_URL.format(venue=VENUE, party=PARTY_SIZE, d=new[0]["date"], t=new[0]["time"])
        print(title + "\n" + body)
        if NTFY_TOPIC:
            req = urllib.request.Request(
                f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"), method="POST",
                headers={"Title": title, "Priority": "high", "Tags": "fork_and_knife",
                         "Click": click})
            urllib.request.urlopen(req, timeout=20).read()
        else:
            print("NTFY_TOPIC not set - no push sent", file=sys.stderr)

    STATE_FILE.write_text(json.dumps(sorted(set(avail) | keep), indent=1) + "\n")

    if checked and len(failed) == checked:
        sys.exit("ERROR: every request to SevenRooms failed")


if __name__ == "__main__":
    main()
