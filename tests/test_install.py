import json
import os
from pathlib import Path
import plistlib
import sqlite3
from subprocess import CompletedProcess
import sys
import tempfile
import time
import unittest
from install_bridge import FILES, locations, preflight, install, job_status, stop_job


@unittest.skipUnless(os.name == "posix", "launchd and Unix file permissions")
class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.paths=locations(self.base/'new-home')
        self.source=self.base/'source';self.source.mkdir()
        for name in FILES:(self.source/name).write_text('# new collector\n')
        self.paths['tool'].parent.mkdir(parents=True);self.paths['tool'].write_text('tool')
        self.codex=self.base/'codex';self.codex.mkdir()
        with sqlite3.connect(self.codex/'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT, updated_at INTEGER)')
        self.calls=[];self.version='0.1.230';self.fail_bootstrap=False;self.loaded=False

    def runner(self,args):
        self.calls.append(args)
        output='';code=0
        if args[-1]=='tool-info':output=json.dumps({'version':self.version})
        elif args[:2]==['launchctl','bootstrap']:
            if self.fail_bootstrap:self.fail_bootstrap=False;code=5
            else:
                self.loaded=True
                self.paths['health'].write_text(json.dumps({'pid':1234,'at':int(time.time()*1000),'phase':'starting'}))
        elif args[:2]==['launchctl','bootout']:self.loaded=False
        elif args[:2]==['launchctl','print']:
            code=0 if self.loaded else 3
            output='state = running\npid = 1234\n' if self.loaded else ''
        return CompletedProcess(args,code,output,'')

    def check(self):
        return preflight(self.paths,self.source,sys.executable,self.codex,self.runner,'Darwin')

    def test_clean_machine_install_uses_selected_python_and_verifies_start(self):
        self.check()
        result=install(self.paths,self.source,sys.executable,self.codex,self.runner,0)
        self.assertTrue(result['installed']);self.assertTrue(result['running'])
        self.assertFalse(result['dataReady']) # starting is not successful data delivery
        config=plistlib.loads(self.paths['plist'].read_bytes())
        self.assertEqual(config['ProgramArguments'][0],sys.executable)
        self.assertIn(str(self.codex),config['ProgramArguments'])
        self.assertEqual(self.paths['plist'].stat().st_mode&0o777,0o600)
        self.assertTrue(all((self.paths['root']/name).exists() for name in FILES))

    def test_wrong_version_does_not_stop_or_replace_existing_job(self):
        self.version='0.1.231'
        with self.assertRaisesRegex(RuntimeError,'0.1.230'):self.check()
        self.assertFalse(any(c[0]=='launchctl' for c in self.calls))
        self.assertFalse(self.paths['plist'].exists())

    def test_missing_source_is_detected_before_service_changes(self):
        (self.source/'collect.py').unlink()
        with self.assertRaisesRegex(RuntimeError,'完整解压'):self.check()
        self.assertFalse(self.calls)

    def test_unreadable_index_blocks_installation(self):
        (self.codex/'state_5.sqlite').write_bytes(b'not a sqlite database')
        with self.assertRaisesRegex(RuntimeError,'不可读'):self.check()
        self.assertFalse(any(c[0]=='launchctl' for c in self.calls))

    def test_new_install_start_failure_is_rolled_back(self):
        self.check();self.fail_bootstrap=True
        with self.assertRaisesRegex(RuntimeError,'撤回'):
            install(self.paths,self.source,sys.executable,self.codex,self.runner,0)
        self.assertFalse(self.paths['plist'].exists())
        self.assertFalse(any((self.paths['root']/name).exists() for name in FILES))

    def test_update_failure_restores_old_files_and_service(self):
        self.check();install(self.paths,self.source,sys.executable,self.codex,self.runner,0)
        old_plist=self.paths['plist'].read_bytes()
        for name in FILES:(self.source/name).write_text('# changed\n')
        self.fail_bootstrap=True
        with self.assertRaisesRegex(RuntimeError,'恢复原配置'):
            install(self.paths,self.source,sys.executable,self.codex,self.runner,0)
        self.assertEqual(self.paths['plist'].read_bytes(),old_plist)
        self.assertEqual((self.paths['root']/'bridge.py').read_text(),'# new collector\n')
        self.assertTrue(self.loaded)

    def test_an_old_health_file_cannot_claim_current_process_is_ready(self):
        self.check();install(self.paths,self.source,sys.executable,self.codex,self.runner,0)
        self.paths['health'].write_text(json.dumps({'pid':9876,'at':int(time.time()*1000),'phase':'ready'}))
        result=job_status(self.paths,'gui/test',self.runner)
        self.assertFalse(result['dataReady']);self.assertEqual(result['collectorPhase'],'unknown')

    def test_cannot_remove_configuration_while_stop_failed(self):
        def failed(args):return CompletedProcess(args,5 if args[1]=='bootout' else 0,'','')
        with self.assertRaisesRegex(RuntimeError,'保留原文件'):stop_job(self.paths,'gui/test',failed)

    def test_non_macos_is_explicitly_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'install-windows[.]cmd'):
            preflight(self.paths,self.source,sys.executable,self.codex,self.runner,'Windows')


if __name__=='__main__':unittest.main()
