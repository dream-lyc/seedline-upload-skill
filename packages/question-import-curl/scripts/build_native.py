"""Developer-only build. Never install packages into an Agent client's runtime."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'packages/question-import-curl'
VERSION = '1.5.0'


def current_platform():
    machine = platform.machine().lower()
    if sys.platform == 'darwin' and machine in {'arm64', 'x86_64'}:
        return 'macos-arm64' if machine == 'arm64' else 'macos-x64'
    if sys.platform == 'win32' and machine in {'amd64', 'x86_64'}:
        return 'windows-x64'
    raise SystemExit('仅支持 Windows x64、macOS x64 和 macOS arm64 的原生构建')


def collect_licenses(stage):
    target = stage / 'licenses'
    target.mkdir()
    records = []
    for name in ('pydantic', 'pydantic-core', 'annotated-types', 'typing-extensions',
                 'typing-inspection', 'jsonschema', 'jsonschema-specifications', 'attrs',
                 'referencing', 'rpds-py', 'httpx', 'httpcore', 'anyio', 'certifi',
                 'h11', 'idna', 'pillow', 'pyinstaller', 'setuptools'):
        dist = importlib.metadata.distribution(name)
        files = []
        for item in dist.files or []:
            if any(part.lower().startswith(('license', 'copying', 'notice')) for part in item.parts):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    dest = target / name / str(item)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    files.append(dest.relative_to(stage).as_posix())
        records.append({'name': name, 'version': dist.version, 'license_files': files})
    for base in (Path(sys.base_prefix), Path(sysconfig.get_path('stdlib'))):
        for name in ('LICENSE.txt', 'LICENSE'):
            if (base / name).is_file():
                shutil.copy2(base / name, target / 'PYTHON-LICENSE.txt')
                break
    (target / 'components.json').write_text(json.dumps(records, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description='构建免安装题目录入工具（开发者专用）')
    parser.add_argument('--output', type=Path, default=PACKAGE/'dist')
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix or not (Path(sys.prefix)/'pyvenv.cfg').is_file():
        parser.error('必须使用独立且完整的构建虚拟环境；禁止使用 Codex/WorkBuddy 的共享 Python')
    target = current_platform()
    output = args.output.resolve()
    sources = [PACKAGE/'src', ROOT/'packages/question-import-sdk/src',
               ROOT/'packages/question-content-contracts/src']
    sdk = sources[1]/'question_import_sdk'
    with tempfile.TemporaryDirectory(prefix='acebro-freeze-') as work:
        command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                   '--onedir', '--name', 'acebro-local', '--distpath', str(output),
                   '--workpath', str(Path(work)/'build'), '--specpath', work]
        for source in sources:
            command += ['--paths', str(source)]
        command += [
            '--add-data', str(sdk/'assets')+os.pathsep+'question_import_sdk/assets',
            '--add-data', str(sdk/'windows_acl.ps1')+os.pathsep+'question_import_sdk',
            '--collect-data', 'question_content_contracts',
            '--collect-data', 'jsonschema_specifications',
            '--hidden-import', 'PIL.PngImagePlugin', '--hidden-import', 'PIL.JpegImagePlugin',
            '--hidden-import', 'PIL.WebPImagePlugin', str(PACKAGE/'scripts/native_entry.py')]
        subprocess.run(command, check=True, env=dict(os.environ, PYINSTALLER_CONFIG_DIR=str(Path(work)/'cache')))
    stage = output/'acebro-local'
    scripts = stage/'scripts'
    scripts.mkdir(exist_ok=True)
    for name in ('request.sh','signing.sh','bootstrap.sh','request.ps1','bootstrap.ps1','windows_support.ps1'):
        shutil.copy2(PACKAGE/'scripts'/name, scripts/name)
    collect_licenses(stage)
    manifest = {
        'tool_version': VERSION, 'platform': target, 'transport': 'curl',
        'validator_version': '1.0.0', 'preview_version': '1.0.0',
        'external_language_runtime_required': False,
        'files': {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(stage.rglob('*')) if p.is_file() and not p.is_symlink()
                  and p.name != 'native-manifest.json'},
    }
    (stage/'native-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(stage)

if __name__ == '__main__':
    main()
