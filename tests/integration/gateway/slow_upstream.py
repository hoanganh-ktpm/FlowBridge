# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test-only fault injection upstream. It never imports the app, DB or IAM credentials.
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SlowUpstream(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path != "/_ops/live":
            time.sleep(6)
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(b"{}")
        except BrokenPipeError:
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), SlowUpstream).serve_forever()
