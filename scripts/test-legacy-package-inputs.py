#!/usr/bin/env python3
"""Offline staging boundaries, with tiny non-executable package byte controls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('legacy_inputs',Path(__file__).with_name('prepare-legacy-package-inputs.py'))
legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)

class LegacyInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
        (self.out/'evidence').mkdir();(self.out/'evidence/prepared.json').write_text(json.dumps({'target':'linux-x64'}))
        self.raw=b'nonexecutable package fixture';self.sha=hashlib.sha256(self.raw).hexdigest()
        for patch in [mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}),mock.patch.object(legacy.preparation,'require_output',side_effect=lambda value:value),mock.patch.object(legacy,'PINS',{'linux-x64':{'directory':'legacy-debs','assets':[('fixture.deb','repo/fixture.deb',len(self.raw),self.sha)]}})]:
            patch.start();self.addCleanup(patch.stop)
    def test_exact_bytes_and_fixed_origin_produce_sanitized_receipt(self):
        def download(url,file,digest):
            self.assertEqual(url,'https://vmpower.blob.core.windows.net/repo/fixture.deb');self.assertEqual(digest,self.sha);file.write_bytes(self.raw)
        result=legacy.stage(self.out,'linux-x64',download)
        self.assertFalse(result['executed']);self.assertEqual(result,json.loads((self.out/'evidence/legacy-inputs.json').read_text()))
    def test_wrong_size_or_hash_never_produces_success_receipt(self):
        for raw in [b'x',b'x'*len(self.raw)]:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError,'bytes differ'):legacy.stage(self.out,'linux-x64',lambda url,file,digest:file.write_bytes(raw))
                self.assertFalse((self.out/'evidence/legacy-inputs.json').exists())
                (self.out/'legacy-debs/fixture.deb').unlink();(self.out/'legacy-debs').rmdir()
    def test_wrong_target_refuses_before_download(self):
        (self.out/'evidence/prepared.json').write_text(json.dumps({'target':'win-x64'}))
        with self.assertRaisesRegex(ValueError,'target mismatch'):legacy.stage(self.out,'linux-x64',mock.Mock(side_effect=AssertionError('No download')))
        self.assertFalse((self.out/'legacy-debs').exists())
    def test_existing_directory_preserved(self):
        destination=self.out/'legacy-debs';destination.mkdir();(destination/'keep').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):legacy.stage(self.out,'linux-x64',mock.Mock(side_effect=AssertionError('No download')))
        self.assertEqual((destination/'keep').read_bytes(),b'keep')
    def test_active_or_stale_fixture_ownership_blocks_network(self):
        for name in ('owned.json','owned-windows.json'):
            journal=self.out/name;journal.write_text('{}')
            with self.assertRaisesRegex(ValueError,'before fixture ownership'):legacy.stage(self.out,'linux-x64',mock.Mock(side_effect=AssertionError('No download')))
            self.assertFalse((self.out/'legacy-debs').exists());journal.unlink()
    def test_non_host_refuses_before_path_or_network(self):
        with mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}),mock.patch.object(legacy.preparation,'require_output',side_effect=AssertionError('No path read')):
            with self.assertRaisesRegex(ValueError,'hosted'):legacy.stage(self.out,'linux-x64')

if __name__=='__main__':unittest.main()
