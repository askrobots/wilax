"""Static checks for mailwon. No Docker, no network, no running stack.

Run from the repo root:  python3 -I -m unittest discover -s tests -v

These catch the mistakes that otherwise only show up when the stack fails to boot on a droplet:
a broken compose/override file, an override naming a service that does not exist, a script that
does not parse. Live behaviour (mail actually flowing) is covered by helper-scripts/dev_tests/smoke.py.
"""
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:  # PyYAML is only needed for the compose tests
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, 'docker-compose.yml')
SLIM = os.path.join(ROOT, 'docker-compose.slim.override.yml')
SMOKE = os.path.join(ROOT, 'helper-scripts', 'dev_tests', 'smoke.py')
MEM_RE = re.compile(r'^\d+[kKmMgG]?$')


def tracked(*suffixes, exclude=('/vendor/', '/node_modules/')):
    """Files tracked by git (so build output and local config are never checked)."""
    out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [
        os.path.join(ROOT, f) for f in out.splitlines()
        if f.endswith(suffixes) and not any(x in '/' + f for x in exclude)
    ]


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates compose merge tags (!override, !reset)."""


for _tag in ('!override', '!reset'):
    _Loader.add_multi_constructor(_tag, lambda l, suf, node: (
        l.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
        else l.construct_mapping(node) if isinstance(node, yaml.MappingNode)
        else l.construct_scalar(node)))


def load(path):
    with open(path) as fh:
        return yaml.load(fh, Loader=_Loader)


def mem_bytes(value):
    value = str(value)
    units = {'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3}
    return int(value[:-1]) * units[value[-1].lower()] if value[-1].isalpha() else int(value)


def bash_major():
    out = subprocess.run(['bash', '-c', 'echo ${BASH_VERSINFO[0]}'], capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else 0


def depends(service):
    deps = service.get('depends_on') or []
    return list(deps) if isinstance(deps, (list, dict)) else [deps]


@unittest.skipIf(yaml is None, 'PyYAML not installed')
class ComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = load(BASE)
        cls.slim = load(SLIM)
        cls.services = cls.base['services']

    def test_base_has_the_core_mail_services(self):
        for name in ('postfix-mailcow', 'dovecot-mailcow', 'rspamd-mailcow', 'mysql-mailcow',
                     'redis-mailcow', 'nginx-mailcow', 'php-fpm-mailcow'):
            self.assertIn(name, self.services)

    def test_every_service_has_an_image(self):
        for name, svc in self.services.items():
            self.assertTrue(svc.get('image') or svc.get('build'), f'{name} has no image or build')

    def test_depends_on_targets_exist(self):
        for name, svc in self.services.items():
            for dep in depends(svc):
                self.assertIn(dep, self.services, f'{name} depends on unknown service {dep}')

    def test_slim_override_only_names_existing_services(self):
        for name in self.slim['services']:
            self.assertIn(name, self.services, f'slim override references unknown service {name}')

    def test_slim_override_adds_no_new_services_or_images(self):
        for name, svc in self.slim['services'].items():
            self.assertNotIn('image', svc, f'{name}: override must not change the image')

    def test_slim_mem_limits_are_valid_and_not_absurdly_small(self):
        for name, svc in self.slim['services'].items():
            limit = svc.get('mem_limit')
            if limit is None:
                continue
            self.assertRegex(str(limit), MEM_RE, f'{name}: bad mem_limit {limit!r}')
            self.assertGreaterEqual(mem_bytes(limit), 16 * 1024 ** 2, f'{name}: mem_limit under 16m would be OOM-killed at start')

    def test_slim_commands_are_non_empty_strings_or_lists(self):
        for name, svc in self.slim['services'].items():
            if 'command' in svc:
                cmd = svc['command']
                self.assertTrue(isinstance(cmd, (str, list)) and len(cmd) > 0, f'{name}: bad command')
                if isinstance(cmd, list):
                    self.assertTrue(all(isinstance(c, str) for c in cmd), f'{name}: command items must be strings')

    def test_slim_mysql_keeps_a_buffer_pool_flag(self):
        cmd = ' '.join(self.slim['services']['mysql-mailcow']['command'])
        self.assertIn('--innodb-buffer-pool-size', cmd)


class ScriptTests(unittest.TestCase):
    @unittest.skipIf(bash_major() < 4, 'mailcow scripts use bash 4+ syntax (;;& and extglob); macOS /bin/bash is 3.2')
    def test_shell_scripts_parse(self):
        bad = []
        for path in tracked('.sh'):
            r = subprocess.run(['bash', '-n', path], capture_output=True, text=True)
            if r.returncode:
                bad.append(f'{os.path.relpath(path, ROOT)}: {r.stderr.strip().splitlines()[:1]}')
        self.assertEqual(bad, [], 'shell syntax errors')

    def test_python_files_compile(self):
        bad = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, path in enumerate(tracked('.py')):
                try:
                    py_compile.compile(path, cfile=os.path.join(tmp, f'{i}.pyc'), doraise=True)
                except py_compile.PyCompileError as exc:
                    bad.append(f'{os.path.relpath(path, ROOT)}: {exc.msg.strip().splitlines()[-1]}')
        self.assertEqual(bad, [], 'python syntax errors')

    @unittest.skipUnless(shutil.which('php'), 'php not installed')
    def test_first_party_php_lints(self):
        bad = []
        for path in tracked('.php', exclude=('/vendor/', '/node_modules/', 'data/web/inc/lib/')):
            r = subprocess.run(['php', '-l', path], capture_output=True, text=True)
            if r.returncode:
                bad.append(f'{os.path.relpath(path, ROOT)}: {r.stdout.strip().splitlines()[:1]}')
        self.assertEqual(bad, [], 'php syntax errors')


class SmokeScriptTests(unittest.TestCase):
    """smoke.py needs a live stack; here we only check it fails clearly when misconfigured."""

    def test_refuses_to_run_without_an_api_key(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('MAILCOW_', 'SMOKE_'))}
        r = subprocess.run([sys.executable, '-I', SMOKE], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('MAILCOW_API_KEY', r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
