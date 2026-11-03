"""Pin only complete three-platform release assets; no credentials are needed."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
VERSION='1.5.0'
REPO='dream-lyc/seedline-upload-skill'
assets=ROOT/'release-assets'
skill=ROOT/'skills/acebro-import-questions'
platforms={}
for target in ('macos-arm64','macos-x64','windows-x64'):
    suffix='zip' if target=='windows-x64' else 'tar.gz'
    name=f'acebro-import-questions-{VERSION}-{target}.{suffix}'
    archive=assets/name
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    expected=(assets/(name+'.sha256')).read_text().split()[0]
    if checksum!=expected: raise SystemExit(f'摘要不符: {name}')
    platforms[target]={'url':f'https://github.com/{REPO}/releases/download/v{VERSION}/{name}','sha256':checksum}
(skill/'assets').mkdir(exist_ok=True)
(skill/'assets/releases.json').write_text(json.dumps({'version':VERSION,'platforms':platforms},indent=2)+'\n')
with zipfile.ZipFile(assets/'acebro-import-questions-skill.zip','w',zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted(skill.rglob('*')):
        if path.is_file(): bundle.write(path,Path('acebro-import-questions')/path.relative_to(skill))
(assets/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(assets.iterdir()) if p.is_file() and not p.name.endswith('.sha256') and p.name!='SHA256SUMS'))
