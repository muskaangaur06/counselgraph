"""Build a static, backend-free copy of the UI for hosting on Vercel or Netlify.

Captures real responses from a locally running instance into demo/api/*.json,
then emits demo/index.html with apiFetch() rewritten to read those files. The
result renders genuine clauses, risk flags, negotiation playbooks and evaluation
metrics with no server, no database and no API key.

Only read-only GET endpoints are captured. Anything that writes (upload, chat,
approvals, brief generation) cannot work without the backend and is disabled in
the demo build rather than faked.

Usage:
    docker compose up -d                       # app must be running
    .venv/Scripts/python.exe scripts/build_static_demo.py
    # then deploy the demo/ directory
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("DEMO_SOURCE_URL", "http://localhost:8000")
REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "src" / "legal_graphrag" / "api" / "static"
DEMO_DIR = REPO_ROOT / "demo"
API_DIR = DEMO_DIR / "api"

# Documents to include. The first is what the demo opens by default, so it
# should be one with clauses, risk flags and playbook entries.
FEATURED_DOCUMENTS = [
    "92621052-27d8-4e9a-a35f-a3630ea0d979",
    "e4ba418a-cc5d-4608-856c-2a1f679c0739",
    "1b5f248c-89f0-46d3-87c0-44c5694e5c89",
]
FEATURED_JOBS = ["a0a17cdf-313f-4131-81c4-12c17037fb41"]


def _login() -> str:
    username = os.environ.get("DEMO_USER")
    password = os.environ.get("DEMO_PASS")
    if not username or not password:
        sys.exit("Set DEMO_USER and DEMO_PASS to the reviewer credentials of the running app.")
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/auth/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        cookie = resp.headers.get("Set-Cookie") or ""
    token = cookie.split(";")[0]
    if not token:
        sys.exit("Login succeeded but no session cookie was returned.")
    return token


def _fetch(path: str, cookie: str):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  skip {path} (HTTP {e.code})")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  skip {path} ({type(e).__name__})")
        return None


def _save(rel_path: str, payload) -> None:
    out = API_DIR / rel_path.lstrip("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def capture(cookie: str) -> dict:
    """Pull every read-only endpoint the frontend calls. Returns a manifest the
    demo shim uses to resolve request paths to saved files."""
    manifest = {}

    simple = {
        "/api/auth/me": "auth_me.json",
        "/api/org-profiles": "org_profiles.json",
        "/api/dashboard/stats": "dashboard_stats.json",
        "/api/eval/summary": "eval_summary.json",
        "/api/evaluation-runs?limit=20": "evaluation_runs.json",
    }
    for path, filename in simple.items():
        data = _fetch(path, cookie)
        if data is not None:
            _save(filename, data)
            manifest[path] = filename
            print(f"  saved {path}")

    # Portfolio requires a scope parameter, so capture one entry per org profile.
    # Profiles with fewer than two documents legitimately return no conflicts;
    # whichever profile has real findings becomes the fallback so the Portfolio
    # tab always shows populated output rather than an empty panel.
    profiles = (_fetch("/api/org-profiles", cookie) or {}).get("org_profiles") or []
    best_scope, best_count = None, -1
    for profile in profiles:
        pid = profile.get("profile_id")
        if not pid:
            continue
        query = f"/api/portfolio/conflicts?org_profile_id={pid}"
        data = _fetch(query, cookie)
        if data is None:
            continue
        name = f"portfolio_{pid}.json"
        _save(name, data)
        manifest[query] = name
        count = len(data.get("conflicts") or [])
        if count > best_count:
            best_scope, best_count = name, count
        print(f"  saved portfolio scope {profile.get('name')} ({count} conflicts)")

    if best_scope:
        # Unscoped and empty-parameter variants both resolve to the richest
        # captured scope, so the tab is never blank on first load.
        manifest["/api/portfolio/conflicts"] = best_scope
        manifest["/api/portfolio/conflicts?"] = best_scope
        print(f"  portfolio default: {best_count} conflicts")

    runs = _fetch("/api/evaluation-runs?limit=20", cookie) or {}
    for run in (runs.get("runs") or [])[:3]:
        rid = run.get("evaluation_run_id")
        detail = _fetch(f"/api/evaluation-runs/{rid}", cookie)
        if detail is not None:
            name = f"evaluation_run_{rid}.json"
            _save(name, detail)
            manifest[f"/api/evaluation-runs/{rid}"] = name
            print(f"  saved evaluation run {rid[:8]}")

    documents = []
    for doc_id in FEATURED_DOCUMENTS:
        detail = _fetch(f"/api/documents/{doc_id}", cookie)
        if detail is None:
            continue
        name = f"document_{doc_id}.json"
        _save(name, detail)
        manifest[f"/api/documents/{doc_id}"] = name
        documents.append({"document_id": doc_id, "filename": detail.get("filename")})
        print(f"  saved document {detail.get('filename')}")

        playbook = _fetch(f"/api/documents/{doc_id}/negotiation-playbook", cookie)
        if playbook is not None:
            pname = f"playbook_{doc_id}.json"
            _save(pname, playbook)
            manifest[f"/api/documents/{doc_id}/negotiation-playbook"] = pname
            print(f"    playbook: {playbook.get('summary', {}).get('total_entries', 0)} entries")

        chat = _fetch(f"/api/documents/{doc_id}/chat", cookie)
        if chat is not None:
            cname = f"chat_{doc_id}.json"
            _save(cname, chat)
            manifest[f"/api/documents/{doc_id}/chat"] = cname

    # Audit trails power the Approval and Escalation lookup. Capture the job ids
    # belonging to the featured documents so the ids shown on screen are the same
    # ones that resolve, then alias the first as the default.
    audit_jobs = list(FEATURED_JOBS)
    for doc in documents:
        detail_file = API_DIR / f"document_{doc['document_id']}.json"
        if detail_file.exists():
            job_id = json.loads(detail_file.read_text(encoding="utf-8")).get("job_id")
            if job_id and job_id not in audit_jobs:
                audit_jobs.append(job_id)

    first_audit = None
    for job_id in audit_jobs:
        audit = _fetch(f"/api/audit/{job_id}", cookie)
        if audit is None:
            continue
        name = f"audit_{job_id}.json"
        _save(name, audit)
        manifest[f"/api/audit/{job_id}"] = name
        first_audit = first_audit or name
        print(f"  saved audit trail {job_id[:8]}")

    fallbacks = {}
    if first_audit:
        fallbacks["/api/audit/"] = first_audit
    if documents:
        did = documents[0]["document_id"]
        fallbacks["/api/documents/"] = f"document_{did}.json"
        fallbacks["/api/documents/:id/negotiation-playbook"] = f"playbook_{did}.json"

    payload = {"routes": manifest, "documents": documents, "fallbacks": fallbacks}
    _save("_manifest.json", payload)
    return payload


DEMO_SHIM = """
<script>
// Static demonstration build. There is no backend: apiFetch resolves each
// request against demo/api/_manifest.json and returns a captured response
// recorded from a real run of the application. Write actions (upload, chat,
// approvals, brief generation) require the live backend and are disabled.
window.__DEMO_MODE__ = true;
window.__DEMO_MANIFEST__ = null;

