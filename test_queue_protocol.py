"""Protocol checks with a fake release transport; no network or model training."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import experiment_queue as q


class Store:
    def __init__(self):
        self.files={}; self.states=[]; self.assets={}; self.release=False; self.lose_response=False; self.corrupt=False
    def get_file(self,path):
        return ({'sha':'old'},self.files[path]) if path in self.files else None
    def put_file(self,path,content,*args):
        self.files[path]=content
    def transport(self,*args,**kwargs):
        action=args[1]
        if action=='create':
            if self.release: raise q.GhError('already exists',422)
            self.release=True
        elif action=='upload':
            p=Path(args[3]); key=p.name
            if key in self.assets: raise q.GhError('existing asset',422)
            self.assets[key]=p.read_bytes()
            if self.lose_response:
                self.lose_response=False
                raise q.GhError('connection lost after accepted upload')
        elif action=='download':
            name=args[args.index('--pattern')+1]; dest=Path(args[args.index('--dir')+1]);dest.mkdir(parents=True,exist_ok=True)
            (dest/name).write_bytes(b'corrupt' if self.corrupt else self.assets[name])
        else: raise AssertionError(args)
        return b''


class ProtocolTests(unittest.TestCase):
    def call_complete(self,root,store,required=None,exit_code=0):
        args=SimpleNamespace(repo='owner/repo',task_id='example',worker='vm',claim_token='token',results=str(root),exit_code=exit_code,started_at='2026-11-14T00:00:00Z')
        task={'command':['{python}','train.py'],'package_sha256':'abc','required_patterns':required or []}
        def update(g,tid,state,msg): store.states.append(state)
        with patch.object(q,'GitHub',return_value=store), patch.object(q,'task_manifest',return_value=task), patch.object(q,'claim_record',return_value={'worker_id':'vm','claim_token':'token'}), patch.object(q,'update_state',side_effect=update), patch.object(q,'refresh_indexes'), patch.object(q,'gh',side_effect=store.transport),contextlib.redirect_stdout(io.StringIO()):
            q.upload_results(args)

    def test_upload_response_lost_then_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'metrics.json').write_text('{"IC":0.03}')
            store=Store();store.lose_response=True
            self.call_complete(root,store,['metrics.json'])
            digest=json.loads(store.files['results/example.json'])['archive_sha256']
            self.call_complete(root,store,['metrics.json'])
            self.assertEqual(digest,json.loads(store.files['results/example.json'])['archive_sha256'])
            self.assertEqual([s['status'] for s in store.states],['completed','completed'])

    def test_missing_required_or_failed_exit_never_completed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'run.log').write_text('failed to load inputs')
            for required,code in [(['best.pt'],0),([],2)]:
                store=Store();self.call_complete(root,store,required,code)
                self.assertEqual(store.states[-1]['status'],'failed')
                self.assertIn('results/example.json',store.files)

    def test_corrupt_remote_does_not_write_result_or_completion(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'run.log').write_text('done')
            store=Store();store.corrupt=True
            with self.assertRaises(RuntimeError): self.call_complete(root,store)
            self.assertFalse(store.states);self.assertNotIn('results/example.json',store.files)

    def test_chunked_upload_all_bytes_verified(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'metrics.json').write_text('{"IC":0.03}')
            store=Store()
            with patch.object(q,'CHUNK',100): self.call_complete(root,store)
            self.assertGreater(len(store.assets),1)
            self.assertEqual(store.states[-1]['status'],'completed')

    def test_not_found_is_distinct_from_network_and_permissions(self):
        g=q.GitHub('owner/repo')
        with patch.object(g,'api',side_effect=q.GhError('missing',404)):
            self.assertIsNone(g.get_file('tasks/a.json'))
        for code in [None,401,403,500]:
            with patch.object(g,'api',side_effect=q.GhError('unavailable',code)),self.assertRaises(q.GhError): g.get_file('tasks/a.json')

    def test_empty_results_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store()
            with self.assertRaises(RuntimeError): self.call_complete(Path(td),store)
            self.assertFalse(store.assets)


if __name__=='__main__': unittest.main()
