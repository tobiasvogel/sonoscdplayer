# Experimentelle OLED-Abschaltanzeige

Optionales Feature für SonosCDPlayer auf dem Raspberry Pi: Beim Ausschalten wird eine experimentelle „Safe to unplug“-Grafik angezeigt.

**Warnung:** Experimentell, Nutzung auf eigene Gefahr. Die Grafik erscheint technisch **vor** dem endgültigen Ausschalten. Erst nach der vollständig abgeschlossenen ACT-LED-Abschaltsequenz die Stromversorgung trennen. Die 3 Sekunden Verzögerung sind **keine** Sicherheitsgarantie.

Im Projektverzeichnis:

```sh
# Status anzeigen
sh tools/manage_experimental_exitrd.sh --status

# Einmalig dauerhaft installieren (interaktive Bestätigung erforderlich)
sh tools/manage_experimental_exitrd.sh --install

# Vollständig deaktivieren und deinstallieren
sh tools/manage_experimental_exitrd.sh --uninstall
```

Die Installation erstellt das benötigte Binary, aktiviert die Option in `src/config.toml` und richtet einen systemd-Dienst für die automatische Bereitstellung bei jedem Start ein. Die Deinstallation entfernt den Dienst und deaktiviert die Option wieder. **Keiner der Befehle führt einen Shutdown oder Neustart aus.**

Für einen einzelnen Shutdown ohne dauerhafte Installation gibt es weiterhin `--enable-once` (nur wenn `[service].experimental_safe_screen = true` in `src/config.toml` gesetzt ist) und `--disable`.

**Voraussetzung:** Unterstützter Raspberry Pi mit angeschlossenem SSD1327-OLED und den benötigten SPI/GPIO-Geräten.
