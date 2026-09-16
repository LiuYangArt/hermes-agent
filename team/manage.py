#!/usr/bin/env python3
"""Install versioned team assets and verify the running Docker deployment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

TEAM = Path(__file__).resolve().parent
REPO = TEAM.parent
STATE = Path(os.environ.get('HERMES_TEAM_STATE_DIR', str(Path.home()/'.local/share/hermes-team')))
CONTAINER = 'hermes-team'


def assets():
    for src_dir, dest_dir in [('plugins', 'data/plugins'), ('skills', 'data/skills'), ('acp', 'data/task-bridge'), ('task-bridge', 'task-bridge')]:
        for source in sorted((TEAM/src_dir).rglob('*')):
            if source.is_file() and not any(p in {'__pycache__','node_modules','runtime','.local'} for p in source.relative_to(TEAM).parts):
                yield source, STATE/dest_dir/source.relative_to(TEAM/src_dir)
    yield TEAM/'cleanup-workspace.sh', STATE/'cleanup-workspace.sh'


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, **kwargs)


def install():
    if not (STATE/'data/config.yaml').is_file():
        raise SystemExit('Existing deployment config is required; this command does not create credentials or accounts.')
    defaults = TEAM/'.local/team-settings.md'
    if not defaults.is_file() and not (STATE/'data/team-settings.md').is_file():
        raise SystemExit('Create team/.local/team-settings.md from team-settings.example.md before installation.')
    count = 0
    lingo_source = TEAM/'skills/lark-lingo'
    lingo_target = STATE/'data/skills/lark-lingo'
    # The managed read-only skill must not retain obsolete write/import guides.
    for destination in lingo_target.rglob('*'):
        if destination.is_file() and not (lingo_source/destination.relative_to(lingo_target)).is_file():
            destination.unlink()
    for source, destination in assets():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or source.read_bytes()!=destination.read_bytes():
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(source.read_bytes())
            temp.chmod(source.stat().st_mode & 0o777)
            temp.replace(destination)
            count += 1
    if defaults.is_file():
        target = STATE/'data/team-settings.md'
        shutil.copyfile(defaults, target)
        target.chmod(0o600)
    print(json.dumps({'updated_source_files':count,'state_directory':str(STATE)}))


def verify():
    run(['docker', 'exec', '-w', '/workspace', CONTAINER, '/opt/hermes/.venv/bin/python', '-c',
         'import team.governance, team.sandbox, team.lingo'], capture_output=True)
    mismatches = [str(source.relative_to(REPO)) for source,destination in assets()
                  if not destination.is_file() or source.read_bytes()!=destination.read_bytes()]
    for core in ('plugins/platforms/feishu/adapter.py', 'plugins/platforms/feishu/thread_router.py',
                 'plugins/platforms/feishu/thread_state.py', 'gateway/run.py',
                 'gateway/slash_access.py', 'agent/tool_executor.py', 'model_tools.py',
                 'tools/file_tools.py', 'tools/environments/local.py', 'tools/terminal_tool.py',
                 'tools/code_execution_tool.py', 'tools/memory_tool.py',
                 'tools/skill_manager_tool.py', 'tools/write_approval.py',
                 'team/governance.py', 'team/sandbox.py', 'team/lingo.py'):
        code = 'import hashlib;from pathlib import Path;print(hashlib.sha256(Path("/opt/hermes/'+core+'").read_bytes()).hexdigest())'
        actual = run(['docker','exec',CONTAINER,'/opt/hermes/.venv/bin/python','-c',code],capture_output=True).stdout.strip()
        expected = hashlib.sha256((REPO/core).read_bytes()).hexdigest()
        if actual != expected:
            mismatches.append(core)
    if mismatches:
        raise SystemExit('Source/deployment differences:\n'+'\n'.join(mismatches))
    temp_home = '/tmp/hermes-source-check-'+uuid.uuid4().hex
    test_count = 0
    try:
        for test in ('test_lark_threads.py','test_helius_image_tool.py','test_thread_conversations.py','test_lark_identity.py',
                     'test_team_governance.py', 'test_team_sandbox.py', 'test_team_lingo.py', 'test_team_runtime.py'):
            try:
                result = run(['docker','exec','-i','-e','HERMES_HOME='+temp_home,CONTAINER,'/opt/hermes/.venv/bin/python'],input=(TEAM/'tests'/test).read_text(), capture_output=True)
            except subprocess.CalledProcessError as exc:
                print((exc.stdout or '') + (exc.stderr or ''))
                raise
            print(result.stdout + result.stderr)
            match = re.search(r'Ran (\d+) tests?', result.stderr)
            if not match:
                raise SystemExit('Test runner did not report a count: '+test)
            test_count += int(match.group(1))
        bridge = STATE/'task-bridge/node_modules/@luckyterry/aamp-acp-bridge/dist/acpx-client.js'
        if not bridge.is_file():
            raise SystemExit('Tasks bridge dependencies are not installed.')
        content = bridge.read_text()
        if "'--approve-all'" in content or content.count("'--deny-all', '--non-interactive-permissions', 'deny', '--no-terminal'")!=2:
            raise SystemExit('Tasks bridge approval protection is missing.')
    finally:
        run(['docker','exec',CONTAINER,'rm','-rf',temp_home])
    print(json.dumps({'source_matches_deployment':True,'assets_checked':sum(1 for _ in assets()),'targeted_tests':test_count,'tasks_permission_patch':True}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['install-assets','verify'])
    args=parser.parse_args()
    install() if args.command=='install-assets' else verify()
