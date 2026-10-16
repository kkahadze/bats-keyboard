import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = build.LAYOUT.read_text()
        cls.version, cls.maps = build.validate_bundle()

    def test_original_outputs_and_control_references(self):
        base = self.maps['16c']['0']
        self.assertEqual([base[str(code)] for code in (0, 1, 2)], ['ა', 'ს', 'დ'])
        self.assertEqual(base['51'], '\b')
        self.assertEqual(base['36'], '\r')
        self.assertEqual(base['48'], '\t')
        self.assertEqual(base['53'], '\x1b')

    def test_hardware_inheritance_preserves_and_overrides(self):
        base, jis = self.maps['16c']['0'], self.maps['a88']['0']
        self.assertEqual(jis['0'], base['0'])
        self.assertEqual(jis['24'], '^')
        self.assertNotEqual(jis['24'], base['24'])
        self.assertEqual(jis['93'], '¥')

    def test_rejects_missing_base_map(self):
        with self.assertRaisesRegex(ValueError, 'Missing map'):
            build.parse_layout(self.source.replace('baseIndex="0"', 'baseIndex="99"', 1))

    def test_rejects_inheritance_cycles(self):
        with self.assertRaisesRegex(ValueError, 'Inheritance cycle'):
            build.parse_layout(self.source.replace('baseMapSet="16c"', 'baseMapSet="a88"', 1))

    def test_rejects_duplicate_key_codes(self):
        with self.assertRaisesRegex(ValueError, 'duplicate key code'):
            build.parse_layout(self.source.replace('<key code="0" output="ა"/>', '<key code="0" output="ა"/><key code="0" output="ბ"/>', 1))

    def test_preview_matches_source(self):
        for name, contents in build.preview_files(self.maps).items():
            self.assertEqual((build.ROOT / name).read_text(), contents, name)

    def test_release_is_complete_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            archive = build.package(self.version, dest)
            first = archive.read_bytes()
            self.assertEqual(build.package(self.version, dest).read_bytes(), first)
            self.assertEqual((dest / 'SHA256SUMS.txt').read_text(), f'{hashlib.sha256(first).hexdigest()}  {archive.name}\n')
            with zipfile.ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                prefix = f'Bats-{self.version}/'
                self.assertEqual(package.read(prefix + 'Bats.bundle/Contents/Resources/Bats.keylayout'), build.LAYOUT.read_bytes())
                for name in ('README.md', 'CHANGELOG.md', 'docs/index.html', 'docs/layout.svg', 'Bats.bundle/Contents/Info.plist', 'Bats.bundle/Contents/Resources/Bats.icns'):
                    self.assertIn(prefix + name, package.namelist())
                self.assertFalse(any('__pycache__' in name or '.DS_Store' in name for name in package.namelist()))


if __name__ == '__main__':
    unittest.main()
