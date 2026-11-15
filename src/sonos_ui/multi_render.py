"""128x128 Modern Hi-Fi multi-target checkbox selection menu."""
from PIL import Image,ImageDraw
from .render import Renderer
from .theme import ssd1327_gray

class MultiRenderer(Renderer):
    def render_multi(self,state,*,gray=False):
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('menu.targets'))
        self.text(im,(6,16),self.tr('status.targets',selected=len(state.draft),total=len(state.topology.targets)),fill=t.secondary,size=8,width=116,height=10)
        count=len(state.topology.targets)
        if not count:
            # Below the two rows (apply/back) that remain without speakers.
            self.text(im,(7,74),self.tr('status.no_targets'),fill=t.secondary,size=9,width=115,height=15)
        rows=self.menu_window(count+2,state.focus)
        for slot,pos in enumerate(rows):
            focus=pos==state.focus
            if pos==count:
                name=self.tr('menu.review')
                check='>'
            elif pos==count+1:
                name=self.tr('menu.back')
                check='<'
            else:
                target=state.topology.targets[pos]
                name=target.label
                check='✓' if target.key in state.draft else '□'
            self.menu_row(im,slot,name,selected=focus,marker=check)
        status=self.tr(state.notice) if state.notice else (self.tr('status.incomplete') if state.topology.error else self.tr('status.no_changes'))
        self.bottom(im,status=status,footer=self.tr('footer.mark'))
        return ssd1327_gray(im) if gray else im

    def render_volume(self,level,*,gray=False):
        """Short volume overlay: big number and a 0..100 bar."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('menu.volume'))
        d=ImageDraw.Draw(im)
        level=max(0,min(100,int(level)))
        self.text(im,(8,30),str(level),fill=t.primary,size=28,bold=True,width=112,height=34)
        d.rounded_rectangle((8,74,119,86),radius=3,outline=t.muted)
        if level:
            d.rounded_rectangle((10,76,10+round(107*level/100),84),radius=2,fill=t.accent)
        return ssd1327_gray(im) if gray else im

    def render_power(self,focus,notice='',*,gray=False):
        """Power menu: Zurück / Ausschalten / Neustarten. ``notice`` is a locale key."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('menu.power'))
        for pos,(key,mark) in enumerate((('menu.back','<'),('menu.poweroff','×'),('menu.reboot','↻'))):
            self.menu_row(im,pos,self.tr(key),selected=pos==focus,marker=mark)
        self.bottom(im,status=self.tr(notice or 'status.power_choose'),footer=self.tr('footer.select'))
        return ssd1327_gray(im) if gray else im