async function __demoManifest() {
  if (!window.__DEMO_MANIFEST__) {
    const res = await fetch("api/_manifest.json");
    window.__DEMO_MANIFEST__ = await res.json();
  }
  return window.__DEMO_MANIFEST__;
}

const __WRITE_BLOCKED = "This action needs the live backend (document processing and Gemini calls). "
  + "The captured screens show real output from an actual run.";

async function apiFetch(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();

  if (path === "/api/auth/login") { return {}; }
  if (path === "/api/auth/logout") { return {}; }

  if (method !== "GET") {
    throw new Error(__WRITE_BLOCKED);
  }

  const manifest = await __demoManifest();
  const bare = path.split("?")[0];
  let file = manifest.routes[path] || manifest.routes[bare];

  // Any document id or job id typed into a lookup box resolves to the captured
  // record, so no screen renders empty just because an unknown id was entered.
  if (!file) {
    const fb = manifest.fallbacks || {};
    if (/^\\/api\\/documents\\/[^/]+\\/negotiation-playbook$/.test(bare)) {
      file = fb["/api/documents/:id/negotiation-playbook"];
    } else if (/^\\/api\\/documents\\/[^/]+$/.test(bare)) {
      file = fb["/api/documents/"];
    } else if (/^\\/api\\/audit\\/[^/]+$/.test(bare)) {
      file = fb["/api/audit/"];
    } else if (bare === "/api/portfolio/conflicts") {
      file = manifest.routes["/api/portfolio/conflicts"];
    }
  }

  if (!file) {
    throw new Error("Not captured in this demonstration build: " + path);
  }
  const res = await fetch("api/" + file);
  if (!res.ok) throw new Error("Missing demo data file: " + file);
  return await res.json();
}
</script>
"""

DEMO_BANNER = """
<div id="demoBanner" style="position:fixed; left:0; right:0; bottom:0; z-index:9999;
  background:#2a1216; border-top:1px solid rgba(240,217,164,.35); color:#f4e4dd;
  font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; font-size:13px;
  padding:9px 16px; display:flex; gap:10px; align-items:center; justify-content:center; text-align:center;">
  <strong style="color:#e2b869; letter-spacing:.04em;">STATIC DEMONSTRATION</strong>
  <span style="opacity:.85;">Screens below show real captured output. Upload, chat and approval
  actions need the live backend and are disabled here.</span>
  <button type="button" onclick="document.getElementById('demoBanner').remove()"
    style="background:none;border:1px solid rgba(244,228,221,.3);color:#f4e4dd;border-radius:6px;
    padding:2px 9px;cursor:pointer;font-size:12px;">Dismiss</button>
