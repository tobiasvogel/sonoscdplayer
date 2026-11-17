"""128x128 Modern Hi-Fi multi-target checkbox selection menu."""
from PIL import Image,ImageDraw
from .render import Renderer,font
from .theme import ssd1327_gray

def text_width(value,size,bold=True):
    box=ImageDraw.Draw(Image.new('L',(1,1))).textbbox((0,0),value,font=font(size,bold))
    return box[2]-box[0]


def fit_size(value,width,size,minimum=8):
    """Largest font size <= size at which value fits (else minimum, scrolling)."""
    while size>minimum and text_width(value,size)>width:
        size-=1
    return size


def wrap_lines(value,width,size,*,max_lines=4):
    """Word-wrap for short centred messages; an overlong word scrolls in its line."""
    lines=[]
    for word in value.split():
        if lines and text_width(lines[-1]+' '+word,size)<=width:
            lines[-1]+=' '+word
        elif lines and word in ('…','...'):
            # never leave the ellipsis alone: take the last word along
            head,_,last=lines[-1].rpartition(' ')
            if head:
                lines[-1]=head
                lines.append(last+' '+word)
            else:
                lines[-1]+=' '+word
        else:
            lines.append(word)
    if len(lines)>max_lines:
        lines=lines[:max_lines-1]+[' '.join(lines[max_lines-1:])]
    return lines


