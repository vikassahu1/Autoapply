"""A local stand-in for the Indeed flow, used only by the browser end-to-end test.

It reproduces the *shape* seen in the screenshots (feed + detail pane + apply tab with
Continue / Submit steps) using the same selectors the adapter config guesses. It cannot
prove those guesses match the real Indeed; `python -m autoapply doctor` does that.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# key, title, company, kind
JOBS = [
    ("j1", "Frontend Developer", "Kognito Kube", "easy"),
    ("j2", "Backend Developer", "Questionable Inc", "easy"),          # asks a required question
    ("j3", "Data Engineer", "Portal Corp", "external_label"),         # "Apply on company site"
    ("j4", "SRE", "Redirect Ltd", "easy"),                            # Apply opens a company-site tab
    ("j5", "ML Engineer", "Captcha Co", "easy"),                      # CAPTCHA, then fine
    ("j6", "QA Engineer", "Silent Systems", "easy"),                  # submit never confirms
    ("j7", "Support Engineer", "Stale Pane LLC", "easy"),             # pane does not update
]


class MockSite:
    def __init__(self, port: int, external_origin: str):
        self.port = port
        self.external_origin = external_origin
        self.captcha_solved = False
        self.hits: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    # --- pages -----------------------------------------------------------------
    def _feed(self) -> str:
        cards = ""
        for key, title, company, kind in JOBS:
            badge = "<div>Easily apply</div>" if key in ("j1", "j2") else ""
            stale = " data-noupdate='1'" if key == "j7" else ""
            cards += (f"<div class='job_seen_beacon' data-jk='{key}' data-kind='{kind}'{stale} "
                      f"style='border:1px solid #ccc;margin:6px;padding:8px'>{badge}"
                      f"<h2>{title}</h2><div>{company}</div><div>Pune, Maharashtra</div></div>")
        ext = self.external_origin
        return f"""<!doctype html><html><head><title>Jobs</title></head><body>
<h1>Welcome, VIKAS</h1>
<div style='display:flex'>
<div id='list' style='width:45%'>{cards}</div>
<div id='pane' data-testid='jobsearch-ViewjobPaneWrapper' style='width:50%'>
  <h2 data-testid='jobsearch-JobInfoHeader-title' id='pane-title'>Some Unrelated Job</h2>
  <div id='actions'></div>
</div></div>
<script>
const EXT = "{ext}/company";
document.querySelectorAll('.job_seen_beacon').forEach(card => card.addEventListener('click', () => {{
  if (card.dataset.noupdate) return;                       // simulates a pane that fails to switch
  const key = card.dataset.jk, kind = card.dataset.kind;
  document.getElementById('pane-title').textContent = card.querySelector('h2').textContent + ' - job post';
  const box = document.getElementById('actions'); box.replaceChildren();
  if (kind === 'external_label') {{
    const a = document.createElement('a'); a.href = EXT; a.textContent = 'Apply on company site'; box.append(a);
  }} else {{
    const b = document.createElement('button'); b.textContent = 'Apply now';
    b.onclick = () => window.open(key === 'j4' ? EXT : '/apply/' + key + '/start', '_blank');
    box.append(b);
  }}
}}));
</script></body></html>"""

    @staticmethod
    def _page(title: str, body: str, script: str = "") -> str:
        return f"<!doctype html><html><head><title>{title}</title></head><body><h1>{title}</h1>{body}<script>{script}</script></body></html>"

    def _apply_page(self, key: str, step: str) -> str:
        go = lambda path, label: f"<button onclick=\"location='{path}'\">{label}</button>"
        if step == "start":
            if key == "j2":
                return self._page("Answer these questions", "<label>Years of experience <input required></label>" + go(f"/apply/{key}/review", "Continue"))
            if key == "j5":
                return self._page(
                    "Verify", "<iframe src='/recaptcha/api2/bframe' width='300' height='80'></iframe>"
                    f"<button id='go' hidden onclick=\"location='/apply/{key}/review'\">Continue</button>",
                    "setInterval(async()=>{const r=await fetch('/captcha-state');if((await r.text())==='solved'){"
                    "document.querySelector('iframe')?.remove();document.getElementById('go').hidden=false;}},150)")
            return self._page("Add your contact information", go(f"/apply/{key}/review", "Continue"))
        if step == "review":
            nxt = "processing" if key == "j6" else "post-apply"
            return self._page("Review your application", go(f"/apply/{key}/{nxt}", "Submit your application"))
        if step == "post-apply":
            return self._page("Your application has been submitted", "<p>Thanks!</p>")
        return self._page("Processing", "<p>Please wait...</p>")  # j6: never confirms

    def _handler(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                site.hits.append(path)
                status, body = 200, ""
                if path == "/":
                    body = site._feed()
                elif path == "/captcha-state":
                    body = "solved" if site.captcha_solved else "pending"
                elif path == "/recaptcha/api2/bframe":
                    body = "<html><body>challenge</body></html>"
                elif path in ("/company", "/unrelated"):
                    body = site._page(path.strip("/").title(), "<p>hello</p>")
                elif path.startswith("/apply/"):
                    _, _, key, step = path.split("/")
                    body = site._apply_page(key, step)
                else:
                    status, body = 404, "nope"
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler
