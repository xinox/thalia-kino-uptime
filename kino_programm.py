"""
Modul 1: Programm besorgen.

Liest das aktuelle Programm von kino-bous.de. Die Seite /programm enthält das
komplette Programm als JavaScript-Objekt  `var programm = {...};`  (JSON).
Pro Vorstellung stehen dort u.a. die kinoheld-showId und das Reservierungs-
fenster (resab/resbis).

Keine Abhängigkeit zum Tester-Modul. Standalone:
    python3 kino_programm.py            # Programm als Tabelle ausgeben
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

PROGRAM_URL = "https://www.kino-bous.de/programm"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) kino-uptime-monitor"


@dataclass(frozen=True)
class Showing:
    show_id: Optional[str]          # kinoheld showId, None = kein Online-Ticket
    film_id: str
    title: str
    start: datetime                 # lokale Zeit (Europe/Berlin), naiv
    saal: str
    res_from: Optional[datetime]    # laut Website: Reservierung ab
    res_until: Optional[datetime]   # laut Website: Reservierung bis
    ticket_url: str


class ProgramError(Exception):
    pass


def fetch_program(url: str = PROGRAM_URL, timeout: int = 20) -> List[Showing]:
    """Lädt die Programmseite und gibt alle Vorstellungen zurück."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise ProgramError(f"Programmseite nicht ladbar: {type(e).__name__}: {e}") from e
    return parse_program(html)


def parse_program(html: str) -> List[Showing]:
    marker = "var programm ="
    i = html.find(marker)
    if i < 0:
        raise ProgramError("'var programm' nicht in der Seite gefunden (Seitenaufbau geändert?)")
    start = html.index("{", i)
    try:
        data, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError as e:
        raise ProgramError(f"Programm-JSON nicht lesbar: {e}") from e

    showings: List[Showing] = []
    for film in (data.get("filme") or {}).values():
        fakten = film.get("filmfakten") or {}
        title = _s(fakten.get("titel")) or "?"
        film_id = _s(fakten.get("idf"))
        for vorst in _as_list(film.get("vorstellungen")):
            for termin in _iter_termine(vorst.get("termine")):
                try:
                    begin = datetime.strptime(f"{termin['datum']} {termin['zeit']}", "%Y-%m-%d %H:%M")
                except (KeyError, ValueError):
                    continue
                showings.append(Showing(
                    show_id=_extract_show_id(termin),
                    film_id=film_id,
                    title=title,
                    start=begin,
                    saal=_s(termin.get("saal_bezeichnung")) or _s(termin.get("saal")),
                    res_from=_parse_res(termin.get("resab")),
                    res_until=_parse_res(termin.get("resbis")),
                    ticket_url=_s(termin.get("link_fixticket")),
                ))
    showings.sort(key=lambda s: (s.start, s.title))
    return showings


# --- Hilfsfunktionen -------------------------------------------------------

def _s(v) -> str:
    """Leere Felder kommen im JSON als {} – in '' umwandeln."""
    return v if isinstance(v, str) else ""


def _as_list(v):
    if isinstance(v, list):
        return v
    return [v] if isinstance(v, dict) else []


def _iter_termine(termine):
    for t in (termine.values() if isinstance(termine, dict) else _as_list(termine)):
        yield from (x for x in _as_list(t))


def _extract_show_id(termin: dict) -> Optional[str]:
    m = re.search(r"showId:\s*&apos;(\d+)&apos;", _s(termin.get("kinoheldEframeSettings")))
    if m:
        return m.group(1)
    qs = urllib.parse.urlparse(_s(termin.get("link_fixticket"))).query
    sid = urllib.parse.parse_qs(qs).get("showId", [""])[0]
    return sid if sid.isdigit() else None


def _parse_res(v) -> Optional[datetime]:
    try:
        return datetime.strptime(_s(v).replace("*", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


if __name__ == "__main__":
    shows = fetch_program()
    films = {s.title for s in shows}
    print(f"{len(shows)} Vorstellungen, {len(films)} Filme\n")
    for s in shows:
        rf = s.res_from.strftime("%d.%m. %H:%M") if s.res_from else "-"
        print(f"{s.start:%a %d.%m. %H:%M}  {s.saal:<7} showId={s.show_id or '-':<6} Res. ab {rf:<12} {s.title}")
