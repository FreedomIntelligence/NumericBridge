"""Preflight tests with stub hardware inventory and no devices or model runtime."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.native_vllm import authorize, clean_environment, prepared, resolve_devices, verify_binding

GPU_A = 'a1111111-1111-1111-1111-111111111111'
GPU_B = 'b2222222-2222-2222-2222-222222222222'


class VllmPreparationTests(unittest.TestCase):
    def test_npu_uses_logical_ids_and_checks_only_the_authorized_chips(self):
        calls = []
        def probe(argv):
            calls.append(argv)
            if argv[-1] == '-m':
                return 'NPU ID  Chip ID  Chip Logic ID  Chip Phy ID  Chip Name\n4 0 4 8 910C\n4 1 5 9 910C\n5 0 6 10 910C\n'
            return 'No process in device.'
        visible, selected = resolve_devices('910c', [4, 6], 'logic-id', query=probe)
        self.assertEqual(visible, {'ASCEND_RT_VISIBLE_DEVICES': '4,6'})
        self.assertEqual([v['physical'] for v in selected], [8, 10])
        self.assertEqual([c[-4:] for c in calls[1:]], [['-i', '4', '-c', '0'], ['-i', '5', '-c', '0']])
        with self.assertRaisesRegex(ValueError, 'inventory format'):
            resolve_devices('910c', [4], 'logic-id', query=lambda _argv: '4 0 4 8 910C')

    def test_b200_namespace_mismatch_cannot_launch(self):
        def probe(argv, **_kw):
            if argv[0] == 'nvidia-smi':
                if 'pid' in argv[3]:
                    return ''
                return f'{argv[2]}, GPU-{GPU_A if argv[2] == "2" else GPU_B}, NVIDIA B200'
            return 'NB_BINDING=' + json.dumps({'uuids': [GPU_B, GPU_A]})
        env, devices = resolve_devices('b200', [2, 3], 'index', query=probe)
        self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '2,3')
        with self.assertRaisesRegex(ValueError, 'do not launch'):
            verify_binding({'hardware': 'b200'}, devices, env, query=probe)

    def test_busy_unknown_and_ungranted_fail_closed(self):
        config = dict(schema_version=1, hardware='b200', approved_devices=[2], devices=[3],
                      model_candidates=[], model_files={}, work_root='/allowed')
        with self.assertRaisesRegex(ValueError, 'explicitly granted'):
            authorize(config)
        def busy(argv):
            return '3107' if 'pid' in argv[3] else f'2, GPU-{GPU_A}, NVIDIA B200'
        with self.assertRaisesRegex(ValueError, 'busy'):
            resolve_devices('b200', [2], 'index', query=busy)

    def test_pinned_candidate_fallback_and_private_short_environment(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            names = ('config.json', 'weights.safetensors')
            bad, good = root / 'bad', root / 'good'
            bad.mkdir(); good.mkdir()
            for name in names:
                (bad / name).write_bytes(b'wrong')
                (good / name).write_bytes(b'pinned')
            profile = dict(schema_version=1, hardware='b200', approved_devices=[2], devices=[2],
                           vllm_version='0.18.0', model_candidates=[str(bad), str(good)], work_root=str(root),
                           model_files={name: hashlib.sha256(b'pinned').hexdigest() for name in names},
                           extra_imports=['pybase64'])
            def query(argv, **kw):
                if argv[0] == 'nvidia-smi':
                    return '' if 'pid' in argv[3] else f'2, GPU-{GPU_A}, NVIDIA B200'
                self.assertNotIn('LOCAL_RANK', kw['env'])
                self.assertIn('pybase64', argv)
                self.assertIn('0.18.0', argv[3])
                return 'NB_BINDING=' + json.dumps({'uuids': [GPU_A]})
            with patch('tools.native_vllm._fs_type', return_value='local'), patch.dict('os.environ', {'LOCAL_RANK': '1,2'}):
                with prepared(profile, query=query) as (work, model, env):
                    self.assertEqual((work, model), (root, good))
                    scratch = Path(env['VLLM_RPC_BASE_PATH'])
                    self.assertTrue(scratch.is_dir())
                    self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(env['HF_HUB_OFFLINE'], '1')
                self.assertFalse(scratch.exists())
                (good / 'weights.safetensors').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'fixed file manifest'):
                    with prepared(profile, query=query):
                        self.fail('altered weights cannot prepare')

    def test_no_inherited_visibility_from_other_backend(self):
        env = clean_environment({'ASCEND_RT_VISIBLE_DEVICES': '0', 'WORLD_SIZE': '8', 'PATH': '/usr/bin'},
                                {'CUDA_VISIBLE_DEVICES': '2'}, Path('/tmp/nb'))
        self.assertNotIn('ASCEND_RT_VISIBLE_DEVICES', env)
        self.assertNotIn('WORLD_SIZE', env)
        self.assertEqual(env['PATH'], '/usr/bin')
        self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '2')


if __name__ == '__main__':
    unittest.main()
