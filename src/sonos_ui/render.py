"""128x128 bounded-layout renderer (no hardware access).

Every text glyph is first drawn to an isolated, explicitly bounded viewport.
No glyph may paint a neighboring region, regardless of font metrics or Unicode.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .model import DisplayModel
from .theme import MODERN_HIFI, ssd1327_gray
from .marquee import Marquee
from .layout_config import DisplayProfile, show_cover
from .i18n import Translator

SIZE=(128,128)
FONT_DIR=Path('/usr/share/fonts/truetype/dejavu')

HEADER=(0,14)
CONTENT=(15,98)
STATUS=(99,112)
DIVIDER_Y=113
FOOTER=(115,127)


def font(size=9,bold=False):
    name='DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    try:
        if not (FONT_DIR/name).is_file():
            raise OSError('required Unicode TrueType font unavailable')
        return ImageFont.truetype(str(FONT_DIR/name),size)
    except OSError as exc:
        raise RuntimeError(
            'DejaVuSans fonts are required for Unicode text (e.g. Büro, Küche, Drücken). '
            'Install the system package fonts-dejavu-core; refusing Pillow fallback font.'
        ) from exc

class Renderer:
    def __init__(self,theme=MODERN_HIFI,*,cover_mode='auto',display_profile=DisplayProfile(),language='de'):
        self.theme=theme
        self.marquee=Marquee()
        self.translator=Translator(language)
        self.tr=self.translator.tr
        self.cover_enabled=show_cover(cover_mode,display_profile)

    def text(self,canvas,xy,value,*,fill,size=9,bold=False,width=None,height=None,time=0,scroll=False):
        """Draw top-ink-aligned text, clipped to an explicit rectangular viewport."""
        if value is None or value=='': return
        x,y=xy
        if width is None: width=128-x
        if height is None: height=size+4
        if not (0<=x<128 and 0<=y<128 and 0<width<=128-x and 0<height<=128-y):
            raise ValueError('text viewport is outside 128x128 canvas')
        f=font(size,bold)
        label=str(value)
        mask=Image.new('RGBA',(width,height),(0,0,0,0))
        d=ImageDraw.Draw(mask)
        bbox=d.textbbox((0,0),label,font=f)
        ink_width=max(0,bbox[2]-bbox[0]); ink_height=max(0,bbox[3]-bbox[1])
        ink_top=max(0,(height-ink_height)//2)
        shift=self.marquee.offset(ink_width,width,time) if scroll else 0
        d.text((-bbox[0]-shift,ink_top-bbox[1]),label,font=f,fill=fill)
        canvas.paste(mask,(x,y),mask)

    def cover(self,canvas,path,x=4,y=17,side=36):
        d=ImageDraw.Draw(canvas)
        if path and Path(path).is_file():
            with Image.open(path) as source:
                thumb=ImageOps.fit(source.convert('RGB'),(side,side),method=Image.Resampling.LANCZOS)
            canvas.paste(thumb,(x,y))
        else:
            d.rounded_rectangle((x,y,x+side-1,y+side-1),radius=3,fill=self.theme.surface,outline=self.theme.muted)
            d.ellipse((x+8,y+8,x+side-9,y+side-9),outline=self.theme.secondary,width=2)
            d.ellipse((x+16,y+16,x+19,y+19),fill=self.theme.secondary)

    def shell(self,canvas,heading):
        d=ImageDraw.Draw(canvas)
        d.rectangle((0,HEADER[0],127,HEADER[1]-1),fill=self.theme.surface)
        self.text(canvas,(5,1),heading,fill=self.theme.primary,size=9,bold=True,width=118,height=11)
        d.line((4,14,123,14),fill=self.theme.muted)

    def bottom(self,canvas,*,status,footer,status_fill=None):
        """Status (99-111), separator (113), footer (115-126) never overlap."""
        t=self.theme
        self.text(canvas,(5,100),status,fill=status_fill or t.secondary,size=8,width=118,height=11)
        d=ImageDraw.Draw(canvas)
        d.line((4,DIVIDER_Y,123,DIVIDER_Y),fill=t.muted)
        d.rectangle((0,114,127,126),fill=t.surface)
        self.text(canvas,(5,115),footer,fill=t.secondary,size=8,width=118,height=12)

    def render(self,model:DisplayModel,*,elapsed=0.0,grayscale=False):
        canvas=Image.new('RGB',SIZE,self.theme.background)
        if model.view=='menu': self.menu(canvas,model,elapsed)
        elif model.view=='playback': self.playback(canvas,model,elapsed)
        elif model.view=='idle': self.idle(canvas,model,elapsed)
        else: raise ValueError('unknown view')
        return ssd1327_gray(canvas) if grayscale else canvas

    def idle(self,canvas,m,elapsed):
        t=self.theme;d=ImageDraw.Draw(canvas)
        self.shell(canvas,self.tr('header.disc') if m.disc_present else self.tr('header.no_disc'))
        if not m.disc_present:
            self.text(canvas,(10,53),self.tr('status.no_disc'),fill=t.secondary,size=10,width=110,height=14)
            self.bottom(canvas,status=self.tr('status.ready'),footer=self.tr('footer.open_menu'))
            return
        if self.cover_enabled:
            self.cover(canvas,m.cover)
            self.text(canvas,(44,18),m.album or self.tr('label.audio_cd'),fill=t.primary,bold=True,size=9,width=80,height=12,time=elapsed,scroll=True)
            self.text(canvas,(44,32),m.artist or self.tr('label.unknown_artist'),fill=t.secondary,size=8,width=80,height=11)
            self.text(canvas,(44,45),self.tr('label.tracks',count=m.track_count or '?'),fill=t.muted,size=8,width=80,height=9)
        else:
            self.text(canvas,(6,18),m.album or self.tr('label.audio_cd'),fill=t.primary,bold=True,size=12,width=116,height=17,time=elapsed,scroll=True)
            self.text(canvas,(6,36),m.artist or self.tr('label.unknown_artist'),fill=t.secondary,size=10,width=116,height=15,time=elapsed,scroll=True)
            self.text(canvas,(6,51),self.tr('label.tracks',count=m.track_count or '?'),fill=t.muted,size=8,width=116,height=8)
        d.line((5,59,123,59),fill=t.muted)
        self.text(canvas,(6,65),'01',fill=t.accent,size=11,bold=True,width=19,height=15)
        self.text(canvas,(28,65),m.title or self.tr('label.track',number=1),fill=t.primary,size=11,bold=True,width=96,height=15,scroll=False)
        self.bottom(canvas,status=self.tr('status.ready') if m.speaker else self.tr('status.choose_target'),footer=self.tr('footer.open_menu'),status_fill=t.playing if m.speaker else t.warning)

    def playback(self,canvas,m,elapsed):
        t=self.theme;d=ImageDraw.Draw(canvas)
        self.shell(canvas,self.tr('header.pause') if m.playback=='paused' else self.tr('header.playing'))
        if self.cover_enabled:
            self.cover(canvas,m.cover)
            self.text(canvas,(44,16),f'{m.track:02d}/{m.track_count:02d}',fill=t.accent,size=9,bold=True,width=80,height=11)
            self.text(canvas,(44,29),m.title or self.tr('label.track',number=m.track),fill=t.primary,size=9,bold=True,width=80,height=12,time=elapsed,scroll=True)
            self.text(canvas,(44,43),m.album or self.tr('label.audio_cd'),fill=t.secondary,size=8,width=80,height=10,time=elapsed,scroll=True)
        else:
            self.text(canvas,(6,16),f'{m.track:02d}/{m.track_count:02d}',fill=t.accent,size=10,bold=True,width=116,height=13)
            self.text(canvas,(6,30),m.title or self.tr('label.track',number=m.track),fill=t.primary,size=12,bold=True,width=116,height=16,time=elapsed,scroll=True)
            self.text(canvas,(6,47),m.album or self.tr('label.audio_cd'),fill=t.secondary,size=9,width=116,height=11,time=elapsed,scroll=True)
        d.line((5,59,123,59),fill=t.muted)
        def fmt(s):
            return '--:--' if s is None else f'{s//60:02d}:{s%60:02d}'
        self.text(canvas,(5,65),fmt(m.elapsed_seconds),fill=t.primary,size=10,bold=True,width=47,height=15)
        self.text(canvas,(93,65),fmt(m.duration_seconds),fill=t.secondary,size=9,width=31,height=15)
        d.rectangle((6,84,122,87),fill=t.surface)
        if m.elapsed_seconds is not None and m.duration_seconds and m.duration_seconds>0:
            progress=max(0,min(1,m.elapsed_seconds/m.duration_seconds))
            d.rectangle((6,84,6+int(116*progress),87),fill=t.playing)
        self.bottom(canvas,status=m.speaker or self.tr('status.no_speaker'),footer=self.tr('footer.open_menu'))

    def menu(self,canvas,m,elapsed):
        t=self.theme; d=ImageDraw.Draw(canvas)
        self.shell(canvas,self.tr('header.speakers'))
        self.text(canvas,(6,16),self.tr('menu.choose_target'),fill=t.secondary,size=8,width=116,height=10)
        count=len(m.speakers)
        if not count:
            label=self.tr('menu.choose_target') if m.speakers_searching else self.tr('status.no_targets')
            self.text(canvas,(8,45),label,fill=t.secondary,size=9,width=112,height=30)
        else:
            top=max(0,min(max(0,count-4),m.focus_index-1))
            for local_index in range(4):
                index=top+local_index
                if index>=count: break
                y=27+local_index*18
                focused=index==m.focus_index
                selected=(index in m.checked_indices if m.checkbox_mode else index==m.selected_index)
                if focused:
                    d.rounded_rectangle((4,y,123,y+15),radius=2,fill=t.accent)
                color=t.selection_text if focused else t.primary
                self.text(canvas,(8,y+1),m.speakers[index],fill=color,size=9,
                          bold=focused,width=99,height=14,time=elapsed,scroll=focused)
                if m.checkbox_mode:
                    # A visible checkbox for every row, distinct from focus.
                    d.rectangle((109,y+3,120,y+13),outline=color,width=1)
                    if selected:
                        self.text(canvas,(110,y+2),'✓',fill=color,size=9,bold=True,
                                  width=11,height=12)
                elif selected:
                    self.text(canvas,(111,y+1),'✓',fill=color,size=9,bold=True,
                              width=12,height=14)
        if m.checkbox_mode:
            # No false claim of an acknowledged target from draft checkboxes.
            self.bottom(canvas,status=self.tr('status.marked_short',count=len(m.checked_indices)),
                        footer=self.tr('footer.turn_press'))
        else:
            current=(m.speakers[m.selected_index] if m.selected_index is not None
                     and 0<=m.selected_index<count else self.tr('status.no_speaker'))
            self.bottom(canvas,status=self.tr('status.current',name=current),
                        footer=self.tr('footer.turn_press'))
