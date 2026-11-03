"""Package a platform-specific, credential-free release asset."""
import hashlib
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT/'packages/question-import-curl'
SOURCE = ROOT/'skills/acebro-import-questions'
if not SOURCE.exists():
    SOURCE = ROOT/'.codex/skills/acebro-import-questions'
NATIVE = PROJECT/'dist/acebro-local'
VERSION = '1.5.0'


def build():
    manifest = json.loads((NATIVE/'native-manifest.json').read_text())
    target = manifest['platform']
    if manifest['tool_version'] != VERSION:
        raise SystemExit('本地工具版本不符，请先构建当前版本')
    stage = PROJECT/'dist'/target/'acebro-import-questions'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(SOURCE/'SKILL.md',stage/'SKILL.md')
    shutil.copytree(SOURCE/'references',stage/'references')
    if (SOURCE/'agents').exists():
        shutil.copytree(SOURCE/'agents',stage/'agents')
    (stage/'scripts').mkdir()
    for name in ('run.sh','run.ps1','request.sh','signing.sh','bootstrap.sh','request.ps1','bootstrap.ps1','windows_support.ps1'):
        shutil.copy2(PROJECT/'scripts'/name,stage/'scripts'/name)
    shutil.copytree(NATIVE,stage/'assets'/target/'acebro-local',symlinks=True)
    if (ROOT/'THIRD_PARTY_NOTICES').exists():
        shutil.copytree(ROOT/'THIRD_PARTY_NOTICES',stage/'assets/licenses/preview')
    if (ROOT/'LICENSE').is_file():
        shutil.copy2(ROOT/'LICENSE',stage/'LICENSE')
    (stage/'assets/distribution.json').write_text(json.dumps({
        'skill':'acebro-import-questions','version':VERSION,'platform':target,
        'origin':'https://edu.learningiland.com','transport':'curl'},indent=2)+'\n')
    # Platform launcher detects an installed complete package without network access.
    sums=[]
    for path in sorted(stage.rglob('*')):
        if path.is_file():
            sums.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(stage).as_posix()}\n')
    (stage/'SHA256SUMS').write_text(''.join(sums))
    stem=f'acebro-import-questions-{VERSION}-{target}'
    if target == 'windows-x64':
        archive=PROJECT/'dist'/f'{stem}.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(stage.rglob('*')):
                if path.is_file(): bundle.write(path,Path('acebro-import-questions')/path.relative_to(stage))
    else:
        archive=PROJECT/'dist'/f'{stem}.tar.gz'
        with tarfile.open(archive,'w:gz') as bundle:
            bundle.add(stage,arcname='acebro-import-questions')
    (archive.parent/(archive.name+'.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(archive)

if __name__ == '__main__': build()
