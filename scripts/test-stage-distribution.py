#!/usr/bin/env python3
"""Offline adverse-input tests; fixtures are never executed or installed."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('stage',Path(__file__).with_name('stage-distribution.py'))
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo';self.modules=self.repo/'node_modules'
        for p in ['distribution','build/lib','node_modules/dep','node_modules/dev-only']:
            (self.repo/p).mkdir(parents=True)
        for name in ['index.js','lib/setup-args.js','lib/send-login-info.js','lib/run-last.js','lib/run-query.js']:
            (self.repo/'build'/name).write_text('// nonexecuted fixture\n')
        (self.repo/'package.json').write_text(json.dumps({'name':'vmpower-agent','version':'0.0.2'}))
        self.lock={'lockfileVersion':1,'dependencies':{
            'dep':{'version':'1.2.3','resolved':'https://registry.npmjs.org/dep/-/dep-1.2.3.tgz','integrity':'sha512-fixture'},
            'dev-only':{'version':'9.0.0','dev':True}}}
        (self.repo/'package-lock.json').write_text(json.dumps(self.lock))
        for name,version in [('dep','1.2.3'),('dev-only','9.0.0')]:
            (self.modules/name/'package.json').write_text(json.dumps({'name':name,'version':version}))
        (self.modules/'dep/index.js').write_text('// public dependency fixture\n')
        binary=bytearray(256);binary[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',binary,18,62)
        self.archive=self.root/'node-v24.22.1-linux-x64.tar.xz'
        with tarfile.open(self.archive,'w:xz') as t:
            for name,data in [('bin/node',bytes(binary)),('LICENSE',b'fixture license')]:
                member=tarfile.TarInfo('node-v24.22.1-linux-x64/'+name);member.size=len(data);t.addfile(member,io.BytesIO(data))
        self.pins={'nodeVersion':'24.22.1','targets':{'linux-x64':{'archive':self.archive.name,'sha256':stage.digest(self.archive.read_bytes()),'binary':'bin/node','minimum':'fixture only'}}}
        self.save_pins()

    def save_pins(self):
        (self.repo/'distribution/runtime-pins.json').write_text(json.dumps(self.pins))

    def bundle(self):
        output=self.root/'bundle';stage.stage(self.repo,self.modules,self.archive,'linux-x64',output);return output

    def test_preserves_binary_bytes_and_excludes_development_and_test_code(self):
        output=self.bundle();manifest=stage.verify_bundle(output)
        self.assertEqual(len(manifest['productionPackages']),1)
        self.assertFalse((output/'app/node_modules/dev-only').exists())
        self.assertFalse((output/'app/test').exists())
        self.assertEqual((output/'runtime/node').read_bytes()[:6],b'\x7fELF\x02\x01')
        self.assertEqual(manifest['applicationVersion'],'0.0.2')

    def test_modified_runtime_archive_rejected_before_output(self):
        with self.archive.open('ab') as f:f.write(b'changed')
        with self.assertRaisesRegex(ValueError,'pinned'):self.bundle()
        self.assertFalse((self.root/'bundle').exists())

    def test_wrong_installed_package_version_rejected(self):
        (self.modules/'dep/package.json').write_text('{"name":"dep","version":"0.0.0"}')
        with self.assertRaisesRegex(ValueError,'lock'):self.bundle()
        self.assertFalse((self.root/'bundle').exists())

    def test_symlink_rejected_without_copying_outside_file(self):
        secret=self.root/'outside';secret.write_text('fixture forbidden')
        (self.modules/'dep/linked').symlink_to(secret)
        with self.assertRaisesRegex(ValueError,'symlink'):self.bundle()

    def test_existing_output_preserved(self):
        output=self.bundle();before=(output/'bundle-manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'exists'):self.bundle()
        self.assertEqual((output/'bundle-manifest.json').read_bytes(),before)

    def test_changed_staged_file_fails_verification(self):
        output=self.bundle();(output/'app/index.js').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'differ'):stage.verify_bundle(output)

    def test_extra_file_fails_verification(self):
        output=self.bundle();(output/'unexpected').write_text('extra')
        with self.assertRaisesRegex(ValueError,'differ'):stage.verify_bundle(output)

    def test_missing_file_fails_verification(self):
        output=self.bundle();(output/'runtime/node').unlink()
        with self.assertRaisesRegex(ValueError,'differ'):stage.verify_bundle(output)

    def test_private_or_nonregistry_source_fails_closed(self):
        self.lock['dependencies']['dep']['resolved']='https://npm.pkg.github.com/dep'
        with self.assertRaisesRegex(ValueError,'origin'):stage.production_nodes(self.lock)

    def test_traversal_dependency_fails_closed(self):
        self.lock['dependencies']['../outside']=self.lock['dependencies']['dep']
        with self.assertRaisesRegex(ValueError,'Unsafe'):stage.production_nodes(self.lock)

    def test_unknown_lock_schema_fails_closed(self):
        self.lock['lockfileVersion']=4
        with self.assertRaisesRegex(ValueError,'format'):stage.production_nodes(self.lock)

    def test_windows_runtime_uses_original_pe_bytes(self):
        binary=bytearray(256);binary[:2]=b'MZ';struct.pack_into('<I',binary,60,128)
        binary[128:132]=b'PE\0\0';struct.pack_into('<H',binary,132,0x8664)
        archive=self.root/'node-v24.22.1-win-x64.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('node-v24.22.1-win-x64/node.exe',binary)
            z.writestr('node-v24.22.1-win-x64/LICENSE','fixture license')
            z.writestr('../../outside','must not be extracted')
        self.pins['targets']['win-x64']={'archive':archive.name,'sha256':stage.digest(archive.read_bytes()),'binary':'node.exe','minimum':'fixture only'}
        self.save_pins();output=self.root/'windows'
        stage.stage(self.repo,self.modules,archive,'win-x64',output)
        self.assertEqual((output/'runtime/node.exe').read_bytes(),binary)
        self.assertFalse((self.root/'outside').exists())
        self.assertIn('%~dp0', (output/'vmpower.cmd').read_text())


if __name__=='__main__':unittest.main()
