"""UI localization, configuration, menu-language and encoder regressions."""
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch
from sonos_ui.i18n import Translator
from sonos_ui.i18n.translator import catalog, available_languages
from sonos_ui.ui_config import load_ui_config, UIConfig
from sonos_ui.render import Renderer
from sonos_ui.model import DisplayModel
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import from_devices


def speakers():
    a=NS(uid='u1',player_name='Gästezimmer')
    g=NS(coordinator=a,members=(a,))
    a.group=g
    return from_devices((a,))


class LocalizationTests(unittest.TestCase):
    def test_translation_catalogs_have_identical_ids(self):
        self.assertEqual(set(catalog('de')),set(catalog('en')))

    def test_all_translations_format_with_matching_fields(self):
        import string
        fmt=string.Formatter()
        en_catalog=catalog('en')
        for lang in available_languages():
            self.assertEqual(set(en_catalog),set(catalog(lang)),lang)
            for key in en_catalog:
                en={name for _,name,_,_ in fmt.parse(en_catalog[key]) if name}
                other={name for _,name,_,_ in fmt.parse(catalog(lang)[key]) if name}
                self.assertEqual(en,other,(lang,key))

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(Translator('zz').tr('menu.back'),'Back')

    def test_missing_id_fails_loudly_in_tests(self):
        with self.assertRaises(KeyError):
            Translator('de').tr('not.a.real.key')

    def test_config_defaults_are_not_linux_locale_dependent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'LANG':'fr_FR.UTF-8'}):
                value=load_ui_config(Path(tmp)/'missing.toml')
        self.assertEqual(value.language,'de')
        self.assertEqual(value.cover_mode,'auto')

    def test_config_loads_language_and_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'ui.toml'
            p.write_text('[ui]\nlanguage = "en"\n[ui.cover]\nmode = "off"\n',encoding='utf8')
            value=load_ui_config(p)
        self.assertEqual((value.language,value.cover_mode),('en','off'))

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(ValueError):
            UIConfig(language='fr')

    def test_renderer_both_locales(self):
        model=DisplayModel(view='idle',disc_present=False)
        images=[Renderer(language=lang).render(model).tobytes() for lang in ('de','en')]
        self.assertNotEqual(images[0],images[1])

    def test_encoder_language_picker_is_session_only(self):
        menu=InteractiveMenu(discover=lambda *,timeout: speakers(),language='de')
        self.assertTrue(menu.event('encoder_sw','pressed',now=0))
        self.assertEqual(menu.section, 'main')
        menu.event('encoder','right',now=1)
        menu.event('encoder','right',now=1)
        self.assertTrue(menu.event('encoder_sw','pressed',now=1))
        self.assertEqual(menu.section, 'settings')
        self.assertTrue(menu.event('encoder_sw','pressed',now=1))
        self.assertTrue(menu.language_picker)
        menu.event('encoder','right')
        menu.event('encoder_sw','pressed')
        self.assertEqual(menu.language,'en')
        self.assertFalse(menu.language_picker)

    def test_new_locale_file_is_available_without_python_registration(self):
        from sonos_ui.i18n import translator
        from integrations.outputs.oled_status import status_frame
        from unittest.mock import patch as mock_patch
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            original=__import__('importlib').resources.files('sonos_ui.i18n.locales')
            for lang in ('de','en'):
                (directory/(lang+'.json')).write_text(
                    original.joinpath(lang+'.json').read_text(encoding='utf8'),
                    encoding='utf8')
            es=dict(catalog('en'))
            es.update({'language.name':'Español','menu.back':'Atrás',
                       'status.marked_short':'{count} marcados'})
            (directory/'es.json').write_text(json.dumps(es),encoding='utf8')
            with mock_patch.object(translator.resources,'files',return_value=directory):
                translator.available_languages.cache_clear()
                translator.catalog.cache_clear()
                try:
                    self.assertEqual(translator.available_languages(),('de','en','es'))
                    self.assertEqual(UIConfig(language='es').language,'es')
                    menu=InteractiveMenu(discover=lambda *,timeout: speakers(),language='de')
                    self.assertEqual(menu.languages,('de','en','es'))
                    menu.event('encoder_sw','pressed',now=0)
                    menu.event('encoder','right',now=1)
                    menu.event('encoder','right',now=1)
                    menu.event('encoder_sw','pressed',now=1)
                    menu.event('encoder_sw','pressed',now=1)
                    menu.event('encoder','right')
                    menu.event('encoder','right')
                    menu.event('encoder_sw','pressed')
                    self.assertEqual(menu.language,'es')
                    self.assertEqual(menu.renderer.tr('menu.back'),'Atrás')
                    self.assertEqual(Renderer(language='es').tr('status.marked_short',count=2),
                                     '2 marcados')
                    self.assertEqual(len(status_frame(error='offline',language='es').data),8192)
                finally:
                    translator.available_languages.cache_clear()
                    translator.catalog.cache_clear()

    def test_unnamed_speakers_use_configured_ui_language(self):
        from sonos_ui.discovery import from_devices as simple_from_devices
        from sonos_ui.multi_target import from_devices as grouped_from_devices
        device=NS(uid='unnamed',player_name=' ')
        group=NS(coordinator=device,members=(device,))
        device.group=group
        self.assertEqual(simple_from_devices((device,),language='en').names,
                         ('Unnamed speaker',))
        self.assertEqual(grouped_from_devices((device,),language='en').targets[0].label,
                         'Unnamed speaker')
        self.assertEqual(grouped_from_devices((device,),language='de').targets[0].label,
                         'Unbenannter Lautsprecher')

    def test_configured_language_is_initial_menu_language(self):
        menu=InteractiveMenu(discover=lambda *,timeout: speakers(),language='en')
        self.assertEqual(menu.language,'en')
        self.assertEqual(menu.language_focus,menu.languages.index('en'))

    def test_connect_does_not_open_menu(self):
        menu=InteractiveMenu(discover=lambda *,timeout: speakers())
        self.assertFalse(menu.event('connect','pressed',now=0))
        self.assertFalse(menu.active)


if __name__=='__main__':
    unittest.main()
