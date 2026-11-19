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
    def multi_pixels(self, heading, subheading, labels, marks, focus, *,
                     placeholder='', status='', footer='', gray=False):
        """Speaker list from translated texts (one mark per row)."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,heading)
        self.text(im,(6,16),subheading,fill=t.secondary,size=8,width=116,height=10)
        if placeholder:
            # Below the two rows (apply/back) that remain without speakers.
            self.text(im,(7,74),placeholder,fill=t.secondary,size=9,width=115,height=15)
        for slot,pos in enumerate(self.menu_window(len(labels),focus)):
            self.menu_row(im,slot,labels[pos],selected=pos==focus,marker=marks[pos])
        self.bottom(im,status=status,footer=footer)
        return ssd1327_gray(im) if gray else im

    def render_multi(self,state,*,gray=False,searching=False):
        """Speaker list from the live MultiSelection."""
        from .semantic_views import speaker_selection_screen
        from .graphic_views import render_screen
        screen=speaker_selection_screen(state,language=self.translator.language,
                                        searching=searching)
        return render_screen(screen,renderer=self,gray=gray)

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

    def render_netsetup(self,snap,*,gray=False,footer=None):
        """WLAN setup: hotspot name, password and address, or progress."""
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
        """On-device WLAN choice: found networks, hidden network, back."""
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

    def power_pixels(self, heading, labels, focus, *, status, footer, gray=False):
        """POWER menu from translated texts: back / power off / restart."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,heading)
        for pos,(label,mark) in enumerate(zip(labels,('<','×','↻'))):
            self.menu_row(im,pos,label,selected=pos==focus,marker=mark)
        self.bottom(im,status=status,footer=footer)
        return ssd1327_gray(im) if gray else im

    def render_power(self,focus,notice='',*,gray=False):
        """POWER menu. ``notice`` is a locale key."""
        from .semantic_views import power_menu_screen
        from .graphic_views import render_screen
        screen=power_menu_screen(focus,notice=notice,language=self.translator.language)
        return render_screen(screen,renderer=self,gray=gray)

    def language_pixels(self, heading, labels, focus, *, status, footer):
        """Language picker (unquantized, as the OLED has always drawn it)."""
        t=self.theme
        im=Image.new('RGB',(128,128),t.background)
        self.shell(im,heading)
        # 128px OLED: keep focused language visible even with many files.
        for slot,index in enumerate(self.menu_window(len(labels),focus)):
            self.menu_row(im,slot,labels[index],selected=index==focus)
        self.bottom(im,status=status,footer=footer)
        return im
