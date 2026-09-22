# Kino-Uptime-Monitor – Thalia Lichtspiele Bous

Prüft, ob die Online-Sitzplatzreservierung (kinoheld-Widget) auf
[kino-bous.de](https://www.kino-bous.de) für alle aktuell im Programm befindlichen
Vorstellungen funktioniert. Das Programm wird bei jedem Lauf neu von der Website
geladen – neue Filme und wegfallende Vorstellungen werden automatisch berücksichtigt,
ohne dass das Script angepasst werden muss.

Reservieren/Buchen geht nur innerhalb des jeweiligen Verkaufsfensters (Vorverkaufsstart
bis ca. 10 Minuten vor Filmbeginn). Vorstellungen außerhalb dieses Fensters werden
übersprungen und zählen nicht als Ausfall.

Reine Python-Standardbibliothek, keine Abhängigkeiten, Python ≥ 3.9.

## Module

- [`kino_programm.py`](kino_programm.py) – lädt und parst das aktuelle Programm von
  `kino-bous.de/programm`.
- [`reservation_tester.py`](reservation_tester.py) – prüft für eine einzelne
  Vorstellung (kinoheld showId), ob die Sitzplatzreservierung funktioniert.
- [`monitor.py`](monitor.py) – verbindet beide Module, loggt nach CSV und kann einen
  Heartbeat an einen [Uptime-Kuma](https://github.com/louislam/uptime-kuma)
  Push-Monitor schicken.

## Lokale Nutzung

```bash
python kino_programm.py            # aktuelles Programm als Tabelle
python reservation_tester.py 30718 # eine Vorstellung testen (showId)
python monitor.py --once           # ein kompletter Durchlauf, loggt nach kino_uptime.csv
python monitor.py --report         # Uptime-Auswertung aus der CSV
```

## Hosting: GitHub Actions + Uptime Kuma

Der Workflow [`.github/workflows/uptime.yml`](.github/workflows/uptime.yml) führt
`monitor.py --once` alle 15 Minuten aus und schickt am Ende einen Heartbeat
(Status, Zusammenfassung, Median-Latenz) an einen Uptime-Kuma Push-Monitor. Kuma
übernimmt damit Uptime-%, Verlaufsgrafik, Status-Page und Benachrichtigungen – im
Repo selbst wird keine Historie gespeichert (kein wachsendes CSV, keine Bot-Commits).

Bleibt ein Heartbeat aus (weil z.B. der Actions-Lauf fehlschlägt oder GitHub den
Workflow nach 60 Tagen Repo-Inaktivität automatisch deaktiviert hat), erkennt Kuma
das selbst über sein "erwartetes Intervall" und schlägt ebenfalls Alarm.

### Einrichtung

1. In Uptime Kuma einen **Push-Monitor** anlegen (z.B. "Sitzplatzreservierung Kino
   Bous"). Erwartetes Intervall mit etwas Puffer setzen, z.B. 20–25 Minuten, damit
   gelegentliche Verzögerungen im GitHub-Actions-Scheduler keinen Fehlalarm
   auslösen. Benachrichtigungen (E-Mail, Telegram, Discord, …) dort wie gewohnt
   konfigurieren.
2. Die Push-URL des Monitors (Format
   `https://<kuma-host>/api/push/<token>`) als Repository-Secret
   **`KUMA_PUSH_URL`** hinterlegen: *Settings → Secrets and variables → Actions →
   New repository secret*.
3. Der Workflow läuft danach automatisch alle 15 Minuten. Für einen sofortigen
   Testlauf: *Actions → Uptime Check → Run workflow*.

Ohne gesetztes `KUMA_PUSH_URL`-Secret läuft der Check trotzdem durch (nur ohne
Kuma-Heartbeat) – nützlich zum Testen des Workflows selbst.

## Bekannte Einschränkungen

- Kuma trackt nur den Gesamtstatus des Reservierungssystems pro Lauf, keine
  Uptime-Historie pro Film. Details zu einem konkreten DOWN (welche Vorstellung,
  welcher Fehler) stehen im jeweiligen GitHub-Actions-Lauf-Log.
- `NICHT_BUCHBAR` (z.B. ausverkauft oder gesperrt) zählt bewusst nicht als Ausfall
  und fließt nicht in den Kuma-Status ein.
