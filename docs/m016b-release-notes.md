# M0.16b — acceptance and release notes

Status: **feature-branch acceptance completed for OLED and GPIO UI; PR review pending**.
This milestone intentionally does **not** enable Sonos transport commands from physical buttons.

## Accepted on Raspberry Pi

- New 128x128 Modern Hi-Fi OLED UI, firmware splash adoption and ambient contrast.
- Encoder push opens the menu; turning navigates and pressing toggles/checks entries.
- A Back entry exits and discards unconfirmed selections.
- Connect does not open the menu.
- Local checkbox/review flow is read-only with respect to Sonos: it does not join,
  unjoin, select playback targets in the player, or start music.
- German/English catalogs with string IDs; UI language is configured in
  `/home/pi/sonoscdplayer/ui.toml`, not derived from Linux locale.
  Encoder-based switching is temporary until an atomic settings writer is tested.
- Central `config/*.example.toml` templates and non-overwriting migration to
  `/home/pi/sonoscdplayer/{player,interface,ui}.toml`.
- Production Python environment dependencies and 51 earlier tests passed;
  subsequent M0.16b i18n/config-migration tests and entire suite reported passing
  by the hardware tester (exact updated total not recorded).
- OLED/buttons/LED frontends reported functioning after systemd config-path work;
  the buttons journal confirms correct GPIO event-to-command mapping in preview.

## Intentionally deferred to M0.17a

- Buttons run `--commands` **without** `--execute`: Previous, Next,
  Stop and Play/Pause log previews but do not control the player.
- Play/Pause requires an explicitly authorized, private UID; there is no automatic
  selection from UI checkboxes or from existing Sonos groups.
- No spontaneous Sonos grouping changes, ownership takeover, or autoplay.
- Player unit config-path cutover and reboot acceptance were prepared but not
  independently confirmed; keep these tracked as operational follow-ups.
- Removal of legacy `src/config.toml` and
  `src/integrations/interfaces/interface.toml` awaits migration acceptance.
- Language changes made through the device menu are session-only, not persistent.

## Scope and safety

This release changes frontend display/navigation, opt-in systemd overlays,
and localized configuration. It does **not** change player command semantics.
The original `systemd/*.service` units remain available for rollback.
Avoid merging private hardware notes or local configuration into a public repository.

## Review before main

Confirm branch/test CI, inspect systemd effective `ExecStart`, verify no
unintended player commands and preserve a rollback path. Merge into `main`
only after explicit approval. Do not tag as a completed mainline release yet.
