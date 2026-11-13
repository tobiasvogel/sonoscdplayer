"""Read-only CONNECT preview notices driven by semantic input events.

NetworkManager actions and real status collection belong to INP-03c/d.
The notice engine never sees hardware and does not perform network writes.
"""
from .notice import Notice
from .notice_engine import NoticeEngine


class ConnectNoticeView:
    def __init__(self):
        self.engine = NoticeEngine()
        self._ident = None

    def show(self, action, *, now):
        """Replace a CONNECT notice using monotonic seconds."""
        if action == 'status':
            notice = Notice('network.status_preview', duration_seconds=0, priority=20)
        elif action == 'settings_hint':
            notice = Notice('network.settings_hint', duration_seconds=0, priority=25)
        elif action == 'settings':
            notice = Notice('network.settings_preview', duration_seconds=0, priority=30)
        elif action == 'setup_requested':
            notice = Notice('network.setup_requested', duration_seconds=0, priority=50)
        elif action.startswith('warning_') and action[8:] in ('1','2','3','4','5'):
            notice = Notice('network.setup_warning', severity='warning',
                            params={'seconds': int(action[8:])},
                            priority=80, duration_seconds=0, dismissible=False)
        else:
            return False
        if self._ident is not None:
            self.engine.clear(self._ident, now=now)
        self._ident = self.engine.post(notice, now=now)
        return True

    def post(self, notice, *, now):
        """Replace the current notice with any other UI notice."""
        if self._ident is not None:
            self.engine.clear(self._ident, now=now)
        self._ident = self.engine.post(notice, now=now)
        return True

    def current(self, *, now):
        return self.engine.presentation(now=now, base_view=None).notice

    def confirm(self, *, now):
        if self.engine.confirm(now=now):
            self._ident = None
            return True
        return False

    def clear(self, *, now):
        if self._ident is None:
            return False
        result = self.engine.clear(self._ident, now=now)
        self._ident = None
        return result
