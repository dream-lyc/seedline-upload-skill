"""Run the actual frozen binary without Python or Node on PATH."""

import base64
import json
import hmac
import re
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from question_content_contracts.contracts import build_contract
from question_content_contracts.examples import ielts_question, toefl_item
from question_import_sdk.config import save_config
from question_import_sdk.workflow import REQUIRED_SCOPES

NATIVE = Path(__file__).resolve().parents[1] / "dist/acebro-local" / ("acebro-local.exe" if os.name == "nt" else "acebro-local")
pytestmark = pytest.mark.skipif(not NATIVE.is_file(), reason="先运行开发构建生成当前平台原生工具")


def no_language_path(tmp_path):
    if os.name == "nt":
        system = Path(os.environ['SystemRoot']) / 'System32'
        return dict(os.environ, PATH=str(system)+';'+str(system/'WindowsPowerShell/v1.0'), PYTHONUTF8='1')
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("curl", "openssl", "jq", "od", "tr", "sed", "stat", "mktemp", "dirname", "date", "cat", "rm", "awk", "tail"):
        source = shutil.which(name)
        assert source, name
        (tools / name).symlink_to(source)
    assert not shutil.which("python3", path=str(tools))
    assert not shutil.which("node", path=str(tools))
    return dict(os.environ, PATH=str(tools))


