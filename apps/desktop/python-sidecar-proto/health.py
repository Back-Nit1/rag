"""Python sidecar packaging prototype: same protocol as sidecar-health (not a Metis service)."""
import http.server, sys, threading

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = b'{"status":"ok"}'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
print(f"METIS_READY port={srv.server_address[1]}", flush=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
sys.stdin.buffer.read()  # EOF = parent gone / shutdown
