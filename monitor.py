#!/usr/bin/env python3
"""
Verbindet beide Module: lädt bei jedem Durchlauf das aktuelle Programm neu
(neue Filme kommen automatisch dazu, alte fallen weg) und testet jede
Vorstellung, deren Reservierungsfenster gerade offen ist.

    python3 monitor.py                 # Endlosschleife, alle 5 min
    python3 monitor.py --once          # ein Durchlauf (z.B. für cron / Aufgabenplanung)
    python3 monitor.py --report        # Uptime-Auswertung aus der CSV
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import timedelta

from kino_programm import PROGRAM_URL, ProgramError, fetch_program
from reservation_tester import COUNTS_FOR_UPTIME, CheckResult, ReservationTester, now_local

FIELDS = list(CheckResult(ts="", show_id="", title="", start="", status="").as_row().keys())


def push_kuma(push_url: str, status: str, msg: str, ping: "int | None" = None, timeout: int = 10) -> None:
    """Schickt einen Heartbeat an einen Uptime-Kuma Push-Monitor. Fehler dabei
    dürfen den Check-Lauf nicht abbrechen, deshalb wird hier nur gewarnt."""
    if not push_url:
        return
    params = {"status": status, "msg": msg}
    if ping is not None:
        params["ping"] = ping
    url = f"{push_url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            pass
    except Exception as e:
        print(f"  WARNUNG: Kuma-Push fehlgeschlagen: {type(e).__name__}: {e}")


def write_github_summary(text: str) -> None:
    """Hängt Markdown an die GitHub-Actions Job-Summary an (sichtbar oben auf der
    Lauf-Seite statt nur im rohen Log). Außerhalb von Actions ist die Variable
    nicht gesetzt, dann passiert nichts."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def run_once(tester, args, state) -> bool:
    """Führt einen Durchlauf aus. Rückgabewert: True = alles UP (oder nichts zu
    testen), False = mindestens ein DOWN oder das Programm konnte nicht geladen
    werden – wird für den Exit-Code bei --once genutzt (Actions-Badge)."""
    now = now_local()
    try:
        program = fetch_program(args.program_url)
    except ProgramError as e:
        print(f"{now:%Y-%m-%d %H:%M:%S} PROGRAMM_FEHLER {e}")
        log(args.csv, CheckResult(ts=now.isoformat(timespec="seconds"), show_id="", title="",
                                  start="", status="PROGRAMM_FEHLER", error=str(e)[:200]))
        push_kuma(args.kuma_push_url, "down", f"Programm nicht ladbar: {e}"[:200])
        write_github_summary(f"### ❌ Programm nicht ladbar\n\n{e}")
        return False

    # Änderungen im Programm melden
    titles = {s.title for s in program}
    if state.get("titles") is not None:
        for t in sorted(titles - state["titles"]):
            print(f"  + neuer Film:  {t}")
        for t in sorted(state["titles"] - titles):
            print(f"  - entfernt:    {t}")
    state["titles"] = titles

    counts = defaultdict(int)
    latencies = []
    for s in program:
        r = tester.check(s.show_id, s.title, s.start, s.res_from, s.res_until)
        counts[r.status] += 1
        if r.latency_ms is not None:
            latencies.append(r.latency_ms)
        if not r.status.startswith("SKIP") or args.log_skips:
            log(args.csv, r)
        if r.status != "UP" and not r.status.startswith("SKIP"):
            print(f"  {r.status:<13} {s.start:%d.%m. %H:%M} {s.title} (showId {s.show_id}) {r.error}")
        if r.widget_http is not None:          # nur nach echten Requests pausieren
            time.sleep(args.pause)

    up, down = counts["UP"], counts["DOWN"]
    tested = up + down
    pct = f"{100 * up / tested:.0f} %" if tested else "–"
    print(f"{now:%Y-%m-%d %H:%M:%S}  {len(program)} Vorstellungen / {len(titles)} Filme | "
          f"getestet {tested}: UP {up}, DOWN {down} ({pct}) | "
          f"nicht buchbar {counts['NICHT_BUCHBAR']} | "
          f"übersprungen {sum(v for k, v in counts.items() if k.startswith('SKIP'))}")

    latencies.sort()
    median_latency = latencies[len(latencies) // 2] if latencies else None
    push_kuma(args.kuma_push_url, "down" if down else "up",
              f"{tested} getestet: {up} UP, {down} DOWN | nicht buchbar {counts['NICHT_BUCHBAR']} | "
              f"{len(program)} Vorstellungen / {len(titles)} Filme", median_latency)

    status_icon = "✅" if down == 0 else "❌"
    write_github_summary(
        f"### {status_icon} Kino Bous Uptime-Check — {now:%Y-%m-%d %H:%M:%S}\n\n"
        f"- Vorstellungen: {len(program)} / Filme: {len(titles)}\n"
        f"- Getestet: {tested} → UP {up}, DOWN {down} ({pct})\n"
        f"- Nicht buchbar: {counts['NICHT_BUCHBAR']}\n"
        f"- Übersprungen: {sum(v for k, v in counts.items() if k.startswith('SKIP'))}\n"
        + (f"- Median-Latenz: {median_latency} ms\n" if median_latency is not None else "")
    )
    return down == 0


def log(path: str, r: CheckResult) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(r.as_row())


def report(path: str) -> None:
    if not os.path.exists(path):
        print("Noch keine Daten.")
        return
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    tested = [r for r in rows if r["status"] in COUNTS_FOR_UPTIME]
    if not tested:
        print("Noch keine auswertbaren Checks.")
        return
    up = sum(r["status"] == "UP" for r in tested)
    lat = sorted(int(r["latency_ms"]) for r in tested if r["latency_ms"])
    print(f"Zeitraum:  {rows[0]['ts']} – {rows[-1]['ts']}")
    print(f"Uptime:    {100 * up / len(tested):.2f} %  ({len(tested)} Checks, {len(tested) - up} DOWN)")
    if lat:
        print(f"Latenz:    Median {lat[len(lat) // 2]} ms, max {lat[-1]} ms")
    print(f"Nicht buchbar: {sum(r['status'] == 'NICHT_BUCHBAR' for r in rows)}  |  "
          f"Programm-Fehler: {sum(r['status'] == 'PROGRAMM_FEHLER' for r in rows)}")

    per_film = defaultdict(lambda: [0, 0])
    for r in tested:
        per_film[r["title"]][0] += r["status"] == "UP"
        per_film[r["title"]][1] += 1
    print("\nPro Film:")
    for t, (u, n) in sorted(per_film.items(), key=lambda x: x[1][0] / x[1][1]):
        print(f"  {100 * u / n:6.1f} %  ({n:>4} Checks)  {t}")

    downs = [r for r in tested if r["status"] == "DOWN"][-10:]
    if downs:
        print("\nLetzte Ausfälle:")
        for r in downs:
            print(f"  {r['ts']}  showId {r['show_id']}  {r['title']}: {r['error']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uptime-Monitor Sitzplatzreservierung Kino Bous")
    ap.add_argument("--interval", type=int, default=300, help="Sekunden zwischen Durchläufen (Default 300)")
    ap.add_argument("--pause", type=float, default=1.0, help="Sekunden Pause zwischen zwei Vorstellungen")
    ap.add_argument("--margin-open", type=int, default=2, help="Minuten Puffer nach Verkaufsstart")
    ap.add_argument("--margin-close", type=int, default=2, help="Minuten Puffer vor Verkaufsende")
    ap.add_argument("--csv", default="kino_uptime.csv")
    ap.add_argument("--program-url", default=PROGRAM_URL)
    ap.add_argument("--log-skips", action="store_true", help="auch übersprungene Vorstellungen loggen")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--kuma-push-url", default=os.environ.get("KUMA_PUSH_URL", ""),
                     help="Uptime-Kuma Push-Monitor-URL (Default: Umgebungsvariable KUMA_PUSH_URL). "
                          "Leer = kein Push.")
    args = ap.parse_args()

    if args.report:
        return report(args.csv)

    tester = ReservationTester(margin_after_open=timedelta(minutes=args.margin_open),
                               margin_before_close=timedelta(minutes=args.margin_close))
    state: dict = {"titles": None}
    while True:
        started = time.monotonic()
        ok = run_once(tester, args, state)
        if args.once:
            sys.exit(0 if ok else 1)
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