</div>
"""


def build_page(manifest: dict) -> None:
    source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Replace the real apiFetch with the static shim. Matching the exact function
    # block keeps the rest of the page byte-identical to the shipped UI.
    pattern = re.compile(
        r"async function apiFetch\(path, options = \{\}\) \{.*?\n\}", re.DOTALL
    )
    if not pattern.search(source):
        sys.exit("Could not locate apiFetch() in index.html; the demo shim was not applied.")
    source = pattern.sub("// apiFetch is provided by the demo shim in <head>.", source, count=1)

    source = source.replace("</head>", DEMO_SHIM + "</head>", 1)
    source = source.replace("</body>", DEMO_BANNER + "</body>", 1)

    # Static hosting serves from the demo root, so absolute asset paths break.
    source = source.replace('src="/static/logo.png"', 'src="logo.png"')
    source = source.replace('href="/static/favicon.png"', 'href="favicon.png"')

    # Preselect a document so Review and the negotiation playbook have content on
    # first load. This has to run before the app's own script reads the URL,
    # because the document context is resolved from ?document= during startup.
    # Injected into <head> ahead of the shim rather than on DOMContentLoaded,
    # which fires too late to influence that read.
    docs = manifest.get("documents") or []
    if docs:
        preselect = (
            "<script>\n"
            "// Static build opens on a captured document so no screen starts empty.\n"
            "(function () {\n"
            f"  var id = {json.dumps(docs[0]['document_id'])};\n"
            "  var u = new URL(window.location.href);\n"
            "  if (!u.searchParams.get('document')) {\n"
            "    u.searchParams.set('document', id);\n"
            "    window.history.replaceState({}, '', u.toString());\n"
            "  }\n"
            "})();\n"
            "</script>\n"
        )
        source = source.replace("</head>", preselect + "</head>", 1)

    (DEMO_DIR / "index.html").write_text(source, encoding="utf-8")
    shutil.copy(STATIC_DIR / "logo.png", DEMO_DIR / "logo.png")
    shutil.copy(STATIC_DIR / "favicon.png", DEMO_DIR / "favicon.png")

    (DEMO_DIR / "vercel.json").write_text(
        json.dumps({"cleanUrls": True, "trailingSlash": False}, indent=1), encoding="utf-8"
    )


def main() -> None:
    # --page-only rebuilds demo/index.html from captures already on disk, so a
    # dropped connection part-way through a capture cannot destroy a good set.
    page_only = "--page-only" in sys.argv
    manifest_path = API_DIR / "_manifest.json"

    if page_only:
        if not manifest_path.exists():
            sys.exit("No existing capture found. Run without --page-only first.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"Reusing {len(manifest['routes'])} captured routes")
    else:
        API_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Capturing from {BASE}")
        cookie = _login()
        manifest = capture(cookie)

    build_page(manifest)

    files = sum(1 for _ in DEMO_DIR.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in DEMO_DIR.rglob("*") if f.is_file())
    print(f"\nBuilt {DEMO_DIR} ({files} files, {size / 1024:.0f} KB)")
    print(f"Captured routes: {len(manifest['routes'])}")
    print("Preview: python -m http.server 5500 --directory demo")


if __name__ == "__main__":
    main()
