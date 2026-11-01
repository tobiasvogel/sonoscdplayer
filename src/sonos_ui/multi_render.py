"""128x128 Modern Hi-Fi multi-target selection and review screens."""
from PIL import Image,ImageDraw
from .render import Renderer
from .theme import ssd1327_gray

class MultiRenderer(Renderer):
    def render_multi(self,state,*,gray=False):
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('menu.review') if state.reviewing else self.tr('menu.targets'))
        d=ImageDraw.Draw(im)
        if state.reviewing:
            count=len(state.draft)
            self.text(im,(6,23),self.tr('status.marked',count=count),fill=t.primary,size=9,width=116,height=14)
            for i,label in enumerate(state.selections()[:3]):
                self.text(im,(8,43+i*16),label,fill=t.secondary,size=9,width=112,height=13)
            self.bottom(im,status=self.tr('status.local_only'),footer=self.tr('footer.confirm'))
        else:
            self.text(im,(6,16),self.tr('status.targets',selected=len(state.draft),total=len(state.topology.targets)),fill=t.secondary,size=8,width=116,height=10)
            count=len(state.topology.targets)
            if not count:
                self.text(im,(7,49),self.tr('status.no_targets'),fill=t.secondary,size=9,width=115,height=15)
            first=max(0,min(max(0,count+3-4),state.focus-1))
            for pos in range(first,min(first+4,count+3)):
                y=27+(pos-first)*18
                focus=pos==state.focus
                if focus:d.rounded_rectangle((4,y,123,y+15),radius=2,fill=t.accent)
                col=t.selection_text if focus else t.primary
                if pos==count:
                    name=self.tr('menu.review')
                    check='>'
                elif pos==count+1:
                    name=self.tr('menu.language')
                    check='>'
                elif pos==count+2:
                    name=self.tr('menu.back')
                    check='<'
                else:
                    target=state.topology.targets[pos]
                    name=target.label
                    check='✓' if target.key in state.draft else '□'
                self.text(im,(8,y+1),check,fill=col,size=9,width=13,height=14)
                self.text(im,(23,y+1),name,fill=col,size=9,bold=focus,width=99,height=14,scroll=focus)
            status=self.tr(state.notice) if state.notice else (self.tr('status.incomplete') if state.topology.error else self.tr('status.no_changes'))
            self.bottom(im,status=status,footer=self.tr('footer.mark'))
        return ssd1327_gray(im) if gray else im