def run_native(environment, *args):
    result = subprocess.run([str(NATIVE), *map(str, args)], env=environment,
                            capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    return result.stdout.decode()


def test_native_frozen_help_ids_and_version_need_no_external_language_runtime(tmp_path):
    environment = no_language_path(tmp_path)
    assert "validate" in run_native(environment, "--help")
    assert len(json.loads(run_native(environment, "ids", "--count", "2"))) == 2
    assert run_native(environment, "--version").strip() == "1.5.0"


@pytest.mark.parametrize("exam,candidate", [
    ("ielts", ielts_question("writing")),
    ("toefl", toefl_item("read_daily_life")),
])
def test_native_frozen_prepare_validate_run_query_and_preview_without_python_node(
    tmp_path, exam, candidate,
):
    environment = no_language_path(tmp_path)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200):
            raw = json.dumps({"request_id": "native-fixture", "data": data}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def verify_signature(self, body=b''):
            params = self.headers['Signature-Input'].removeprefix('sig1=')
            names = re.findall(r'"([^"]+)"', params.split(')', 1)[0])
            fields = {key.lower(): value for key, value in self.headers.items()}
            fields.update({'@method': self.command, '@target-uri': f'http://127.0.0.1:{self.server.server_port}'+self.path})
            canonical = '\n'.join([f'"{name}": {fields[name]}' for name in names] + ['"@signature-params": '+params])
            expected = base64.b64encode(hmac.digest(bytes(range(32)), canonical.encode(), 'sha256')).decode()
            assert self.headers['Signature'] == 'sig1=:'+expected+':'

        def do_GET(self):
            self.verify_signature()
            requests.append(("GET", self.path))
            if self.path.endswith("/me"):
                self.respond({"role": "admin", "user_id": "u", "scopes": list(REQUIRED_SCOPES), "limits": {}})
            elif "/contracts/" in self.path:
                self.respond(build_contract(exam, "1.0.0"))
            else:
                self.respond({"records": [], "total": 0})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            self.verify_signature(raw)
            requests.append(("POST", self.path))
            assert json.loads(raw)["objects"][0]["id"] == candidate["id"]
            self.respond({"state": "succeeded", "created_count": 1, "objects": []}, 201)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = tmp_path / "profile.json"
        save_config(profile, f"http://127.0.0.1:{server.server_port}", "ak_fixture",
                    base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="))
        source = tmp_path / "candidate.json"
        source.write_text(json.dumps({"exam": exam, "objects": [candidate]}))
        batch = tmp_path / "batch"
        common = ["--profile", profile, "--cache", tmp_path / "cache"]
        run_native(environment, *common, "prepare", source, "--batch", batch)
        assert json.loads(run_native(environment, *common, "validate", batch)) == {"issues": []}
        waiting = json.loads(run_native(environment, *common, "run", batch))
        assert waiting["state"] == "awaiting_confirmation"
        assert sum(method == "POST" for method, _ in requests) == 0
        shown = json.loads(run_native(environment, *common, "preview", batch))
        # A malformed question must not become publishable on any platform.
        invalid = json.loads(source.read_text())
        invalid['objects'][0]['data'] = {}
        bad_source = tmp_path / 'invalid.json'
        bad_source.write_text(json.dumps(invalid))
        bad_batch = tmp_path / 'invalid-batch'
        run_native(environment, *common, 'prepare', bad_source, '--batch', bad_batch)
        assert json.loads(run_native(environment, *common, 'validate', bad_batch))['issues']
        rejected = subprocess.run([str(NATIVE), *map(str, common), 'confirm', str(bad_batch),
                                   '--preview', shown['preview_open_file']], env=environment,
                                  capture_output=True, timeout=120)
        assert rejected.returncode != 0
        assert sum(method == 'POST' for method, _ in requests) == 0
        confirmed = json.loads(run_native(environment, *common, "confirm", batch,
                                          "--preview", shown["preview_open_file"]))
        assert confirmed["confirmation"]["state"] == "confirmed"
        report = json.loads(run_native(environment, *common, "run", batch))
        assert report["content"]["state"] == "succeeded"
        run_native(environment, *common, "run", batch)
        run_native(environment, *common, "query", batch)
        assert sum(method == "POST" for method, _ in requests) == 1
        saved = json.loads(run_native(environment, *common, "preview", batch))
        page = Path(saved["preview_file"])
        assert page.is_file()
        open_page = Path(saved["preview_open_file"])
        assert open_page != page and open_page.parent == page.parent
        assert open_page.read_bytes() == page.read_bytes()
        assert 'id="acebro-preview-data"' in page.read_text()
        assert "connect-src 'none'" in page.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_website_connection_saves_private_profile_and_restores_next_session(tmp_path):
    """Exercise the actual platform bootstrap, private storage, and signed /me."""
    import time
    from datetime import UTC, datetime, timedelta

    environment = no_language_path(tmp_path)
    request_id = '019ab7e1-1234-7000-8000-000000000001'
    credential_id = '019ab7e1-1234-7000-8000-000000000002'
    user_id = '019ab7e1-1234-7000-8000-000000000003'
    key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
    expires = (datetime.now(UTC)+timedelta(minutes=10)).isoformat()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, data):
            raw = json.dumps({'request_id': 'fixture', 'data': data}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(self.path)
            if self.path == '/api/agent-connections':
                assert data['client_kind'] == 'workbuddy'
                self.respond(dict(id=request_id, user_code='ABCD-EFGH', expires_at=expires,
                    poll_interval_seconds=5, verification_url=f'http://127.0.0.1:{self.server.server_port}/admin/connect-import?request_id={request_id}'))
            else:
                assert len(data['claim_secret']) == 43
                self.respond(dict(state='delivering', credential=dict(id=credential_id,
                    user_id=user_id, access_key='ak_fixture', secret_key=key,
                    scopes=sorted(REQUIRED_SCOPES), expires_at=expires, authorized_expires_at=expires)))
        def do_PUT(self):
            self.rfile.read(int(self.headers['Content-Length']))
            calls.append(self.path)
            self.respond(dict(state='completed', credential_id=credential_id, expires_at=expires))
        def do_GET(self):
            assert self.headers['Signature']
            self.respond(dict(user_id=user_id, credential_id=credential_id, role='admin',
                              scopes=sorted(REQUIRED_SCOPES), environment='development'))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = tmp_path / '教师连接' / 'profile.json'
        common = ['--profile', profile]
        if os.name == 'nt':
            # Isolated bootstrap probe emits only synthetic responses and sanitized errors.
            from question_import_sdk.config import atomic_private_json
            probe = tmp_path / 'probe.json'
            atomic_private_json(probe, {'client_kind': 'workbuddy', 'claim_challenge': 'a'*43})
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(NATIVE.parent/'scripts/bootstrap.ps1'),
                'init', f'http://127.0.0.1:{server.server_port}', 'none', str(probe),
                str(tmp_path/'probe-status'), str(tmp_path/'probe-retry')],
                env=environment, capture_output=True, timeout=90)
            assert result.returncode == 0, result.stderr.decode(errors='replace')
            calls.clear()
        start = json.loads(run_native(environment, *common, 'connect', 'start', '--origin',
                                      f'http://127.0.0.1:{server.server_port}', '--client-kind', 'workbuddy'))
        assert start['status'] == 'waiting_approval'
        assert start['verification_url'].startswith(f'http://127.0.0.1:{server.server_port}/admin/connect-import?')
        time.sleep(5)
        for state in ('candidate_saved', 'acknowledged', 'activated'):
            result = run_native(environment, *common, 'connect', 'continue')
            assert key not in result
            assert json.loads(result)['status'] == state
        assert json.loads(run_native(environment, *common, 'doctor'))['identity']['user_id'] == user_id
        assert len(calls) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