class MultiRenderer(Renderer):
    def _multi_pixels(self, labels, marks, focus, *, count, draft_count,
                      notice='', error=False, searching=False, gray=False):
        """Identical speaker-list pixel path for legacy and semantic views."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('menu.targets'))
        self.text(im,(6,16),self.tr('status.targets',selected=draft_count,total=count),
                  fill=t.secondary,size=8,width=116,height=10)
        if not count:
            key='status.scan_running' if searching else 'status.no_targets'
            self.text(im,(7,74),self.tr(key),fill=t.secondary,size=9,width=115,height=15)
        for slot,pos in enumerate(self.menu_window(len(labels),focus)):
            self.menu_row(im,slot,labels[pos],selected=pos==focus,marker=marks[pos])
        status=self.tr(notice) if notice else (
            self.tr('status.incomplete') if error else self.tr('status.no_changes'))
        self.bottom(im,status=status,footer=self.tr('footer.mark'))
        return ssd1327_gray(im) if gray else im

    def render_multi(self,state,*,gray=False,searching=False):
        """Existing production API: build rows from the live selection."""
        count=len(state.topology.targets)
        labels=[target.label for target in state.topology.targets]
        labels.extend((self.tr('menu.review'),self.tr('menu.back')))
        marks=[('✓' if target.key in state.draft else '□')
               for target in state.topology.targets]
        marks.extend(('>','<'))
        return self._multi_pixels(
            labels,marks,state.focus,count=count,draft_count=len(state.draft),
            notice=state.notice,error=bool(state.topology.error),
            searching=searching,gray=gray)

    def render_multi_semantic(self,screen,state,*,gray=False,searching=False):
        """Use semantic labels, focus and checkmarks; no controller actions."""
        from .semantic_screen import SemanticScreen
        if not isinstance(screen,SemanticScreen) or screen.kind!='menu':
            raise ValueError('speaker menu SemanticScreen required')
        count=len(state.topology.targets)
        if len(screen.items)!=count+2:
            raise ValueError('speaker menu length differs from controller')
        if tuple(item.key for item in screen.items[:count])!=tuple(
                target.key for target in state.topology.targets):
            raise ValueError('speaker target identities differ')
        if tuple(item.key for item in screen.items[count:])!=(
                'menu.review','menu.back'):
            raise ValueError('speaker action keys differ')
        focus=[pos for pos,item in enumerate(screen.items) if item.focused]
        if len(focus)!=1:
            raise ValueError('speaker menu requires exactly one focus')
        if focus[0]!=state.focus:
            raise ValueError('speaker focus differs from controller')
        labels=[item.label for item in screen.items]
        marks=[('✓' if item.selected else '□') for item in screen.items[:count]]
        marks.extend(('>','<'))
        return self._multi_pixels(
            labels,marks,focus[0],count=count,draft_count=len(state.draft),
            notice=state.notice,error=bool(state.topology.error),
            searching=searching,gray=gray)

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

    def render_volume_semantic(self, screen, *, gray=False):
        """Read-only OLED volume projection from the shared semantic value."""
        from .semantic_screen import SemanticScreen
        if not isinstance(screen, SemanticScreen) or screen.kind != 'idle':
            raise ValueError('volume SemanticScreen required')
        if len(screen.items) != 1 or screen.items[0].key != 'volume':
            raise ValueError('unexpected volume fields')
        value = screen.items[0].value
        if (value is None or not value.isascii() or not value.isdecimal()
                or str(int(value)) != value or not 0 <= int(value) <= 100):
            raise ValueError('volume must be an integer from 0 to 100')
        return self.render_volume(int(value), gray=gray)

    def render_netsetup(self,snap,*,gray=False,footer=None):
        """WLAN setup (D-223): hotspot name, password and address, or progress."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('netsetup.title'))
        if snap.state=='hotspot':
            self.text(im,(5,18),self.tr('netsetup.network'),fill=t.secondary,size=8,width=118,height=10)
            self.text(im,(5,28),snap.hotspot_ssid,fill=t.primary,size=fit_size(snap.hotspot_ssid,118,10),bold=True,width=118,height=13,scroll=True)
            self.text(im,(5,44),self.tr('netsetup.password'),fill=t.secondary,size=8,width=118,height=10)
            self.text(im,(5,54),snap.password,fill=t.primary,size=13,bold=True,width=118,height=16)
            self.text(im,(5,73),self.tr('netsetup.open'),fill=t.secondary,size=8,width=118,height=10)
            self.text(im,(5,83),snap.url.replace('http://',''),fill=t.primary,size=10,bold=True,width=118,height=13,scroll=True)
            if snap.error=='connect_failed':
                status,fill=self.tr('netsetup.failed'),t.warning
            else:
                status,fill=self.tr('netsetup.remaining',minutes=max(1,(snap.remaining+59)//60)),None
            self.bottom(im,status=status,footer=footer or self.tr('netsetup.cancel_hint'),status_fill=fill)
        else:
            message={
                'preparing':self.tr('netsetup.preparing'),
                'scanning':self.tr('netsetup.scanning'),
                'connecting':self.tr('netsetup.connecting',ssid=snap.target),
                'connected':self.tr('netsetup.connected',ssid=snap.target),
                'unavailable':self.tr('netsetup.no_wifi' if snap.error=='no_wifi' else 'netsetup.hotspot_failed'),
            }.get(snap.state,'')
            fill=t.warning if snap.state=='unavailable' else t.primary
            lines=wrap_lines(message,116,11,max_lines=4)
            top=64-len(lines)*7
            for row,line in enumerate(lines):
                self.text(im,(6,top+row*14),line,fill=fill,size=11,bold=True,width=116,height=14,scroll=True)
            self.bottom(im,status='',footer=self.tr('netsetup.cancel_hint') if snap.state in ('preparing','scanning') else '')
        return ssd1327_gray(im) if gray else im

    def render_wifi_list(self,networks,saved,focus,*,notice='',gray=False):
        """On-device WLAN choice (D-224): found networks, hidden network, back."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('netsetup.choose'))
        rows=[(n.ssid,'✓' if n.ssid in saved else '') for n in networks]
        rows+=[(self.tr('netsetup.rescan'),'↻'),(self.tr('netsetup.hidden'),'+'),(self.tr('netsetup.back_hotspot'),'<')]
        if not networks:
            self.text(im,(6,16),self.tr('netsetup.none_found'),fill=t.secondary,size=8,width=116,height=10)
        for slot,index in enumerate(self.menu_window(len(rows),focus)):
            label,mark=rows[index]
            self.menu_row(im,slot,label,selected=index==focus,marker=mark)
        if notice:
            status,fill=self.tr(notice),t.warning
        elif focus<len(networks):
            n=networks[focus]
            kind='saved' if n.ssid in saved else ('secure' if n.secure else 'open')
            status,fill=self.tr('netsetup.detail',signal=n.signal,kind=self.tr('netsetup.kind.'+kind)),None
        else:
            status,fill='',None
        self.bottom(im,status=status,footer=self.tr('footer.select'),status_fill=fill)
        return ssd1327_gray(im) if gray else im

    def _power_pixels(self, labels, focus, notice='', *, gray=False):
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,self.tr('menu.power'))
        for pos,(label,mark) in enumerate(zip(labels,('<','×','↻'))):
            self.menu_row(im,pos,label,selected=pos==focus,marker=mark)
        self.bottom(im,status=self.tr(notice or 'status.power_choose'),
                    footer=self.tr('footer.select'))
        return ssd1327_gray(im) if gray else im

    def render_power(self,focus,notice='',*,gray=False):
        """Existing production POWER menu API."""
        keys=('menu.back','menu.poweroff','menu.reboot')
        return self._power_pixels([self.tr(key) for key in keys],
                                  focus,notice,gray=gray)

    def render_power_semantic(self,screen,notice='',*,gray=False):
        """Read-only semantic POWER projection via the same pixel path."""
        from .semantic_screen import SemanticScreen
        if not isinstance(screen,SemanticScreen) or screen.kind!='menu':
            raise ValueError('power menu SemanticScreen required')
        if tuple(item.key for item in screen.items)!=(
                'menu.back','menu.poweroff','menu.reboot'):
            raise ValueError('wrong power action keys')
        focus=[pos for pos,item in enumerate(screen.items) if item.focused]
        if len(focus)!=1:
            raise ValueError('power menu requires exactly one focus')
        return self._power_pixels([item.label for item in screen.items],
                                  focus[0],notice,gray=gray)
