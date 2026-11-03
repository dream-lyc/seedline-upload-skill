"""Recoverable browser connection. Only curl performs platform HTTP requests.

No background process: each continue advances the saved protocol once and returns.
The output projection deliberately contains only public connection information.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import webbrowser
from uuid import UUID

from question_content_contracts.validation import load_json
from question_import_sdk.config import canonical_origin, default_home, load_config, Profile
from question_import_sdk.workflow import REQUIRED_SCOPES

from question_import_sdk.local_files import (check_private, protect_file, sync_directory, open_regular, lock_exclusive, windows_acl)
from .client import CurlClient, request_helper, helper_command

UUID7 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
DEFAULT_ORIGIN = 'https://edu.learningiland.com'
ADMIN_LOGIN_PATH = '/login?role=admin&returnTo=%2Fadmin%2Faccess-keys'


class PrivateStorageError(ValueError):
    """Restricted storage failed its ownership or permission check."""



def is_canonical_uuid(value):
    """Existing account UUIDs retain their version across generator upgrades."""
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('连接响应时间无时区')
    return parsed.timestamp()


def checked(path, *, directory=False):
    try:
        return check_private(path, directory=directory)
    except ValueError as error:
        raise PrivateStorageError(str(error)) from error


def private_bytes(path):
    before = checked(path)
    fd = open_regular(path)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('连接文件在打开时发生变化')
        return stream.read()


def private_read(path):
    return load_json(private_bytes(path))


def sync_dir(path):
    sync_directory(path)


def private_write(path, value):
    checked(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        checked(path)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        protect_file(fd, temporary)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class BootstrapError(ValueError):
    def __init__(self, status, retry_after=5, code=None):
        super().__init__('连接服务暂不可用，请保留原连接并继续；未自动重试')
        self.status = status
        self.code = code
        self.retry_after = retry_after


class BootstrapCurl:
    def request(self, operation, origin, body, request_id=None):
        origin = canonical_origin(origin)
        if operation not in {'init', 'claims', 'ack', 'cancel'}:
            raise ValueError('不支持的连接操作')
        if operation != 'init' and not UUID7.fullmatch(request_id or ''):
            raise ValueError('无效连接标识')
        helper = request_helper().with_name('bootstrap.ps1' if os.name == 'nt' else 'bootstrap.sh')
        with tempfile.TemporaryDirectory(prefix='seedline-connect-http-') as directory:
            root = Path(directory)
            if os.name == 'nt':
                windows_acl(root, 'protect')
            body_path = root/'body.json'
            private_write(body_path, body)
            status_path, retry_path = root/'status', root/'retry'
            try:
                response = subprocess.run([*helper_command(helper), operation, origin, request_id or 'none',
                    str(body_path), str(status_path), str(retry_path)], capture_output=True,
                    timeout=45, check=False)
            except (subprocess.TimeoutExpired, OSError):
                raise BootstrapError(0) from None
            status_text = status_path.read_text() if status_path.is_file() else '000'
            status = int(status_text) if re.fullmatch(r'[0-9]{3}', status_text) else 0
            retry_text = retry_path.read_text().strip() if retry_path.is_file() else ''
            retry = max(5, min(3600, int(retry_text))) if re.fullmatch(r'[0-9]{1,9}', retry_text) else 5
            if response.returncode or not 200 <= status < 300:
                code = None
                try:
                    error = load_json(response.stdout).get('error', {})
                    if error.get('code') in {'connection_not_found', 'connection_expired',
                        'connection_denied', 'account_unavailable', 'connection_conflict',
                        'credential_invalid', 'connection_rate_limited', 'invalid_request',
                        'service_unavailable'}:
                        code = error['code']
                except (ValueError, TypeError, AttributeError):
                    pass
                raise BootstrapError(status, retry, code)
            try:
                payload = load_json(response.stdout)
                if not isinstance(payload['data'], dict):
                    raise ValueError()
                return payload['data']
            except (ValueError, KeyError, TypeError):
                raise ValueError('连接服务返回无效响应，保留原连接后继续') from None


class Connection:
    def __init__(self, profile, *, transport=None, client_factory=None, clock=time.time):
        self.profile = Path(profile).expanduser().absolute()
        # Never resolve the target profile: resolving would hide a symlink.
        self.root = self.profile.parent / ('.'+self.profile.name+'.connection')
        self.state_path = self.root/'state.json'
        self.candidate_path = self.root/'candidate.json'
        self.transport = transport or BootstrapCurl()
        self.client_factory = client_factory or CurlClient
        self.clock = clock

    @contextmanager
    def lock(self):
        new_parent = not self.profile.parent.exists()
        self.profile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == 'nt' and new_parent:
            windows_acl(self.profile.parent, 'protect')
        # Reject path aliases which could otherwise create two locks for one profile.
        if self.profile.parent.resolve() != self.profile.parent:
            raise PrivateStorageError('配置目录不能经由符号链接访问')
        if os.name == 'nt':
            windows_acl(self.profile.parent, 'owner')
        elif self.profile.parent.stat().st_uid != os.getuid() or self.profile.parent.stat().st_mode & 0o022:
            raise PrivateStorageError('配置目录不属于当前用户')
        if self.profile.exists() or self.profile.is_symlink():
            checked(self.profile)
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
            if os.name == 'nt':
                windows_acl(self.root, 'protect')
        checked(self.root, directory=True)
        lock_path = self.root/'lock'
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            checked(lock_path)
            try:
                lock_exclusive(fd)
            except BlockingIOError:
                raise ValueError('已有连接正在执行，请稍后继续同一连接') from None
            yield
        finally:
            os.close(fd)

    def read_state(self):
        return private_read(self.state_path)

    def readonly_state(self):
        # Missing storage is a read result, not a reason to create private state.
        try:
            self.root.lstat()
        except FileNotFoundError:
            return None
        if self.profile.parent.resolve() != self.profile.parent:
            raise PrivateStorageError('配置目录不能经由符号链接访问')
        checked(self.root, directory=True)
        try:
            state = self.read_state()
        except FileNotFoundError:
            return None
        origin = canonical_origin(state['origin'])
        if (origin != state['origin'] or not UUID7.fullmatch(state['id'])
            or state['verification_url'] != origin+'/admin/connect-import?request_id='+state['id']
            or not re.fullmatch(r'[A-Z0-9-]{4,32}', state['user_code'])
            or not re.fullmatch(r'[A-Za-z0-9_-]{43}', state['claim_secret'])
            or state['phase'] not in {'waiting_approval', 'candidate_saved', 'acknowledged', 'activated'}):
            raise ValueError('本机连接记录无效')
        if any(type(state.get(key)) not in {int, float} or not math.isfinite(state[key])
               for key in ('deadline', 'next_poll_at')):
            raise ValueError('本机连接时间记录无效')
        if state['phase'] != 'waiting_approval':
            if not UUID7.fullmatch(state['credential_id']) or not is_canonical_uuid(state['user_id']):
                raise ValueError('本机连接身份记录无效')
            timestamp(state['authorized_expires_at'])
        return state

    def error_result(self, error, *, origin=None):
        state = None
        try:
            state = self.readonly_state()
        except Exception:
            pass
        resume = bool(state and (
            state['phase'] != 'waiting_approval' or self.clock() < state['deadline']))
        if resume and state['phase'] != 'waiting_approval':
            try:
                self.candidate(state)
                resume = self.clock() < timestamp(state['authorized_expires_at'])
            except Exception:
                resume = False
        try:
            site = canonical_origin(origin or (state['origin'] if state else DEFAULT_ORIGIN))
        except (ValueError, TypeError):
            site = None
        if isinstance(error, (PermissionError, PrivateStorageError)) or (
            isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EPERM, errno.ELOOP}
        ):
            kind = 'local_permission_denied'
            message = '本机连接目录未获当前执行器访问权限，请由助手申请该目录权限后再连接'
        elif isinstance(error, BootstrapError):
            if error.status == 0:
                kind = 'bootstrap_network_unavailable'
                message = '暂时无法连到题库服务，请检查站点与服务连接后再试'
            elif error.status == 429:
                kind = 'bootstrap_rate_limited'
                message = '连接请求过于频繁，请稍后再试'
            elif error.status >= 500:
                kind = 'bootstrap_service_unavailable'
                message = '题库连接服务暂时异常，请由助手检查服务状态后再试'
            else:
                kind = 'connection_protocol_error'
                message = '题库未接受本次连接，请由助手检查站点和连接状态'
        elif state and state['phase'] in {'acknowledged', 'activated'}:
            kind = 'identity_verification_failed'
            message = '已确认保存，身份验证尚未通过，请继续原连接检查身份'
        elif isinstance(error, (ValueError, KeyError, TypeError, OSError)):
            kind = 'local_state_invalid'
            message = '连接记录或本地工具暂不可用，请由助手检查后再连接'
        else:
            kind = 'unexpected_error'
            message = '连接暂未完成，请由助手检查后再试'
        if resume:
            message += '；可继续已保存的原连接'
        else:
            message += '；尚未确认有可恢复的本机连接，请勿假定已完成授权'
        result = dict(status='needs_attention', error_kind=kind, message=message,
                      resume_available=resume)
        if site:
            result.update(website_url=site, login_url=site+ADMIN_LOGIN_PATH)
        if resume:
            result.update(verification_url=state['verification_url'], user_code=state['user_code'])
        return result

    def save(self, state):
        private_write(self.state_path, state)

    def clear(self):
        for path in (self.candidate_path, self.state_path):
            if path.exists() or path.is_symlink():
                checked(path)
                path.unlink()
        sync_dir(self.root)

    def public(self, state, *, message=None):
        result = dict(status=state['phase'], verification_url=state['verification_url'],
                      user_code=state['user_code'],
                      next_poll_seconds=max(0, math.ceil(state.get('next_poll_at', 0)-self.clock())))
        if message:
            result['message'] = message
        return result

    def binding(self, batch, origin):
        if batch is None:
            return None
        value = load_json((Path(batch)/'ledger.json').read_bytes())
        if value.get('ledger_version') != 1 or value.get('origin') != origin or not value.get('user_id'):
            raise ValueError('批次站点或身份绑定无效，不能跨环境连接')
        return {'origin': origin, 'user_id': value['user_id']}

    def run(self, action, *, origin=None, client_kind='codex', open_browser=False,
            batch=None, environment=None):
        if action == 'status':
            state = self.readonly_state()
            if state is None:
                return dict(status='not_started', message='尚未开始连接，请执行 connect start')
            if origin is not None and canonical_origin(origin) != state['origin']:
                raise ValueError('已有连接绑定另一站点')
            return self.public(state)
        with self.lock():
            state = self.read_state() if self.state_path.exists() or self.state_path.is_symlink() else None
            if state:
                if origin is not None and canonical_origin(origin) != state['origin']:
                    raise ValueError('已有连接绑定另一站点，请先完成或取消原连接')
                expected = self.binding(batch, state['origin'])
                if expected and state.get('binding') not in (None, expected):
                    raise ValueError('已有连接绑定另一批次身份')
                if expected:
                    state['binding'] = expected
                if environment and state.get('environment') not in (None, environment):
                    raise ValueError('已有连接环境不匹配')
                if environment:
                    state['environment'] = environment
                self.save(state)
            if action == 'start' and state is None:
                origin = canonical_origin(origin or DEFAULT_ORIGIN)
                if client_kind not in {'codex', 'workbuddy'}:
                    raise ValueError('无效客户端类型')
                binding = self.binding(batch, origin)
                known_path = self.root/'identity.json'
                if known_path.exists() or known_path.is_symlink():
                    known = private_read(known_path)
                    if (known.get('origin') == origin and
                        known.get('profile_fingerprint') == self.profile_fingerprint()):
                        if environment and environment != known.get('environment'):
                            raise ValueError('已有配置的环境与预期不一致')
                        environment = known.get('environment')
                raw_secret = secrets.token_bytes(32)
                def encode(value):
                    return base64.urlsafe_b64encode(value).decode().rstrip('=')
                started = self.clock()
                try:
                    data = self.transport.request('init', origin, dict(client_kind=client_kind,
                        claim_challenge=encode(hashlib.sha256(raw_secret).digest())))
                except BootstrapError as error:
                    if error.status in {404, 405, 501}:
                        return dict(status='manual_required', manual_url=origin+'/admin/access-keys',
                                    message='该站点尚不支持网站连接，请使用手动接入备用入口')
                    raise
                request_id = data.get('id', '')
                if not isinstance(request_id, str) or not UUID7.fullmatch(request_id):
                    raise ValueError('连接服务返回无效标识')
                url = origin+'/admin/connect-import?request_id='+request_id
                if data.get('verification_url') != url:
                    raise ValueError('连接服务返回非本站专用链接，已停止')
                code = data.get('user_code')
                if not isinstance(code, str) or not re.fullmatch(r'[A-Z0-9-]{4,32}', code):
                    raise ValueError('无效连接核对码')
                deadline = min(started+600, timestamp(data['expires_at']))
                if deadline <= self.clock():
                    raise ValueError('连接已过期，请重新连接')
                interval = self.interval(data)
                state = dict(phase='waiting_approval', origin=origin, id=request_id,
                    verification_url=url, user_code=code, claim_secret=encode(raw_secret),
                    deadline=deadline, next_poll_at=self.clock()+interval, binding=binding,
                    environment=environment, initial_profile=self.profile_fingerprint())
                self.save(state)
            if state is None:
                return dict(status='not_started', message='请先开始连接')
            if open_browser:
                expected_url = state['origin']+'/admin/connect-import?request_id='+state['id']
                if state['verification_url'] != expected_url:
                    raise ValueError('本机连接链接不匹配')
                try:
                    webbrowser.open(expected_url, new=2)
                except Exception:
                    pass  # The exact validated clickable URL is always returned.
            if action in {'start', 'status'}:
                return self.public(state)
            proof = {'claim_secret': state['claim_secret']}
            if action == 'cancel':
                try:
                    data = self.transport.request('cancel', state['origin'], proof, state['id'])
                except BootstrapError as error:
                    if error.code == 'connection_conflict':
                        return self.public(state, message='连接可能已完成，请继续原连接；如需吊销请在网站API接入页操作')
                    return self.terminal_or_raise(error)
                if data.get('state') != 'cancelled':
                    raise ValueError('服务尚未确认取消，请继续原连接')
                self.clear()
                return dict(status='cancelled')
            if action != 'continue':
                raise ValueError('不支持的连接命令')
            if state['phase'] == 'waiting_approval':
                if self.clock() >= state['deadline']:
                    self.clear()
                    return dict(status='expired', message='连接已过期，请重新连接')
                if self.clock() < state['next_poll_at']:
                    return self.public(state)
                # Persist before sending, so a crash or lost response cannot accelerate polls.
                state['next_poll_at'] = self.clock()+5
                self.save(state)
                try:
                    data = self.transport.request('claims', state['origin'], proof, state['id'])
                except BootstrapError as error:
                    if error.status == 429:
                        state['next_poll_at'] = self.clock()+error.retry_after
                        self.save(state)
                        return self.public(state, message='请稍后继续连接')
                    if error.status in {403, 404, 410}:
                        self.clear()
                        return dict(status='unavailable', message='连接已拒绝、失效或不可用，请重新连接')
                    raise
                if data.get('state') in {'pending', 'approved'}:
                    state['next_poll_at'] = self.clock()+self.interval(data)
                    self.save(state)
                    return self.public(state)
                if data.get('state') in {'denied', 'cancelled', 'expired'}:
                    self.clear()
                    return dict(status=data['state'])
                if data.get('state') != 'delivering':
                    raise ValueError('连接服务返回无效阶段')
                credential = data.get('credential')
                self.validate_credential(credential)
                candidate = dict(origin=state['origin'], access_key=credential['access_key'],
                                 secret_key=credential['secret_key'])
                private_write(self.candidate_path, candidate)
                state.update(phase='candidate_saved', credential_id=credential['id'],
                    user_id=credential['user_id'], scopes=credential['scopes'],
                    authorized_expires_at=credential['authorized_expires_at'],
                    candidate_fingerprint=self.file_fingerprint(self.candidate_path), next_poll_at=0)
                self.save(state)
                return self.public(state)
            if state['phase'] == 'candidate_saved':
                if self.clock() < state.get('next_poll_at', 0):
                    return self.public(state)
                self.candidate(state)
                # Never expire this stage by the old deadline: ack may already have completed.
                try:
                    data = self.transport.request('ack', state['origin'], dict(proof,
                        credential_id=state['credential_id']), state['id'])
                except BootstrapError as error:
                    if error.status == 429:
                        state['next_poll_at'] = self.clock()+error.retry_after
                        self.save(state)
                        return self.public(state, message='请稍后继续连接')
                    return self.terminal_or_raise(error)
                if data.get('state') != 'completed' or data.get('credential_id') != state['credential_id']:
                    raise ValueError('服务尚未确认保存，请继续原连接')
                if timestamp(data['expires_at']) != timestamp(state['authorized_expires_at']):
                    raise ValueError('确认有效期与网页批准不一致')
                state['phase'] = 'acknowledged'
                state['next_poll_at'] = 0
                self.save(state)
                return self.public(state)
            if state['phase'] in {'acknowledged', 'activated'}:
                candidate = self.candidate(state)
                identity_path = self.candidate_path if self.candidate_path.exists() else self.profile
                client = self.client_factory(identity_path)
                try:
                    me = client.me()
                finally:
                    client.close()
                if (me.get('role') != 'admin' or me.get('user_id') != state['user_id']
                    or me.get('credential_id') != state['credential_id']
                    or set(me.get('scopes', [])) != set(state['scopes'])
                    or not REQUIRED_SCOPES.issubset(me.get('scopes', []))
                    or not isinstance(me.get('environment'), str) or not me['environment']
                    or (state.get('environment') and me['environment'] != state['environment'])):
                    raise ValueError('连接身份、环境或完整权限不匹配，原配置保留')
                binding = state.get('binding')
                if binding and (binding['origin'] != state['origin'] or binding['user_id'] != me['user_id']):
                    raise ValueError('连接账号与原批次不匹配，原配置保留')
                current = self.profile_fingerprint()
                if current not in {state['initial_profile'], state['candidate_fingerprint']}:
                    raise ValueError('原配置已由其他会话更改，请先核对，禁止覆盖')
                state['environment'] = me['environment']
                # Formal profile parent may be 0755. Atomic replacement still writes 0600.
                self.activate(candidate)
                private_write(self.root/'identity.json', dict(origin=state['origin'],
                    environment=me['environment'], user_id=me['user_id'],
                    credential_id=me['credential_id'], scopes=sorted(me['scopes']),
                    profile_fingerprint=state['candidate_fingerprint']))
                state['phase'] = 'activated'
                self.save(state)
                self.clear()
                return dict(status='activated', message='题库已连接')
            raise ValueError('无效本机连接阶段')

    def terminal_or_raise(self, error):
        if error.status in {403, 404, 410} or error.code == 'credential_invalid':
            self.clear()
            return dict(status='unavailable', message='连接已到期、取消或不可用，请重新连接')
        raise error

    @staticmethod
    def interval(data):
        value = data.get('poll_interval_seconds')
        if type(value) is not int or not 5 <= value <= 3600:
            raise ValueError('无效轮询间隔')
        return value

    @staticmethod
    def validate_credential(value):
        if not isinstance(value, dict):
            raise ValueError('无效领取响应')
        if (not isinstance(value.get('id'), str) or not UUID7.fullmatch(value['id'])
            or not is_canonical_uuid(value.get('user_id'))):
            raise ValueError('无效凭据身份')
        Profile(DEFAULT_ORIGIN, value.get('access_key'), value.get('secret_key'))
        if not isinstance(value.get('access_key'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value['access_key']):
            raise ValueError('无效凭据标识')
        scopes = value.get('scopes')
        if not isinstance(scopes, list) or not all(isinstance(v, str) for v in scopes) or set(scopes) != REQUIRED_SCOPES or len(scopes) != len(REQUIRED_SCOPES):
            raise ValueError('领取权限与固定题目录入权限不一致')
        timestamp(value['expires_at'])
        timestamp(value['authorized_expires_at'])

    @staticmethod
    def file_fingerprint(path):
        return hashlib.sha256(private_bytes(path)).hexdigest()

    def profile_fingerprint(self):
        if not self.profile.exists() and not self.profile.is_symlink():
            return None
        return self.file_fingerprint(self.profile)

    def candidate(self, state):
        path = self.candidate_path
        if not path.exists() and not path.is_symlink() and state['phase'] == 'activated':
            path = self.profile
        value = private_read(path)
        if self.file_fingerprint(path) != state['candidate_fingerprint'] or value.get('origin') != state['origin']:
            raise ValueError('候选配置与原连接不匹配')
        load_config(path)
        return value

    def activate(self, candidate):
        # Keep candidate intact through state-save/cleanup, enabling crash recovery.
        fd, path = tempfile.mkstemp(prefix='.'+self.profile.name, dir=self.profile.parent)
        try:
            protect_file(fd, path)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(private_bytes(self.candidate_path).decode() if self.candidate_path.exists()
                             else private_bytes(self.profile).decode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.profile)
            sync_dir(self.profile.parent)
        finally:
            if os.path.exists(path):
                os.unlink(path)


def connection_main(argv):
    parser = argparse.ArgumentParser(description='连接题库，请在网站确认')
    parser.add_argument('--profile', type=Path, default=default_home()/'profile.json')
    parser.add_argument('--cache', type=Path)  # Compatible global option; unused here.
    parser.add_argument('command', choices=['connect'])
    parser.add_argument('action', choices=['start', 'continue', 'status', 'cancel'])
    parser.add_argument('--origin')
    parser.add_argument('--client-kind', choices=['codex', 'workbuddy'], default='codex')
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--batch', type=Path)
    parser.add_argument('--environment', choices=['development', 'test', 'production'])
    args = parser.parse_args(argv)
    connection = Connection(args.profile)
    try:
        value = connection.run(args.action, origin=args.origin,
            client_kind=args.client_kind, open_browser=args.open, batch=args.batch,
            environment=args.environment)
        print(json.dumps(value, ensure_ascii=False))
    except Exception as error:
        # Only fixed categories and validated public URLs may cross the CLI boundary.
        print(json.dumps(connection.error_result(error, origin=args.origin), ensure_ascii=False))
        raise SystemExit(1) from None
