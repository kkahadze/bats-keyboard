#!/usr/bin/env python3
"""Validate the bundle and generate a self-contained preview and reproducible ZIP."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import plistlib
import re
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'Bats.bundle'
LAYOUT = BUNDLE / 'Contents/Resources/Bats.keylayout'
# Apple virtual key codes, in physical ANSI keyboard order.
ROWS = [
    [(50, '`'), (18, '1'), (19, '2'), (20, '3'), (21, '4'), (23, '5'), (22, '6'), (26, '7'), (28, '8'), (25, '9'), (29, '0'), (27, '-'), (24, '=')],
    [(12, 'Q'), (13, 'W'), (14, 'E'), (15, 'R'), (17, 'T'), (16, 'Y'), (32, 'U'), (34, 'I'), (31, 'O'), (35, 'P'), (33, '['), (30, ']'), (42, '\\')],
    [(0, 'A'), (1, 'S'), (2, 'D'), (3, 'F'), (5, 'G'), (4, 'H'), (38, 'J'), (40, 'K'), (37, 'L'), (41, ';'), (39, "'")],
    [(6, 'Z'), (7, 'X'), (8, 'C'), (9, 'V'), (11, 'B'), (45, 'N'), (46, 'M'), (43, ','), (47, '.'), (44, '/')],
]
LAYERS = ['Base', 'Shift', 'Caps Lock / Command', 'Option', 'Shift + Option', 'Caps Lock + Option', 'Option + Command', 'Control combinations', 'Shift + Caps Lock / Command']


def parse_layout(source):
    # ElementTree understands XML 1.0, while Apple's XML 1.1 layout legitimately
    # contains control references. Substitute only those during parsing, then
    # restore their exact values. The distributable source is never rewritten.
    if any(0xE000 <= ord(char) <= 0xE01F for char in source):
        raise ValueError('Layout uses reserved parser placeholder characters')
    def safe_reference(match):
        token = match[1]
        value = int(token[1:], 16) if token.startswith('x') else int(token)
        return chr(0xE000 + value) if value < 32 and value not in (9, 10, 13) else match[0]
    root = ET.fromstring(re.sub(r'&#(x[0-9a-fA-F]+|[0-9]+);', safe_reference, source))
    if root.tag != 'keyboard' or root.get('name') != 'Bats':
        raise ValueError('Expected the Bats keyboard')
    maps = {}
    for map_set in root.findall('keyMapSet'):
        set_id = map_set.attrib['id']
        if any(key[0] == set_id for key in maps):
            raise ValueError(f'Duplicate map set: {set_id}')
        for key_map in map_set.findall('keyMap'):
            key = (set_id, key_map.attrib['index'])
            if key in maps:
                raise ValueError(f'Duplicate map: {key}')
            maps[key] = key_map
    resolved = {}
    def resolve(key, stack=()):
        if key in stack:
            raise ValueError(f'Inheritance cycle: {key}')
        if key not in maps:
            raise ValueError(f'Missing map: {key}')
        if key in resolved:
            return resolved[key]
        node = maps[key]
        result = {}
        if 'baseMapSet' in node.attrib:
            result.update(resolve((node.attrib['baseMapSet'], node.attrib['baseIndex']), stack + (key,)))
        seen = set()
        for item in node.findall('key'):
            code = int(item.attrib['code'])
            if code in seen or not 0 <= code <= 127:
                raise ValueError(f'Invalid or duplicate key code {code} in {key}')
            seen.add(code)
            if 'output' not in item.attrib:
                raise ValueError('Preview does not yet support action/dead keys')
            result[str(code)] = ''.join(chr(ord(c) - 0xE000) if 0xE000 <= ord(c) <= 0xE01F else c for c in item.attrib['output'])
        resolved[key] = result
        return result
    modifiers = {node.attrib['id']: node for node in root.findall('modifierMap')}
    for layout in root.findall('layouts/layout'):
        modifier = modifiers.get(layout.attrib['modifiers'])
        if modifier is None:
            raise ValueError('Missing modifier map')
        resolve((layout.attrib['mapSet'], modifier.attrib['defaultIndex']))
        for selection in modifier.findall('keyMapSelect'):
            resolve((layout.attrib['mapSet'], selection.attrib['mapIndex']))
    for key in maps:
        resolve(key)
    # The preview exposes the original layout's nine layers. Fail explicitly if
    # future edits change this contract instead of silently mislabelling them.
    if set(maps) != {(s, str(i)) for s in ('16c', 'a88') for i in range(9)}:
        raise ValueError('Update preview layers for the changed map structure')
    return {s: {i: resolved[(s, i)] for ss, i in maps if ss == s} for s in ('16c', 'a88')}


def validate_bundle():
    info = plistlib.loads((BUNDLE / 'Contents/Info.plist').read_bytes())
    version = info['CFBundleShortVersionString']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or info['CFBundleVersion'] != version:
        raise ValueError('Bundle version must be a consistent semantic version')
    if info['CFBundleIdentifier'] != 'org.sil.ukelele.keyboardlayout.bats':
        raise ValueError('Preserve the installed bundle identifier')
    if info['KLInfo_Bats']['TISIntendedLanguage'] != 'bbl-Geor-GE':
        raise ValueError('Unexpected intended language')
    metadata = plistlib.loads((BUNDLE / 'Contents/version.plist').read_bytes())
    if metadata['SourceVersion'] != version or metadata['BuildVersion'] != version:
        raise ValueError('version.plist is out of sync')
    icon = (BUNDLE / 'Contents/Resources/Bats.icns').read_bytes()
    if icon[:4] != b'icns' or int.from_bytes(icon[4:8], 'big') != len(icon):
        raise ValueError('Invalid ICNS container')
    (BUNDLE / 'Contents/Resources/en.lproj/InfoPlist.strings').read_text(encoding='utf-16')
    return version, parse_layout(LAYOUT.read_text())


def preview_files(data):
    template = (ROOT / 'scripts/preview.html').read_text()
    payload = json.dumps({'maps': data, 'rows': ROWS, 'layers': LAYERS}, ensure_ascii=True).replace('<', '\\u003c')
    preview = template.replace('/*LAYOUT_DATA*/', payload)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="390" viewBox="0 0 1000 390" role="img" aria-labelledby="title desc">',
           '<title id="title">Bats keyboard — base and Shift layers</title><desc id="desc">ANSI reference. Large letters are base outputs; green letters are Shift outputs. Small grey labels are US physical keys.</desc>',
           '<rect width="1000" height="390" rx="20" fill="#112725"/>',
           '<g font-family="Arial, sans-serif"><text x="30" y="40" font-size="22" fill="#f7f3e9">Bats / Tsova-Tush</text><text x="30" y="68" font-size="14" fill="#b4c8bf">Base · Shift in green · US key labels in grey</text>']
    for row_index, row in enumerate(ROWS):
        for col, (code, label) in enumerate(row):
            x, y = 30 + col * 72 + row_index * 12, 90 + row_index * 70
            svg.append(f'<rect x="{x}" y="{y}" width="65" height="62" rx="7" fill="#203c38"/>')
            for value, dx, dy, size, color in [(label, 8, 16, 11, '#b4c8bf'), (data['16c']['0'].get(str(code), ''), 10, 47, 25, '#f7f3e9'), (data['16c']['1'].get(str(code), ''), 43, 27, 18, '#a8deb4')]:
                svg.append(f'<text x="{x+dx}" y="{y+dy}" font-size="{size}" fill="{color}">{html.escape(value)}</text>')
    svg.append('</g></svg>')
    return {'docs/index.html': preview, 'docs/layout.svg': '\n'.join(svg) + '\n'}


def package(version, destination):
    destination.mkdir(parents=True, exist_ok=True)
    name = f'Bats-{version}'
    archive = destination / f'{name}.zip'
    files = sorted(path for path in BUNDLE.rglob('*') if path.is_file())
    files += [ROOT / file for file in ('README.md', 'CHANGELOG.md', 'docs/index.html', 'docs/layout.svg')]
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for file in files:
            entry = zipfile.ZipInfo(f'{name}/{file.relative_to(ROOT).as_posix()}', date_time=(2024, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(entry, file.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n')
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify generated previews without modifying files')
    parser.add_argument('--package', action='store_true', help='Build a versioned ZIP and SHA-256 checksum in dist/')
    args = parser.parse_args()
    version, data = validate_bundle()
    for name, contents in preview_files(data).items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text() != contents:
                raise SystemExit(f'{name} is stale; run python3 scripts/build.py')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
    if args.package:
        print(package(version, ROOT / 'dist'))
    print(f'Validated Bats {version}: 2 hardware map sets, 9 layers each; preview matches source.')


if __name__ == '__main__':
    main()
