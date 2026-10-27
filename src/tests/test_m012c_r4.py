"""HTTP byte ranges for Sonos pause/resume; synthetic PCM only."""
import http.client
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from track_ring_http import Stream, Server, wav_header

class RangeTests(unittest.TestCase):
    def setUp(self):
        self.expected = 58800 * 4
        self.pcm = bytes(range(256)) * (self.expected // 256) + bytes(range(self.expected % 256))
        script = 'import sys; n='+str(self.expected)+'; sys.stdout.buffer.write(bytes(range(256))*(n//256)+bytes(range(n%256)))'
        self.stream = Stream(self.expected, 65536, [sys.executable, '-u', '-c', script], write_timeout=3)
        self.server = Server(('127.0.0.1', 0), self.stream, 1)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.stream.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def request(self, method='GET', headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8)
        try:
            c.request(method,'/track/1.wav',headers=headers or {})
            r=c.getresponse(); code=r.status; hdr=dict(r.getheaders()); body=r.read()
            return code,hdr,body
        finally: c.close()
    def test_middle_open_ended_range_replays_if_history_expired(self):
        # A complete first fetch forces most of the first 16-bit PCM segment out
        # of our small test ring. A resume must replay sequentially.
        code,_,full=self.request()
        self.assertEqual(code,200)
        start=56365
        code,hdr,body=self.request(headers={'Range':f'bytes={start}-'})
        self.assertEqual(code,206)
        self.assertEqual(hdr['Content-Range'],f'bytes {start}-{self.expected+43}/{self.expected+44}')
        self.assertEqual(body,full[start:])
        self.assertEqual(self.server._generation,2)
        self.assertIs(self.server.stream.ring,self.stream.ring)
    def test_range_in_wav_header_and_bounded_unaligned_pcm(self):
        code,hdr,body=self.request(headers={'Range':'bytes=0-42'})
        self.assertEqual((code,body),(206,wav_header(self.expected)[:43]))
        code,hdr,body=self.request(headers={'Range':'bytes=49-106'})
        self.assertEqual(code,206)
        self.assertEqual(body,self.pcm[5:63])
    def test_unsatisfiable_range(self):
        code,hdr,body=self.request(headers={'Range':'bytes=999999999-'})
        self.assertEqual(code,416)
        self.assertEqual(hdr['Content-Range'],f'bytes */{self.expected+44}')
        self.assertEqual(body,b'')
    def test_head_range_does_not_attach_reader(self):
        code,hdr,body=self.request('HEAD',{'Range':'bytes=44-47'})
        self.assertEqual(code,206)
        self.assertEqual(hdr['Content-Length'],'4')
        self.assertEqual(body,b'')

if __name__=='__main__': unittest.main()
