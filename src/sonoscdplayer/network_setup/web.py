"""Minimal setup web page, served only on the hotspot address while it is active.

GET /        form: visible networks (scanned before the hotspot started),
             a field for a hidden network name, the password
POST /       hands the choice to the session; the hotspot then ends
No other paths, no files, no external resources. Requests are small and bounded.
"""
from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import parse_qs

MAX_BODY = 2048


class _T:
    """Callable view on a ui.i18n Translator (language follows the menu)."""
    def __init__(self, translator):
        self._translator = translator() if callable(translator) else translator
        self.language = self._translator.language

    def __call__(self, key, **values):
        return self._translator.tr(key, **values)
PORTS = (80, 8080)   # 80 needs CAP_NET_BIND_SERVICE; 8080 is the fallback


def page(translator, snapshot, *, message_key=''):
    """HTML of the setup page: network list, password field and status."""
    t = _T(translator)
    options = []
    for index, network in enumerate(snapshot.networks):
        lock = ' &#128274;' if network.secure else ''
        checked = ' checked' if index == 0 else ''
        options.append(
            f'<label><input type="radio" name="ssid" value="{escape(network.ssid)}"{checked}> '
            f'{escape(network.ssid)}{lock} <small>{network.signal}%</small></label>')
    if not options:
        options.append(f'<p>{escape(t("netsetup.web.no_networks"))}</p>')
    message = ''
    if message_key:
        message = f'<p class="msg">{escape(t(message_key))}</p>'
    elif snapshot.error == 'connect_failed':
        message = f'<p class="msg">{escape(t("netsetup.web.failed"))}</p>'
    return f'''<!DOCTYPE html>
<html lang="{escape(t.language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SonosCDPlayer</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:28rem;margin:1.5rem auto;padding:0 1rem;color:#222}}
h1{{font-size:1.3rem}} label{{display:block;padding:.45rem 0;border-bottom:1px solid #eee}}
input[type=text],input[type=password]{{width:100%;padding:.5rem;margin:.3rem 0 1rem;box-sizing:border-box;font-size:1rem}}
button{{width:100%;padding:.7rem;font-size:1rem}} small{{color:#777}} .msg{{color:#a00;font-weight:600}}
</style></head><body>
<h1>{escape(t("netsetup.web.title"))}</h1>
{message}
<form method="post" action="/">
<p>{escape(t("netsetup.web.choose"))}</p>
{"".join(options)}
<p>{escape(t("netsetup.web.hidden"))}</p>
<input type="text" name="hidden" maxlength="32" autocomplete="off">
<p>{escape(t("netsetup.web.password"))}</p>
<input type="password" name="password" maxlength="63" autocomplete="off">
<button type="submit">{escape(t("netsetup.web.connect"))}</button>
</form>
<p><small>{escape(t("netsetup.web.note"))}</small></p>
</body></html>'''


def done_page(translator, ssid):
    """HTML shown after the network was chosen."""
    t = _T(translator)
    return f'''<!DOCTYPE html>
<html lang="{escape(t.language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SonosCDPlayer</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:1.5rem auto;padding:0 1rem}}</style>
</head><body><h1>{escape(t("netsetup.web.title"))}</h1>
<p>{escape(t("netsetup.web.connecting", ssid=ssid))}</p></body></html>'''


class SetupWebServer:
    """Serve the setup page on address; stop() shuts it down."""

    def __init__(self, session, address, translate, ports=PORTS):
        handler = self._handler(session, translate)
        last_error = None
        for port in ports:
            try:
                self._server = ThreadingHTTPServer((address, port), handler)
                break
            except OSError as exc:
                last_error = exc
        else:
            raise last_error
        self._server.daemon_threads = True
        port = self._server.server_address[1]
        self.url = f'http://{address}' + ('' if port == 80 else f':{port}')
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name='netsetup-web', daemon=True)
        self._thread.start()

    def stop(self):
        """Stop serving the setup page."""
        self._server.shutdown()
        self._server.server_close()

    @staticmethod
    def _handler(session, translate):
        class Handler(BaseHTTPRequestHandler):
            server_version = 'SonosCDPlayer'
            sys_version = ''

            def log_message(self, *_args):
                pass  # never log form contents

            def _send(self, body, status=200):
                data = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                # Any path shows the form, so phones' connectivity checks land here too.
                self._send(page(translate, session.snapshot()))

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    length = -1
                if not 0 <= length <= MAX_BODY:
                    self._send(page(translate, session.snapshot(),
                                    message_key='netsetup.web.invalid'), 400)
                    return
                fields = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'),
                                  keep_blank_values=True, max_num_fields=8)
                hidden = (fields.get('hidden') or [''])[0].strip()
                ssid = hidden or (fields.get('ssid') or [''])[0]
                password = (fields.get('password') or [''])[0]
                error = session.submit(ssid, password)
                if error:
                    self._send(page(translate, session.snapshot(),
                                    message_key='netsetup.web.' + error), 400)
                    return
                self._send(done_page(translate, ssid))

        return Handler
