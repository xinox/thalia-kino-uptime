"""
Modul 2: Reservierung testen.

Prüft für EINE Vorstellung (kinoheld showId), ob die Sitzplatzreservierung
funktioniert. Kennt das Programm-Modul nicht – bekommt nur showId und optional
Zeitfenster übergeben.

Ablauf pro Vorstellung:
  1. Liegt "jetzt" außerhalb des Reservierungsfensters der Website (resab/resbis)?
     -> SKIP, es wird gar nicht erst angefragt.
  2. Widget-Seite laden (muss HTTP 200 sein). Darin stehen die echten
     kinoheld-Verkaufszeiten saleStart/saleEnd (i.d.R. ~10 min vor Beginn).
     Liegt "jetzt" außerhalb davon -> SKIP.
  3. Sitzplan-API /ajax/getSeats: HTTP 200 + mindestens 1 Sitz -> UP, sonst DOWN.
     (Außerhalb des Fensters liefert die API HTTP 400 – deshalb Schritt 1+2.)

Status-Werte:
  UP, DOWN                 zählen für die Uptime
  NICHT_BUCHBAR            kinoheld meldet isBookable=false (z.B. ausverkauft/gesperrt)
  SKIP_NOCH_NICHT          Vorverkauf hat noch nicht begonnen
  SKIP_GESCHLOSSEN         Film läuft schon / zu wenig Vorlaufzeit
  SKIP_KEIN_ONLINETICKET   keine showId (nur Kinokasse)

Standalone:
    python3 reservation_tester.py 30718
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional, Tuple

BASE = "https://www.kinoheld.de"
CINEMA_ID = "1198"                                    # kinoheld-cid Thalia Lichtspiele Bous
CINEMA_PATH = "Kino-Bous/Thalia%20Lichtspiele%20Bous"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) kino-uptime-monitor"

COUNTS_FOR_UPTIME = ("UP", "DOWN")


def now_local() -> datetime:
    """Aktuelle Zeit in Europe/Berlin (naiv), damit es auch auf UTC-Servern stimmt."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)
    except Exception:  # z.B. Windows ohne tzdata-Paket -> Systemzeit
        return datetime.now()


@dataclass
class CheckResult:
    ts: str
    show_id: str
    title: str
    start: str
    status: str
    widget_http: Optional[int] = None
    seats_http: Optional[int] = None
    seats_total: int = 0
    seats_free: int = 0
    latency_ms: Optional[int] = None
    sale_start: str = ""
    sale_end: str = ""
    error: str = ""

    def as_row(self) -> dict:
        return asdict(self)


class ReservationTester:
    def __init__(self, cinema_id: str = CINEMA_ID, cinema_path: str = CINEMA_PATH,
                 margin_after_open: timedelta = timedelta(minutes=2),
                 margin_before_close: timedelta = timedelta(minutes=2),
                 timeout: int = 15, user_agent: str = USER_AGENT):
        self.cinema_id = cinema_id
        self.cinema_path = cinema_path
        self.margin_open = margin_after_open      # Puffer nach Verkaufsstart
        self.margin_close = margin_before_close   # Puffer vor Verkaufsende
        self.timeout = timeout
        self.user_agent = user_agent

    # -- öffentliche API ----------------------------------------------------

    def check(self, show_id: Optional[str], title: str = "", start: Optional[datetime] = None,
              res_from: Optional[datetime] = None, res_until: Optional[datetime] = None,
              now: Optional[datetime] = None) -> CheckResult:
        now = now or now_local()
        r = CheckResult(ts=now.isoformat(timespec="seconds"), show_id=show_id or "",
                        title=title, start=start.isoformat(timespec="minutes") if start else "",
                        status="")

        if not show_id:
            r.status = "SKIP_KEIN_ONLINETICKET"
            return r

        # 1) Grobfilter über Website-Daten (spart Requests)
        skip = self._window_status(now, res_from, res_until)
        if skip:
            r.status = skip
            return r

        # 2) Widget-Seite + echte Verkaufszeiten
        try:
            r.widget_http, html = self._get(self._widget_url(show_id))
        except Exception as e:
            return self._down(r, f"Widget: {e}")
        if r.widget_http != 200:
            return self._down(r, f"Widget HTTP {r.widget_http}")

        meta = self._parse_show_meta(html, show_id)
        if meta is None:
            return self._down(r, "Show-Daten nicht in Widget-Seite gefunden")
        sale_start, sale_end = _dt(meta.get("saleStart")), _dt(meta.get("saleEnd"))
        r.sale_start = sale_start.isoformat() if sale_start else ""
        r.sale_end = sale_end.isoformat() if sale_end else ""

        skip = self._window_status(now, sale_start, sale_end)
        if skip:
            r.status = skip
            return r
        if meta.get("isBookable") is False:
            r.status = "NICHT_BUCHBAR"
            return r

        # 3) Sitzplan-API
        q = urllib.parse.urlencode({"cid": self.cinema_id, "showId": show_id, "mode": "widget", "rb": "1"})
        t0 = time.perf_counter()
        try:
            r.seats_http, body = self._get(f"{BASE}/ajax/getSeats?{q}", ajax=True)
        except Exception as e:
            return self._down(r, f"getSeats: {e}")
        r.latency_ms = round((time.perf_counter() - t0) * 1000)
        if r.seats_http != 200:
            return self._down(r, f"getSeats HTTP {r.seats_http}")
        try:
            seats = (json.loads(body).get("seats") or {})
        except ValueError:
            return self._down(r, "getSeats: kein gültiges JSON")
        r.seats_total = len(seats)
        r.seats_free = sum(1 for s in seats.values() if s.get("status") == "sf")  # sf=frei, ss=belegt
        if not seats:
            return self._down(r, "getSeats: Sitzplan leer")
        r.status = "UP"
        return r

    # -- intern -------------------------------------------------------------

    def _window_status(self, now, open_at, close_at) -> Optional[str]:
        if open_at and now < open_at + self.margin_open:
            return "SKIP_NOCH_NICHT"
        if close_at and now > close_at - self.margin_close:
            return "SKIP_GESCHLOSSEN"
        return None

    def _widget_url(self, show_id: str) -> str:
        return (f"{BASE}/{self.cinema_path}/show/{show_id}?mode=widget&layout=movies"
                f"&lang=de&showId={show_id}&rb=1&change=0")

    def _get(self, url: str, ajax: bool = False) -> Tuple[int, str]:
        headers = {"User-Agent": self.user_agent}
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:   # 4xx/5xx sind Ergebnisse, keine Abbrüche
            return e.code, ""

    @staticmethod
    def _parse_show_meta(html: str, show_id: str) -> Optional[dict]:
        """Sucht im Widget-HTML das Objekt  "show":{"id":"<showId>", ...}."""
        key = f'"show":{{"id":"{show_id}"'
        i = html.find(key)
        if i < 0:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(html, i + len('"show":'))
            return obj
        except ValueError:
            return None

    @staticmethod
    def _down(r: CheckResult, msg: str) -> CheckResult:
        r.status = "DOWN"
        r.error = msg[:200]
        return r


def _dt(v) -> Optional[datetime]:
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S") if isinstance(v, str) else None
    except ValueError:
        return None


if __name__ == "__main__":
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else input("showId: ").strip()
    res = ReservationTester().check(sid)
    for k, v in res.as_row().items():
        print(f"{k:>12}: {v}")
