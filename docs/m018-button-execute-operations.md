# M0.18 – Opt-in-Betrieb der sicheren GPIO-Transporttasten

## Geltungsbereich

Die sichere Button-Ausführung ist seit INP-01c (PR #12) in `main`: PLAY/PAUSE/RESUME/STOP/NEXT/PREVIOUS laufen über den Selected-Target-Executor und dessen Fail-Closed-Policy. Die **versionierte** `systemd/sonoscdplayer-buttons.service` bleibt absichtlich im Diagnosemodus. Ein Git-Update oder Merge **aktiviert `--execute` nicht**.

Die Produktivfreigabe ist ein separater, gerätespezifischer Betriebsentscheid. Dieses Dokument enthält **keine** Geräte-UIDs, Adressen, Produktionskonfigurationen oder direkt einsetzbare `ExecStart`-Zeile. Der private Entwicklungs-Tracker gehört nicht ins Repository.

## Freigabevoraussetzungen

1. `main` ist synchronisiert, Working Tree sauber; vollständige Offline-Regression bestanden.
2. Player, OLED und Buttons sind aktiv. Laufender Button-Prozess und effektive systemd-Units sind gelesen und auf unerwartete Flags geprüft.
3. `status` meldet `STOPPED`, `owned=false`, `stream.active=false`, `group_cleanup_pending=false`; `get-target` meldet `selected=true`. Das Ziel wird zusätzlich in der OLED-Auswahl **eindeutig** kontrolliert, da die Read-only-Target-Summary keine UID/Namen preisgibt.
4. Alle bestehenden Drop-ins und der wirksame `ExecStart` wurden geprüft. Insbesondere optionale `--ui-events-socket`-Anbindung, Interpreter, `--config`, Benutzer und Working Directory müssen **unverändert** bleiben. Ein veralteter Dateiname allein rechtfertigt kein Löschen eines Drop-ins.
5. Ausdrückliche Freigabe für das Gerät und den Neustart **nur** von `sonoscdplayer-buttons.service`, inklusive Rückfallverfahren.

## Gezielte persistente Aktivierung

Die gerätespezifische Umsetzung kann über ein lokales persistentes Drop-in unter

`/etc/systemd/system/sonoscdplayer-buttons.service.d/zz-m018-execute.conf`

erfolgen. Es muss den **tatsächlich wirksamen** Button-`ExecStart` korrekt übernehmen und ausschliesslich die sicheren Opt-in-Flags `--execute --selected-target-execute` ergänzen. Systemd erlaubt kein einfaches Anhängen von Argumenten an einen bestehenden `ExecStart`: Es ist ein vollständiges Zurücksetzen und Neusetzen der Startanweisung notwendig. Deshalb **keine universelle, potentiell falsche `ExecStart`-Zeile ins Repository kopieren**.

Vor einem Button-Neustart: vorhandene lokale Konfiguration sichern; `sudo systemctl daemon-reload`; `systemctl show sonoscdplayer-buttons.service -p ExecStart -p DropInPaths` auswerten; effektive Flags auf `--gpio`, `--commands`, `--config`, ggf. `--ui-events-socket`, `--execute`, `--selected-target-execute` und Abwesenheit von `--uid-file`/`--selected-target-preview` überprüfen. Bei Widerspruch **nicht neu starten**.

Erst nach erfolgreicher Prüfung ausschliesslich `sudo systemctl restart sonoscdplayer-buttons.service`. Anschliessend `systemctl is-active`, `MainPID`, `NRestarts`, die echte Prozess-`cmdline` und gefilterte Startlogs prüfen. Player, OLED, gesamtes Target, private Player-Konfiguration und Shutdown-Sentinel unverändert lassen.

## Hardware-Abnahme

Nur nach bestätigter Solo-Zielauswahl und sicherem STOPPED-Ausgangszustand: einmal PLAY; auf ACK, hörbare Wiedergabe und `PLAYING/owned=true/stream.active=true` warten. Danach einmal NEXT, auf Quittung/Trackwechsel warten; abschliessend einmal STOP, auf ACK und `STOPPED/owned=false/stream.active=false/group_cleanup_pending=false` prüfen. Bei unbekanntem Ergebnis, Timeout oder inkonsistenter Ownership **keine wiederholten Tastendrücke und keinen Force-STOP**.

Ein erfolgreicher Button-Dienst-Neustart ist noch kein Nachweis für das Verhalten nach einem vollständigen Raspberry-Pi-Reboot. Boot-/Autostart-Validierung separat freigeben und prüfen.

## Rollback in Diagnosemodus

Wenn ausschliesslich `zz-m018-execute.conf` für diesen Cutover neu angelegt wurde:

```bash
sudo rm -f /etc/systemd/system/sonoscdplayer-buttons.service.d/zz-m018-execute.conf
sudo systemctl daemon-reload
sudo systemctl restart sonoscdplayer-buttons.service
```

Danach **den tatsächlichen Prozess** prüfen: `--execute` und `--selected-target-execute` müssen fehlen, ursprüngliche `--ui-events-socket`-Anbindung muss gegebenenfalls erhalten sein. Bei unsicherer Ausgangslage oder veränderter Drop-in-Struktur zuerst die gesicherten Originale prüfen und nicht blind Dateien löschen.

## Abnahmehistorie (ohne lokale Betriebsdaten)

- INP-01a/b/c auf kontrolliertem Solo-Ziel erfolgreich; INP-01c über PR #12, Merge `6f363ed` integriert.
- Post-Merge auf Raspberry Pi `main`: **784/784** Offline-Tests, **17,379 s**, Exit-Code 0 (07.11.2026).
- Nach gesonderter Freigabe lokaler persistenter Button-Cutover durchgeführt: wirksame Execute-Flags kontrolliert, Player/OLED aktiv, PLAY und STOP quittiert, NEXT-Trackwechsel 1 → 2 mit `PLAYING` und eigener aktiver Stream-Ownership bestätigt, STOP gab Ownership und Stream frei.
- **Noch offen:** vollständiger Reboot-/Autostarttest und separate Bereinigung historischer systemd-Drop-ins. Reale Fremdgruppen-, Timeout-, PAUSED-Trackwechsel- und Langzeitfälle wurden nicht durch diesen positiven Solo-Hardwarezyklus abgenommen.
