"""
Simple PoC server to help test DNS rebinding scenarios.

Usage (manual steps):
1. Deploy this server on a public host you control (or run locally and expose via ngrok).
2. Point a test domain (e.g., test.example.com) to the server IP.
3. The server alternates responses: first it serves a page that loads an image from the same host; later, change the DNS of the domain to point to a private IP (or simulate by editing hosts file) and the browser will request the image from the new IP.

This script only provides the HTTP server component and instructions — DNS control is required to perform an actual DNS-rebinding PoC.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import time

class RebindHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/image.png'):
            # serve a small 1x1 PNG
            img = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82")
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(img)))
            self.end_headers()
            self.wfile.write(img)
            return
        # main page loads the image via relative URL
        html = """
        <html><body>
        <h1>DNS Rebind PoC</h1>
        <img src="/image.png" alt="dot">
        <p>Reload after changing DNS to a private IP to test rebind effect.</p>
        </body></html>
        """
        b = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', 8080), RebindHandler)
    print('DNS rebind PoC server listening on :8080')
    server.serve_forever()
