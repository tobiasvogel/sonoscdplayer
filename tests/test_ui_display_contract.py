"""Contract tests for ARC-DISPLAY-01A; no physical display is required."""
import unittest

from sonos_ui.display_capabilities import DisplayCapabilities, SSD1327_CAPABILITIES
from sonos_ui.display_backend import (
    BackendRegistry, CharacterFrame, GraphicFrame, validate_frame,
)
from sonos_ui.semantic_screen import (
    ScreenItem, SemanticScreen, screen_from_display_model, screen_from_notice,
)
from sonos_ui.model import DisplayModel
from sonos_ui.notice import Notice


CHAR_16X2 = DisplayCapabilities(
    kind="character", columns=16, rows=2, charset="hd44780")
CHAR_20X4 = DisplayCapabilities(
    kind="character", columns=20, rows=4, charset="ascii")


class CapabilitiesTests(unittest.TestCase):
    def test_reference_graphic_and_character_geometries(self):
        self.assertEqual((SSD1327_CAPABILITIES.width,
                          SSD1327_CAPABILITIES.height), (128, 128))
        self.assertEqual((CHAR_16X2.columns, CHAR_16X2.rows), (16, 2))
        self.assertEqual((CHAR_20X4.columns, CHAR_20X4.rows), (20, 4))
        self.assertEqual(CHAR_16X2.kind, "character")

    def test_character_and_graphic_geometry_must_not_be_mixed(self):
        invalid = (
            dict(kind="character", columns=16, rows=2, charset="ascii", width=128),
            dict(kind="graphic", width=128, height=128, pixel_format="gray4-packed",
                 rows=2),
            dict(kind="character", columns=0, rows=2, charset="ascii"),
            dict(kind="character", columns=True, rows=2, charset="ascii"),
            dict(kind="graphic", width=128, height=0, pixel_format="rgb565"),
            dict(kind="graphic", width=128, height=128),
            dict(kind="character", columns=16, rows=2),
            dict(kind="character", columns=16, rows=2, charset="ascii",
                 min_refresh_ms=0),
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                DisplayCapabilities(**kwargs)

    def test_event_driven_display_may_require_slow_refresh(self):
        cap = DisplayCapabilities(kind="graphic", width=200, height=200,
                                  pixel_format="rgb565",
                                  refresh_policy="event_driven", min_refresh_ms=5000)
        self.assertEqual(cap.min_refresh_ms, 5000)


class SemanticScreenTests(unittest.TestCase):
    def test_solo_menu_focus_and_selection_are_independent(self):
        model = DisplayModel(view="menu", speakers=("Wohnzimmer", "Kueche"),
                             focus_index=1, selected_index=0)
        screen = screen_from_display_model(model)
        self.assertEqual(screen.kind, "menu")
        self.assertEqual([x.focused for x in screen.items], [False, True])
        self.assertEqual([x.selected for x in screen.items], [True, False])
        self.assertEqual(screen.items[1].intent, "select_speaker")

    def test_checkbox_menu_preserves_multiple_selections(self):
        screen = screen_from_display_model(DisplayModel(
            view="menu", speakers=("A", "B", "C"), checkbox_mode=True,
            checked_indices=(0, 2), focus_index=1))
        self.assertEqual([x.selected for x in screen.items], [True, False, True])
        self.assertEqual([x.focused for x in screen.items], [False, True, False])

    def test_playback_and_idle_do_not_invent_missing_metadata(self):
        playing = screen_from_display_model(DisplayModel(
            view="playback", album="Beispiel", title="Langes Stueck",
            track=3, track_count=12, speaker="Wohnzimmer", playback="playing"))
        self.assertEqual(playing.kind, "playback")
        self.assertEqual(next(x.value for x in playing.items
                              if x.key == "track"), "3/12")
        self.assertEqual(next(x.value for x in playing.items
                              if x.key == "speaker"), "Wohnzimmer")
        empty = screen_from_display_model(DisplayModel(
            view="idle", disc_present=False, album="Old"))
        self.assertNotIn("album", {x.key for x in empty.items})

    def test_notice_uses_existing_localization_and_severity(self):
        for language in ("de", "en"):
            screen = screen_from_notice(
                Notice("status.player_offline", severity="error"),
                language=language)
            self.assertEqual(screen.kind, "notice")
            self.assertTrue(screen.message)
            self.assertEqual(screen.severity, "error")

    def test_contract_rejects_multiple_focus_and_invalid_notices(self):
        with self.assertRaises(ValueError):
            SemanticScreen("menu", items=(
                ScreenItem("a", "A", focused=True),
                ScreenItem("b", "B", focused=True)))
        with self.assertRaises(ValueError):
            SemanticScreen("notice", message="Lost", severity="error",
                           items=(ScreenItem("a", "A"),))
        with self.assertRaises(ValueError):
            SemanticScreen("notice", message="", severity="error")
        with self.assertRaises(ValueError):
            SemanticScreen("menu", items=(ScreenItem("a", "A"), ScreenItem("a", "B")))


class BackendContractTests(unittest.TestCase):
    class FakeBackend:
        def __init__(self, capabilities):
            self.capabilities = capabilities
            self.frames = []
            self.opened = False

        def open(self):
            self.opened = True

        def show(self, frame):
            validate_frame(self.capabilities, frame)
            if not self.opened:
                raise RuntimeError("backend not open")
            self.frames.append(frame)

        def close(self):
            self.opened = False

    def test_explicit_registry_and_character_fake(self):
        registry = BackendRegistry()
        registry.register("character_16x2", lambda: self.FakeBackend(CHAR_16X2))
        backend = registry.create("character_16x2")
        backend.open()
        frame = CharacterFrame(("PLAY 03/12      ", "Langes Stueck   "))
        backend.show(frame)
        self.assertEqual(backend.frames, [frame])
        backend.close()
        self.assertFalse(backend.opened)

    def test_unknown_and_duplicate_backends_fail_closed(self):
        registry = BackendRegistry()
        with self.assertRaises(ValueError):
            registry.create("arbitrary.module")
        registry.register("test", lambda: self.FakeBackend(CHAR_20X4))
        with self.assertRaises(ValueError):
            registry.register("test", lambda: self.FakeBackend(CHAR_20X4))
        with self.assertRaises(ValueError):
            registry.register("../../unsafe", lambda: None)
        registry.register("bad", lambda: object())
        with self.assertRaises(TypeError):
            registry.create("bad")

    def test_character_frames_require_exact_dimensions(self):
        validate_frame(CHAR_16X2, CharacterFrame((" " * 16, " " * 16)))
        validate_frame(CHAR_20X4, CharacterFrame((" " * 20,) * 4))
        for frame in (CharacterFrame((" " * 16,)),
                      CharacterFrame((" " * 16, " " * 15)),
                      GraphicFrame(16, 2, "rgb565", b"")):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                validate_frame(CHAR_16X2, frame)

    def test_graphic_frames_require_matching_format_and_packing(self):
        frame = GraphicFrame(128, 128, "gray4-packed", bytes(8192))
        validate_frame(SSD1327_CAPABILITIES, frame)
        for bad in (GraphicFrame(128, 128, "gray4-packed", bytes(8191)),
                    GraphicFrame(128, 128, "rgb565", bytes(32768)),
                    CharacterFrame((" " * 16, " " * 16))):
            with self.subTest(frame=type(bad).__name__), self.assertRaises(ValueError):
                validate_frame(SSD1327_CAPABILITIES, bad)


if __name__ == "__main__":
    unittest.main()
