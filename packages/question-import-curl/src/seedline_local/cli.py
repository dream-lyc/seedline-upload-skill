"""Standalone entrypoint reusing the exact local contracts and durable workflow."""

import argparse
import json
from pathlib import Path
import sys

from question_import_sdk.cli import main as shared_main
from question_import_sdk.config import default_home
from .connection import ADMIN_LOGIN_PATH, DEFAULT_ORIGIN, connection_main

from . import __version__
from .client import CurlClient


def main():
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument('--profile', type=Path, default=default_home()/'profile.json')
    options.add_argument('--cache')
    flags, remaining = options.parse_known_args(sys.argv[1:])
    if remaining and remaining[0] == 'connect':
        connection_main(sys.argv[1:])
        return
    profile = flags.profile.expanduser().absolute()
    pending = profile.parent / ('.'+profile.name+'.connection') / 'state.json'
    if ((profile.name == 'candidate.json' and profile.parent.name.endswith('.connection'))
        or pending.exists() or pending.is_symlink()):
        if remaining and remaining[0] not in {'ids', '--version', '--help', '-h'}:
            print(json.dumps({'status': 'needs_attention', 'message':
                '请先继续或取消原连接；候选配置不能用于题目或媒体操作'}, ensure_ascii=False))
            raise SystemExit(1)
    if remaining and remaining[0] == 'doctor' and not profile.exists() and not profile.is_symlink():
        print(json.dumps({'status': 'connection_required', 'error_kind': 'credentials_missing',
            'resume_available': False, 'website_url': DEFAULT_ORIGIN,
            'login_url': DEFAULT_ORIGIN+ADMIN_LOGIN_PATH,
            'message': '尚未连接题库，请执行 connect start 并在网站确认连接'}, ensure_ascii=False))
        raise SystemExit(1)
    shared_main(
        client_factory=lambda profile, path: CurlClient(path, profile=profile),
        tool_version=__version__,
    )


if __name__ == "__main__":
    main()
