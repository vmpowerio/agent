#!/usr/bin/env python3
"""Offline pack-input tests with generated, non-executable vendor byte fixtures."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_package', ROOT / 'scripts/build-windows-package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
digest = package.stage.digest
verify_spec = importlib.util.spec_from_file_location('verify_windows_package', ROOT / 'scripts/verify-windows-package.py')
verification = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verification)


class WindowsPackageInputs(unittest.TestCase):
    def test_bounded_canonical_version_protocol(self):
        for value in ['0.0.2','0.0.3','0.0.4','0.2.0','2147483647.2147483647.2147483647']:
            self.assertEqual(package.package_version(value),value)
        for value in ['',None,'0.0.1','0.0.0','01.2.3','1.02.3','1.2.03','+1.2.3',
                      '1.2.3\n','１.2.3','1.2.3.4','2147483648.0.2','1'*1000000+'.2.3']:
            with self.subTest(value=str(value)[:40]),mock.patch.object(Path,'exists',side_effect=AssertionError('filesystem reached')),mock.patch.object(package.stage,'verify_bundle',side_effect=AssertionError('bundle reached')):
                with self.assertRaises(ValueError):
                    package.prepare(Path('/unused'), 'a'*64, Path('/unused'), Path('/unused'), value, Path('/unused'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='windows-package-control-')
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.bundle = self.root / 'bundle'
        for relative in ('distribution', 'scripts', 'packages/win32/node24/tools'):
            (self.repo / relative).mkdir(parents=True)
        for relative in ('scripts/install-windows-bundle.ps1',
                         'packages/win32/node24/tools/chocolateyInstall.ps1',
                         'packages/win32/node24/tools/package-shim.ps1',
                         'packages/win32/node24/tools/chocolateyUninstall.ps1'):
            shutil.copyfile(ROOT / relative, self.repo / relative)
        (self.repo / 'package-lock.json').write_bytes(b'fixture lock, not a dependency graph')
        # Enough PE structure for the actual shared runtime validator, never run.
        node = bytearray(80)
        node[:2] = b'MZ'; struct.pack_into('<I', node, 60, 64)
        node[64:68] = b'PE\0\0'; struct.pack_into('<H', node, 68, 0x8664)
        self.node = self.root / 'node-v24.22.1-win-x64.zip'
        with zipfile.ZipFile(self.node, 'w') as z:
            z.writestr('node-v24.22.1-win-x64/node.exe', node)
            z.writestr('node-v24.22.1-win-x64/LICENSE', b'generated licence fixture')
        runtime_pins = {'nodeVersion': '24.22.1', 'targets': {'win-x64': {
            'archive': self.node.name, 'sha256': digest(self.node.read_bytes()), 'binary': 'node.exe'}}}
        (self.repo / 'distribution/runtime-pins.json').write_text(json.dumps(runtime_pins))
        self.nssm = self.root / 'nssm.zip'
        member = 'nssm-fixture/win64/nssm.exe'
        with zipfile.ZipFile(self.nssm, 'w') as z:
            z.writestr(member, b'generated wrapper fixture; not executable')
            z.writestr('nssm-fixture/README.txt', b'NSSM is public domain; fixture')
        (self.repo / 'distribution/nssm-pins.json').write_text(json.dumps({
            'version': 'fixture', 'archiveSha256': digest(self.nssm.read_bytes()),
            'x64Member': member, 'x64Sha256': digest(b'generated wrapper fixture; not executable')}))
        for name, data in {'runtime/node.exe': node, 'runtime/LICENSE': b'generated licence fixture',
                           'app/index.js': b'throw new Error("fixture must not run")'}.items():
            f = self.bundle / name; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(data)
        self.manifest = {'target': 'win-x64', 'nodeVersion': '24.22.1',
                         'runtimeArchiveSha256': digest(self.node.read_bytes()),
                         'sourceLockSha256': digest((self.repo / 'package-lock.json').read_bytes())}
        self.seal()
        self.output = self.root / 'prepared'

    def tearDown(self):
        self.temp.cleanup()

    def seal(self):
        self.manifest['files'] = {p.relative_to(self.bundle).as_posix(): digest(p.read_bytes())
                                  for p in self.bundle.rglob('*') if p.is_file() and p.name != 'bundle-manifest.json'}
        f = self.bundle / 'bundle-manifest.json'
        f.write_text(json.dumps(self.manifest)); self.expected = digest(f.read_bytes())

    def prepare(self, version='0.0.2'):
        return package.prepare(self.bundle, self.expected, self.node, self.nssm, version, self.output, self.repo)

    def test_same_id_upgrade_inputs_bind_runtime_and_retain_rollback_installer(self):
        result = self.prepare()
        ns = {'n': 'http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd'}
        xml = ET.parse(self.output / 'vmpower-agent.nuspec')
        self.assertEqual(xml.findtext('n:metadata/n:id', namespaces=ns), 'vmpower-agent')
        self.assertEqual(xml.findtext('n:metadata/n:version', namespaces=ns), '0.0.2')
        self.assertIsNone(xml.find('n:metadata/n:dependencies', ns))
        self.assertFalse(result['nativePackageManagerQualified'])
        self.assertEqual(package.stage.verify_bundle(self.output / 'tools/bundle'), self.manifest)
        self.assertEqual((self.output / 'tools/scripts/install-windows-bundle.ps1').read_bytes(),
                         (ROOT / 'scripts/install-windows-bundle.ps1').read_bytes())
        self.assertFalse((self.output / 'tools/chocolateyBeforeModify.ps1').exists())
        self.assertEqual((self.output / 'tools/package-shim.ps1').read_bytes(),
                         (ROOT / 'packages/win32/node24/tools/package-shim.ps1').read_bytes())
        self.assertFalse(any(p.name == 'vmpower-agent.amd64.exe' for p in self.output.rglob('*')))
        self.assertEqual(json.loads((self.output / 'tools/package-input.json').read_text())['bundleManifestSha256'], self.expected)

    def test_cannot_reissue_legacy_version_or_overwrite_previous_inputs(self):
        for version in ['0.0.1', '0.0.0', '0.0.2;evil', '0.0.2-beta', '01.2.3']:
            with self.assertRaises(ValueError): self.prepare(version)
        self.prepare(); sentinel = self.output / 'retained-proof'; sentinel.write_text('keep')
        with self.assertRaises(ValueError): self.prepare('0.0.3')
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_tampered_bundle_rejected_without_publishing_partial_inputs(self):
        (self.bundle / 'app/index.js').write_bytes(b'changed after qualification')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.output.exists())

    def test_resealed_wrong_runtime_does_not_inherit_vendor_provenance(self):
        (self.bundle / 'runtime/node.exe').write_bytes(b'replaced runtime')
        self.seal()
        with self.assertRaisesRegex(ValueError, 'differs from official'): self.prepare()
        self.assertFalse(self.output.exists())

    def test_changed_vendor_archives_rejected(self):
        for archive in (self.node, self.nssm):
            original = archive.read_bytes(); archive.write_bytes(original + b'tamper')
            with self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.output.exists()); archive.write_bytes(original)

    def test_foreign_lock_and_linked_bundle_rejected(self):
        (self.repo / 'package-lock.json').write_bytes(b'another dependency graph')
        with self.assertRaisesRegex(ValueError, 'lock differs'): self.prepare()
        link = self.root / 'linked'; link.symlink_to(self.bundle, target_is_directory=True)
        self.bundle = link
        with self.assertRaisesRegex(ValueError, 'unlinked'): self.prepare()

    def packed_fixture(self, mutate=None):
        members = {'vmpower-agent.nuspec': (self.output / 'vmpower-agent.nuspec').read_bytes()}
        for file in (self.output / 'tools').rglob('*'):
            if file.is_file():
                name = file.relative_to(self.output).as_posix()
                if name == 'tools/node.exe.ignore': name = 'tools/bundle/runtime/node.exe.ignore'
                members[name] = file.read_bytes()
        members['[Content_Types].xml'] = b'<Types/>'
        if mutate: mutate(members)
        packed = self.root / 'fixture.nupkg'
        with zipfile.ZipFile(packed, 'w') as archive:
            for name, raw in members.items(): archive.writestr(name, raw)
        return packed

    def test_packed_payload_and_shim_mapping_are_bound_to_inputs(self):
        self.prepare()
        packed = self.packed_fixture()
        result = verification.verify(self.output, packed)
        self.assertEqual(result['nupkgSha256'], digest(packed.read_bytes()))
        self.assertEqual(result['bundleManifestSha256'], self.expected)

    def test_resealed_bundle_cannot_claim_legacy_shim_basename(self):
        (self.bundle / 'app/VMPower-Agent.AMD64.EXE').write_bytes(b'synthetic unrelated bytes')
        self.seal()
        with self.assertRaisesRegex(ValueError, 'legacy shim basename'): self.prepare()
        self.assertFalse(self.output.exists())

    def test_consistent_pack_inputs_cannot_adopt_legacy_command_or_drop_suppression(self):
        self.prepare()
        extra = self.output / 'tools/VMPower-Agent.AMD64.EXE'
        extra.write_bytes(b'synthetic unrelated bytes')
        with self.assertRaisesRegex(ValueError, 'legacy shim basename'):
            verification.verify(self.output, self.packed_fixture())
        extra.unlink()
        for name in ('tools/node.exe.ignore', 'tools/nssm.exe.ignore'):
            marker = self.output / name
            marker.write_bytes(b'changed marker')
            with self.assertRaisesRegex(ValueError, 'suppression marker'):
                verification.verify(self.output, self.packed_fixture())
            marker.write_bytes(b'')

    def test_packed_tamper_missing_shim_and_extra_executable_rejected(self):
        self.prepare()
        mutations = [
            lambda files: files.update({'tools/bundle/runtime/node.exe': b'tampered'}),
            lambda files: files.pop('tools/bundle/runtime/node.exe.ignore'),
            lambda files: files.update({'tools/extra.exe': b'unreviewed'}),
            lambda files: files.update({'../escape': b'escape'}),
            lambda files: files.update({'TOOLS/NSSM.EXE': b'alias'}),
            lambda files: files.update({'tools/chocolateyBeforeModify.ps1': b'unreviewed hook'}),
            lambda files: files.update({'vmpower-agent.nuspec': files['vmpower-agent.nuspec'].replace(b'<version>0.0.2</version>', b'<version>0.0.1</version>')}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                with self.assertRaises(ValueError):
                    verification.verify(self.output, self.packed_fixture(mutation))


if __name__ == '__main__':
    unittest.main()
