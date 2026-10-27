#!/usr/bin/env python3
"""Local workflow evidence. Human identity and release authority belong to the forge."""
import argparse
import contextlib
import errno
import fnmatch
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT
API_VERSION = 1
API_CAPABILITIES = ['status-json', 'verify', 'mark', 'log-file']
ITEM_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,39}')
ITEM_KEYS = {'id', 'description', 'verify'}
CHECK_KEYS = {'argv', 'timeout_seconds'}
ARTIFACTS = ['01-plan/output/intent.md', '02-design/output/spec.md', '03-build/output/plan.md']


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def read_json(path):
    """Invalid JSON is refused with the file it came from."""
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        shown = path.relative_to(ROOT) if path.is_absolute() and path.is_relative_to(ROOT) else path
        raise ValueError(f'{shown} is not valid JSON: {error}') from None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run_path(slug):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', slug):
        raise ValueError('Run name must be 1–80 lowercase letters, digits, or hyphens; start with a letter or digit.')
    path = ROOT / 'runs' / slug
    if path.resolve() != path.absolute():
        raise ValueError('Run paths must not contain symlinks.')
    return path


def tink_lock_path(root=ROOT):
    repo_id = digest(str(root.resolve()).encode())[:12]
    return Path(tempfile.gettempdir()) / f'sdlc-tink-{os.getuid()}-{repo_id}.lock'


@contextlib.contextmanager
def locked(path, timeout=5.0, poll_interval=0.05):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError(f'Lock path must not be a symlink: {path}') from error
        if error.errno == errno.EISDIR:
            raise ValueError(f'Stale directory lock found at {path}. Remove it to allow file flock.') from error
        raise
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise ValueError(f'Lock path must be a regular file: {path}')
    with open(fd, 'a+b', closefd=True) as handle:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() - start >= timeout:
                    raise ValueError(f'Busy or interrupted operation: {path}. Confirm no writer remains before removing this lock.')
                time.sleep(poll_interval)
        yield


def require_run(path):
    """The one supported run shape; anything else is refused with what it lacks."""
    if not path.is_dir() or not (path / 'run.json').is_file():
        raise ValueError('Run has no run.json; create runs with sdlc.py new.')
    meta = read_json(path / 'run.json')
    if not isinstance(meta, dict):
        raise ValueError('Run run.json must be an object.')
    value = meta.get('run_id')
    try:
        identity = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        identity = None
    if identity is None or identity.version != 4 or str(identity) != value:
        raise ValueError('Run run_id must be a canonical UUIDv4 string; create runs with sdlc.py new.')
    bound = meta.get('seed_contract')
    if bound is not None:
        if not isinstance(bound, dict):
            raise ValueError('Seed binding must be an object or null.')
        for key in ('path', 'sha256', 'source'):
            if not isinstance(bound.get(key), str) or not bound[key].strip():
                raise ValueError(f'Seed binding {key} must be a nonempty string.')
        if not re.fullmatch(r'[0-9a-f]{64}', bound['sha256']):
            raise ValueError('Seed binding sha256 must be a lowercase SHA-256 hex digest.')
        if 'definitions_sha256' not in bound:
            raise ValueError('Seed binding has no record intake; create a new run and bind the confirmed seed at stage 1.')
    return meta


def validate_check(check, exact=False):
    """Shared argv/timeout rules for verification.json checks and checklist item checks."""
    if not isinstance(check, dict):
        raise ValueError('A check must be an object with argv and timeout_seconds.')
    if exact and set(check) != CHECK_KEYS:
        raise ValueError('A check must have exactly the keys argv, timeout_seconds.')
    argv = check.get('argv')
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError('Every check requires a nonempty argv array.')
    timeout = check.get('timeout_seconds')
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError('Every check requires a positive timeout_seconds.')


def validate_checklist(value):
    """Return the item list of a checklist definition, or raise ValueError naming the problem."""
    if not isinstance(value, dict) or set(value) != {'schema', 'items'}:
        raise ValueError('Checklist must be an object with exactly "schema" and "items".')
    if value['schema'] != 1 or isinstance(value['schema'], bool):
        raise ValueError('Checklist schema must be 1.')
    items = value['items']
    if not isinstance(items, list):
        raise ValueError('Checklist items must be a list.')
    seen = set()
    for index, item in enumerate(items):
        name = item.get('id') if isinstance(item, dict) and isinstance(item.get('id'), str) else f'#{index + 1}'
        if not isinstance(item, dict) or not ITEM_KEYS <= set(item) <= ITEM_KEYS | {'check'}:
            raise ValueError(f'Checklist item {name} must have the keys id, description, verify, and optionally check.')
        if not ITEM_ID.fullmatch(item['id']):
            raise ValueError(f'Checklist item {name} has an invalid id (use [a-z0-9][a-z0-9-]{{0,39}}).')
        if item['id'] in seen:
            raise ValueError(f'Checklist item {name} has a duplicate id.')
        seen.add(item['id'])
        for key in ('description', 'verify'):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f'Checklist item {name} needs a nonempty {key}.')
        if 'check' in item:
            try:
                validate_check(item['check'], exact=True)
            except ValueError as error:
                raise ValueError(f'Checklist item {name} has an invalid check: {error}') from error
    return items


def records_digest(records):
    return digest(json.dumps(records, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8'))


def is_seed_check(item):
    return item.get('origin') in ('operator-confirmed-seed', 'agent-confirmed-seed')


def unproven_goal(items):
    """Says so when every seed check is still provisional: verify may pass, yet nothing proves the goal."""
    seeds = [item for item in items or [] if is_seed_check(item)]
    if seeds and all(item.get('pending') for item in seeds):
        return ('Nothing proves the confirmed goal yet: '
                + '; '.join(f"{item['id']} is provisional (awaiting {item['prerequisite']})" for item in seeds) + '.')
    return None


def record_definitions(records, confirmer='operator'):
    """Tinkery owns validation and rendering; preserve each validated record verbatim."""
    return {'schema': 1, 'items': [
        {**record, 'id': f'seed-{index}', 'description': record['statement'],
         'origin': f'{confirmer}-confirmed-seed', 'locked': True}
        for index, record in enumerate(records, 1)]}


def relative_check_cwd(cwd):
    if (not isinstance(cwd, str) or not cwd.strip() or '\x00' in cwd
            or cwd.startswith('/') or '..' in cwd.split('/') or '\\' in cwd
            or re.match(r'^[A-Za-z]:', cwd) or '\n' in cwd or '\r' in cwd):
        raise ValueError('Seed check cwd must be checkout-relative, without traversal or backslashes.')


def agent_record(record):
    reviewer = record.get('reviewer', '')
    actor = record.get('actor')
    return ((isinstance(reviewer, str) and
             (reviewer.casefold().startswith('agent') or 'not human' in reviewer.casefold()))
            or (isinstance(actor, dict) and actor.get('by') == 'agent'))


def human_review(args, path):
    actor = getattr(args, 'actor', None)
    if actor is not None and (not isinstance(actor, dict) or actor.get('by') not in ('human', 'operator', 'agent')):
        raise ValueError('--actor must be an object declaring by: human, operator or agent.')
    if agent_record(vars(args)) or agent_record(require_run(path)):
        raise ValueError('This is a human-only gate; an agent-recorded reviewer cannot satisfy it. Obtain the actual human decision.')


def seed_baseline(path):
    bound = require_run(path).get('seed_contract')
    if not bound:
        return None
    source = Path(bound['path'])
    source = source if source.is_absolute() else ROOT / source
    if not source.is_file() or digest(source.read_bytes()) != bound['sha256']:
        raise ValueError('The bound seed changed or is missing; rebind the confirmed seed at stage 1.')
    value = read_json(path / 'seed-checks.json')
    baseline = bound.get('definitions')
    if baseline is None:
        baseline = [{k: v for k, v in item.items() if k != 'activation'} for item in value['items']]
    if digest(encoded(baseline)) != bound['definitions_sha256']:
        raise ValueError('Locked seed checks changed; rebind the confirmed seed at stage 1.')
    return baseline


def seed_departures(path):
    baseline = seed_baseline(path)
    if baseline is None:
        return None
    current = read_json(path / 'seed-checks.json')['items']
    before = {item['id']: item for item in baseline}
    after = {item['id']: item for item in current}
    lines = []
    for name in sorted(before.keys() | after.keys()):
        if name not in after:
            lines.append(f"{name} removed: {before[name]['statement']}")
        elif name not in before:
            lines.append(f"{name} added: {after[name]['statement']}")
        else:
            item = after[name]
            definition = {k: v for k, v in item.items() if k != 'activation'}
            for key in sorted(before[name].keys() | definition.keys()):
                if before[name].get(key) != definition.get(key):
                    lines.append(f"{name} {key} changed: `{definition.get(key, '<removed>')}` "
                                 f"instead of `{before[name].get(key, '<absent>')}`")
            if 'activation' in item:
                activation = item['activation']
                lines.append(f"{name} activated: runs `{activation['cmd']}` instead of "
                             f"`{before[name].get('cmd', '<no command>')}` "
                             f"(cwd: {activation['cwd']})")
    target = path / 'checklist.json'
    if not target.is_file():
        raise ValueError(MISSING_CHECKLIST)
    for item in validate_checklist(read_json(target)):
        command = item.get('check', {}).get('argv')
        detail = ' '.join(command) if command else item['description']
        lines.append(f"{item['id']} added worker check: {detail}")
    return lines


def seed_match_evidence(path):
    departures = seed_departures(path)
    if departures is not None and not departures:
        return f"matches confirmed seed (sha256: {require_run(path)['seed_contract']['sha256']})"
    return None


def record_seed_match(path):
    """One automatic receipt per matching definition; repeating verify or stage adds none."""
    evidence = seed_match_evidence(path)
    if evidence and seed_check_items(path) and gate(path, 3) == 'approved':
        previous = latest(path, 3)
        if (previous and previous.get('basis') == 'confirmed-seed' and previous.get('reason') == evidence
                and previous.get('inputs') == inputs(path, 3)):
            return
        write_json(path / 'decisions' / f'3-{time.time_ns()}-{uuid.uuid4().hex}.json', {
            'inputs': inputs(path, 3), 'decision': 'approved', 'basis': 'confirmed-seed',
            'reviewer': 'confirmed-seed binding (automatic)',
            'source': require_run(path)['seed_contract']['path'],
            'seed_binding': require_run(path)['seed_contract'], 'reason': evidence})


def seed_check_items(path):
    """Validate the immutable intake snapshot; seed Markdown is never parsed."""
    bound = require_run(path).get('seed_contract')
    if not bound:
        return []
    target = path / 'seed-checks.json'
    if not target.is_file():
        raise ValueError('Locked seed checks are missing; rebind the confirmed seed at stage 1.')
    value = read_json(target)
    if (not isinstance(value, dict) or set(value) != {'schema', 'items'}
            or type(value['schema']) is not int or value['schema'] != 1 or not isinstance(value['items'], list)):
        raise ValueError('Invalid locked seed checks.')
    seed_baseline(path)
    result = []
    seen = set()
    for item in value['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not re.fullmatch(r'seed-[1-9][0-9]*', item['id']) or item['id'] in seen
                or item.get('kind') not in ('cmd', 'provisional', 'judge')
                or not all(isinstance(item.get(key), str) and item[key].strip()
                           for key in ('statement', 'description', 'source', 'origin'))
                or not is_seed_check(item) or item.get('locked') is not True):
            raise ValueError('Invalid locked seed check definition.')
        seen.add(item['id'])
        fields = {'statement', 'kind', 'source', 'id', 'description', 'origin', 'locked'}
        required = set()
        optional = set()
        if item['kind'] == 'cmd':
            required = {'cmd', 'cwd', 'expect'}
        elif item['kind'] == 'provisional':
            required = {'missing', 'cwd', 'expect'}
            optional = {'cmd', 'activation'}
        else:
            optional = {'how_to_judge'}
        if (not fields | required <= set(item) <= fields | required | optional
                or any(not isinstance(item.get(key), str) or not item[key].strip() for key in required)
                or any(not isinstance(item[key], str) or not item[key].strip()
                       for key in optional - {'activation'} if key in item)):
            raise ValueError('Invalid seed record fields; preserve the cmd, provisional or judge schema.')
        activation = item.get('activation')
        if 'activation' in item:
            if (item['kind'] != 'provisional' or not isinstance(activation, dict)
                    or set(activation) != {'cmd', 'cwd', 'evidence'}
                    or not all(isinstance(v, str) and v.strip() for v in activation.values())):
                raise ValueError('Provisional activation needs a real cmd, cwd and prerequisite evidence.')
            relative_check_cwd(activation['cwd'])
            if any('\x00' in value for value in activation.values()):
                raise ValueError('Provisional activation must not contain NUL.')
        pending = item['kind'] == 'provisional' and activation is None
        base = {'id': item['id'], 'description': item['description'],
                'verify': item.get('how_to_judge', item['statement']) if item['kind'] == 'judge' else item['expect'], 'origin': item['origin'],
                'locked': True, 'pending': pending, 'seed_definition': digest(encoded(item))}
        if item['kind'] == 'judge' or pending:
            result.append({**base, 'operator_attestation': not pending,
                           **({'prerequisite': item['missing']} if pending else {})})
            continue
        command = activation or item
        relative_check_cwd(command['cwd'])
        result.append({**base, 'check': {'argv': ['bash', '-c', command['cmd']],
                                        'timeout_seconds': 120, 'cwd': command['cwd']}})
    return result


def missing_cited_evidence(path):
    """Relative paths cited as activation evidence that exist in neither the run nor the checkout (#76)."""
    target = path / 'seed-checks.json'
    if not seed_check_items(path):
        return []
    missing = []
    for item in read_json(target)['items']:
        for token in item.get('activation', {}).get('evidence', '').split():
            token = token.lstrip('"\'`([').rstrip('"\'`)],;:.')
            if ('/' not in token or '://' in token or token.startswith(('/', '~'))
                    or not (token.endswith('/') or re.search(r'\.[A-Za-z]\w*$', token))):
                continue
            brace = re.search(r'\{([^{}]*,[^{}]*)\}', token)
            for cited in ([token[:brace.start()] + part + token[brace.end():] for part in brace.group(1).split(',')]
                          if brace else [token]):
                kind = Path.is_dir if cited.endswith('/') else Path.exists
                if not (kind(path / cited) or kind(ROOT / cited)) and cited not in missing:
                    missing.append(cited)
    return missing


MISSING_CHECKLIST = 'checklist.json is missing; restore it from Git.'


def load_checklist(path):
    """None when checklist.json was deleted (sdlc.py new always writes one); otherwise the validated items."""
    target = path / 'checklist.json'
    seed_items = seed_check_items(path)
    if not target.exists():
        return seed_items or None
    items = validate_checklist(read_json(target))
    if any(item['id'].startswith('seed-') for item in items) and seed_items:
        raise ValueError('seed- item IDs are reserved for locked operator-confirmed checks.')
    return items + seed_items


def checklist_digest(path):
    """Digest of canonical definitions (whitespace/key order insensitive); None when absent."""
    target = path / 'checklist.json'
    if not target.exists():
        return None
    try:
        return digest(encoded(read_json(target)))
    except ValueError:
        return digest(target.read_bytes())


def marks(path, items, include_agents=False):
    """Latest valid receipt per current item id; receipts for removed items are ignored."""
    ids = {item['id'] for item in items}
    latest_marks = {}
    for receipt in (path / 'marks').glob('*.json') if (path / 'marks').is_dir() else []:
        try:
            record = read_json(receipt)
            key = (record['time_ns'], receipt.name)
            if record['item'] in ids and record['result'] in ('passed', 'failed', 'unknown') and isinstance(record['time_ns'], int):
                if record['item'] not in latest_marks or key > latest_marks[record['item']][0]:
                    latest_marks[record['item']] = (key, record)
        except (ValueError, KeyError, TypeError, OSError):
            continue
    definitions = {item['id']: item for item in items}
    return {name: record for name, (_, record) in latest_marks.items()
            if (not definitions[name].get('operator_attestation')
                or (record.get('reviewer') and record.get('source')
                    and record.get('seed_definition') == definitions[name]['seed_definition']))
            and (include_agents or not (definitions[name].get('operator_attestation') and agent_record(record)))}


def inputs(path, stage):
    metadata = require_run(path)
    files = [path / 'brief.md'] if metadata['profile'] == 'light' else [path / p for p in ARTIFACTS[:stage]]
    files += sorted((ROOT / 'stages').glob('*/CONTEXT.md'))
    values = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in files}
    seed_contract = metadata.get('seed_contract')
    if seed_contract:
        source = Path(seed_contract['path'])
        source = source if source.is_absolute() else ROOT / source
        values['seed-contract'] = digest(source.read_bytes()) if source.is_file() else '<missing>'
    if seed_contract:
        target = path / 'seed-checks.json'
        if 'definitions_sha256' in seed_contract:
            values['seed-checks'] = digest(target.read_bytes()) if target.is_file() else '<missing>'
    if stage == 3 and checklist_digest(path) is not None:
        values[str((path / 'checklist.json').relative_to(ROOT))] = checklist_digest(path)
    return digest(encoded(values))


def latest(path, stage, human_only=False):
    for file in sorted((path / 'decisions').glob(f'{stage}-*.json'), reverse=True):
        record = read_json(file)
        if not human_only or (not agent_record(record) and record.get('basis') != 'confirmed-seed'):
            return record
    return None


def gate(path, stage):
    record = latest(path, stage)
    if stage == 3:
        try:
            seed_check_items(path)
            if require_run(path).get('seed_contract'):
                human = latest(path, stage, human_only=True)
                if human and human['decision'] == 'changes-requested':
                    return 'changes-requested'
            if seed_match_evidence(path):
                return 'approved'
        except (ValueError, OSError, KeyError, TypeError):
            return 'stale' if record else 'pending'
    if record is None or agent_record(record):
        return 'pending'
    if record['inputs'] != inputs(path, stage):
        return 'stale'
    return record['decision']


def stages(path):
    return [3] if require_run(path)['profile'] == 'light' else [1, 2, 3]


def ready(path):
    if load_checklist(path) is None:
        raise ValueError(MISSING_CHECKLIST)
    for stage in stages(path):
        if gate(path, stage) != 'approved':
            raise ValueError(f'Stage {stage} needs a current approval receipt.')


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True)
    if result.returncode:
        lines = [line for line in result.stderr.decode('utf-8', 'replace').splitlines() if line.strip()]
        reason = next((line for line in lines if line.startswith(('fatal:', 'error:'))), lines[0] if lines else f'exit {result.returncode}')
        raise ValueError(f"git {' '.join(args)} failed ({reason}); "
                         'verification needs the scaffold inside a git repository with at least one commit')
    return result.stdout


def strip_generated_rules(data):
    """Drop each well-formed tink:rules block and the blank line tink writes before it (#52), so a removed
    block equals an excluded one; unbalanced markers are left as-is."""
    lines = data.decode('utf-8', errors='surrogateescape').splitlines(keepends=True)
    blocks, problems = rules_blocks(lines)
    if not blocks or any('duplicate' not in problem for problem in problems):
        return data
    out, cursor = [], 0
    for begin, end, _ in blocks:
        separator = begin > cursor and not lines[begin - 1].strip()
        out.append(''.join(lines[cursor:begin - separator]).encode('utf-8', errors='surrogateescape'))
        cursor = end + 1
    out.append(''.join(lines[cursor:]).encode('utf-8', errors='surrogateescape'))
    return b''.join(out)


def disposable(relative):
    """Untracked paths that are generated, not authored: Python caches and what tink mounts leave in .tink/.

    Tracked files stay covered even when their name resembles generated output."""
    path = Path(relative)
    if any(part in {'__pycache__', '.pytest_cache', '.ruff_cache'} for part in path.parts) or path.suffix in {'.pyc', '.pyo'}:
        return True
    return relative == '.tink/.gitignore' or relative.startswith(('.tink/.active/', '.tink/cache/'))


def submodule_head(path):
    """A submodule or nested repository counts by its checked-out commit; changes inside it are its own."""
    if not (path / '.git').exists():
        return b'<uninitialized submodule>'
    head = run_git(path, 'rev-parse', 'HEAD')
    return b'submodule ' + (head.stdout.strip().encode() if head.returncode == 0 else b'<no commit>')


def snapshot_files():
    """Per-file fingerprint map of the candidate: {relative: [digest, mode]}."""
    tracked = set(git('ls-files', '-z', '--cached').split(b'\0'))
    submodules = {entry.split(b'\t', 1)[1] for entry in git('ls-files', '-z', '--stage').split(b'\0')
                  if entry.startswith(b'160000 ')}
    names = tracked | set(git('ls-files', '-z', '--others', '--exclude-standard').split(b'\0'))
    files = {}
    for name in names:
        if not name:
            continue
        relative = os.fsdecode(name)
        if relative.startswith('runs/'):
            continue
        if name not in tracked and disposable(relative):
            continue
        path = ROOT / relative
        if path.is_symlink():
            value = os.readlink(path).encode()
        elif path.is_dir() and (name in submodules or relative.endswith('/')):  # untracked nested repos end in /
            value = submodule_head(path)
        elif path.is_file():
            value = path.read_bytes()
            if relative == 'AGENTS.md':
                value = strip_generated_rules(value)
        elif not path.exists():
            value = b'<deleted>'
        else:
            raise ValueError(f'Unsupported candidate path: {relative}')
        files[relative] = [digest(value), path.lstat().st_mode if path.exists() or path.is_symlink() else 0]
    return files


def snapshot():
    head = git('rev-parse', 'HEAD').decode().strip()
    return {'head': head, 'tree': digest(encoded(snapshot_files()))}


def tracked_paths():
    return {os.fsdecode(n) for n in git('ls-files', '-z', '--cached').split(b'\0') if n}


def changed_report(before_files, after_files):
    changed = sorted(name for name in set(before_files) | set(after_files) if before_files.get(name) != after_files.get(name))
    if not changed:
        return ''
    shown = changed[:5]
    text = ' changed: ' + ', '.join(shown) + (f' (+{len(changed) - 5} more)' if len(changed) > 5 else '')
    tracked = tracked_paths()
    if any(name in tracked and (Path(name).suffix in {'.pyc', '.pyo'} or '__pycache__' in Path(name).parts) for name in shown):
        text += ' Hint: tracked bytecode files change during test runs; untrack them and ignore __pycache__'
    created = [name for name in shown if name not in before_files and name not in tracked]
    if created:
        text += (' Hint: a check created these untracked files. Add generated output to .gitignore; '
                 'if the file is meant to be committed, commit it and rerun verification.')
    return text


def checks_config():
    config = read_json(ROOT / '_system/verification.json')
    checks = config.get('checks')
    if not isinstance(config.get('require_tink'), bool) or not isinstance(checks, list) or not checks:
        raise ValueError('Verification requires an explicit Tink policy and a nonempty check list.')
    for check in checks:
        validate_check(check)
    return config


def unlisted_authors(config):
    """Commits after `authors.since` whose author matches no `authors.allow` pattern (#78); earlier history is never judged."""
    policy = config.get('authors')
    if policy is None:
        return []
    if (not isinstance(policy, dict) or set(policy) != {'since', 'allow'} or not isinstance(policy['since'], str)
            or not isinstance(policy['allow'], list) or not policy['allow']
            or not all(isinstance(p, str) and p.strip() for p in policy['allow'])):
        raise ValueError('verification.json authors needs a since commit and a nonempty allow list of "Name <email>" patterns.')
    listed = run_git(ROOT, 'log', '--format=%h %an <%ae>', f"{policy['since']}..HEAD")
    if listed.returncode:
        raise ValueError(f"verification.json authors.since is not a commit before HEAD: {git_line(listed)}")
    return [line for line in listed.stdout.splitlines()
            if not any(fnmatch.fnmatchcase(line.split(' ', 1)[1], pattern) for pattern in policy['allow'])]


def test_lock(path):
    lock = path / 'test-lock.json'
    if not lock.exists():
        if require_run(path)['kind'] == 'bug':
            raise ValueError('Bug runs require a reproduction test lock.')
        return None
    record = read_json(lock)
    for name, expected in record['files'].items():
        target = ROOT / name
        if target.is_symlink() or not target.is_file() or digest(target.read_bytes()) != expected:
            raise ValueError(f'Locked test input changed or disappeared: {name}')
    return digest(lock.read_bytes())


def manual_marks(path):
    """Digest of the latest receipt per attested (check-less) item: a later mark, or an edited receipt, stales verification."""
    items = load_checklist(path) or []
    latest = marks(path, items)
    return {item['id']: digest(encoded(latest[item['id']])) for item in items
            if 'check' not in item and not item.get('pending') and item['id'] in latest}


def evidence_inputs(path):
    attested = manual_marks(path)
    return {'candidate': snapshot(), 'inputs': inputs(path, 3),
            'policy': digest((ROOT / '_system/verification.json').read_bytes()),
            'test_lock': test_lock(path),
            **({'checklist': checklist_digest(path), 'manual_marks': attested}
               if checklist_digest(path) is not None or seed_check_items(path) else {})}


def create(args):
    path = run_path(args.run)
    path.parent.mkdir(exist_ok=True)
    with locked(path.parent / f'.{args.run}.lock'):
        if path.exists():
            raise ValueError('Run already exists; nothing overwritten.')
        temporary = Path(tempfile.mkdtemp(prefix=f'.{args.run}-', dir=path.parent))
        try:
            write_json(temporary / 'run.json', {'profile': args.profile, 'kind': args.kind, 'schema': 1,
                                              'run_id': str(uuid.uuid4())})
            write_json(temporary / 'checklist.json', {'schema': 1, 'items': []})
            if args.profile == 'light':
                shutil.copyfile(ROOT / '_shared/brief-template.md', temporary / 'brief.md')
            else:
                for target, template in zip(ARTIFACTS, ['intent', 'spec', 'plan']):
                    dest = temporary / target
                    dest.parent.mkdir(parents=True)
                    shutil.copyfile(ROOT / '_shared' / f'{template}-template.md', dest)
            temporary.rename(path)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    print(f'Created {path}. Next: define the change and obtain a human review.')


def decide(args):
    path = run_path(args.run)
    require_run(path)
    human_review(args, path)
    with locked(path / '.writer-lock'):
        if args.stage not in stages(path):
            raise ValueError('Light runs use stage 3 for their combined definition gate.')
        if args.decision == 'approved':
            bound = require_run(path).get('seed_contract')
            if bound:
                source = Path(bound['path'])
                source = source if source.is_absolute() else ROOT / source
                if not source.is_file() or digest(source.read_bytes()) != bound['sha256']:
                    raise ValueError(f"The seed contract {bound['path']} changed or is missing since stage 1 was opened; "
                                     f'confirm it again, then run: sdlc.py stage {args.run} 1 --seed-contract {bound.get("source", bound["path"])}')
        items = load_checklist(path) if args.decision == 'approved' and args.stage == 3 else ()
        if items is None:
            raise ValueError(MISSING_CHECKLIST)
        if items == []:
            raise ValueError(f'Define at least one checklist item before approval. Edit runs/{args.run}/checklist.json '
                             '(each item needs id, description, verify; optional check).')
        if args.decision == 'approved':
            seed_check_items(path)
            for stage in stages(path):
                if stage < args.stage and gate(path, stage) != 'approved':
                    raise ValueError(f'Approve stage {stage} first.')
        write_json(path / 'decisions' / f'{args.stage}-{time.time_ns()}-{uuid.uuid4().hex}.json', {
            'inputs': inputs(path, args.stage), 'decision': args.decision,
            'reviewer': args.reviewer, 'source': args.source, 'reason': args.reason,
            **({'actor': args.actor} if getattr(args, 'actor', None) is not None else {}),
            **({'seed_evidence': seed_match_evidence(path), 'departures': seed_departures(path)}
               if args.stage == 3 and require_run(path).get('seed_contract') else {})})
    print('Recorded local review receipt. Release approval must be independently authenticated by the forge.')


def run_check(check, log):
    with subprocess.Popen(check['argv'], cwd=ROOT / check.get('cwd', '.'), stdout=log, stderr=subprocess.STDOUT,
                          start_new_session=True) as process:
        try:
            returncode = process.wait(timeout=check['timeout_seconds'])
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.wait()
    return subprocess.CompletedProcess(check['argv'], returncode)


def dry_run(args):
    path = run_path(args.run)
    require_run(path)
    with locked(path / '.writer-lock'):
        items = load_checklist(path)
        if not items:
            raise ValueError('Define checklist items before a dry run.')
        log_path = path / 'checklist-dry-run.log'
        problems = []
        with log_path.open('w') as log:
            for item in items:
                if 'check' not in item:
                    continue
                check = item['check']
                log.write(f"\n# checklist item {item['id']}\n$ {json.dumps(check['argv'])}\n")
                log.flush()
                try:
                    result = run_check(check, log)
                    outcome = f'exit {result.returncode}'
                    if result.returncode:
                        problems.append(item['id'])
                        outcome += ' (inspect output; reproduction failure may be expected)'
                except (subprocess.TimeoutExpired, OSError) as error:
                    outcome = f'{type(error).__name__}: {error}'
                    problems.append(item['id'])
                log.write(f'\n{outcome}\n')
                print(f"{item['id']}: {outcome}")
        print(f'Output: {log_path.relative_to(ROOT)}')
        print('Dry run only: no approval, marks, or verification receipt recorded. Review output before the definition gate.')
        if problems:
            raise ValueError(f"Dry-run checks need review: {', '.join(problems)} (output: {log_path.relative_to(ROOT)})")


def describe(error):
    """Error text plus any pointer to the check output attached where the check ran."""
    return f"{error}{getattr(error, 'sdlc_hint', '')}"


def verification_files(path):
    output = path / '04-test/output'
    return output / 'verification.json', output / 'test-log.md'


def verify(args):
    path = run_path(args.run)
    require_run(path)
    with locked(path / '.writer-lock'):
        receipt, log_path = verification_files(path)
        log_hint = f' (output: {log_path.relative_to(ROOT)})'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(receipt, {'result': 'running'})
        seed_results = {}
        try:
            ready(path)
            record_seed_match(path)
            missing = missing_cited_evidence(path)
            if missing:
                raise ValueError('Cited evidence is missing from this checkout: ' + ', '.join(missing))
            config = checks_config()
            unlisted = unlisted_authors(config)
            if unlisted:
                raise ValueError('Commits by authors verification.json does not allow: ' + '; '.join(unlisted)
                                 + ' (allowed: ' + ', '.join(config['authors']['allow']) + ')')
            before_files = snapshot_files()
            before = evidence_inputs(path)
            items = load_checklist(path)
            seed_results = {item['id']: 'pending' if 'check' not in item else 'not-run'
                            for item in items or [] if is_seed_check(item)}
            checks = list(config['checks'])
            if config['require_tink']:
                checks.insert(0, {'argv': ['tink', 'skill', 'check'], 'timeout_seconds': 120})
            with log_path.open('w') as log:
                for check in checks:
                    log.write(f"\n$ {json.dumps(check['argv'])}\n")
                    log.flush()
                    try:
                        result = run_check(check, log)
                    except (subprocess.TimeoutExpired, OSError) as error:
                        log.write(f'{type(error).__name__}: {error}\n')
                        error.sdlc_hint = log_hint
                        raise
                    if result.returncode:
                        raise ValueError(f"Check failed ({result.returncode}): {check['argv']}{log_hint}")
                for item in items or []:
                    if 'check' not in item:
                        continue
                    check = item['check']
                    seed = is_seed_check(item)
                    label = 'seed check' if seed else 'checklist item'
                    failure = 'Seed check failed' if seed else 'Checklist check failed'
                    if seed:
                        seed_results[item['id']] = 'failed'
                    log.write(f"\n# {label} {item['id']}\n$ {json.dumps(check['argv'])}\n")
                    if seed:
                        log.write(f"cwd: {check['cwd']}\nexpect (operator confirms): {item['verify']}\n")
                    log.flush()
                    try:
                        result = run_check(check, log)
                    except (subprocess.TimeoutExpired, OSError) as error:
                        log.write(f'{type(error).__name__}: {error}\n')
                        raise ValueError(f"{failure}: {item['id']}{log_hint}") from error
                    if result.returncode:
                        raise ValueError(f"{failure}: {item['id']}{log_hint}")
                    if seed:
                        seed_results[item['id']] = 'proven'
            if before != evidence_inputs(path):
                raise ValueError('Candidate or inputs changed during verification; rerun against stable inputs.'
                                 + changed_report(before_files, snapshot_files()))
            ready(path)
            proof = {}
            if items is not None:
                marked = marks(path, items)
                attested = [item['id'] for item in items if 'check' not in item and not item.get('pending')]
                for item in items:
                    if is_seed_check(item) and 'check' not in item:
                        record = marked.get(item['id'], {})
                        accepted = record.get('result') == 'passed' and record.get('reviewer') and record.get('source')
                        seed_results[item['id']] = 'attested' if accepted and not item.get('pending') else 'pending'
                missing = [i for i in attested if marked.get(i, {}).get('result') != 'passed'
                           or (i in seed_results and seed_results[i] != 'attested')]
                if missing:
                    raise ValueError(f"Checklist incomplete: {', '.join(missing)} "
                                     f'(mark them with: sdlc.py mark {args.run} <id> passed --evidence "...")')
                proof = {'checklist_items': {item['id']: 'proven' if 'check' in item else 'attested'
                                             for item in items if not is_seed_check(item)}}
                if seed_results:
                    proof['seed_checks'] = seed_results
            write_json(receipt, {'result': 'passed', **before, **proof, 'log': digest(log_path.read_bytes())})
        except Exception as error:
            defined = {}
            with contextlib.suppress(Exception):
                defined = {'inputs': inputs(path, 3)}  # Lets status retire this error once definitions change (#75).
            write_json(receipt, {'result': 'failed', 'error': describe(error), **defined,
                                 **({'seed_checks': seed_results} if seed_results else {})})
            raise
    if seed_results:
        confirmer = 'agent' if any(item.get('origin') == 'agent-confirmed-seed' for item in items) else 'operator'
        print(f'Seed checks ({confirmer}-confirmed): ' + ', '.join(f'{name}: {state}' for name, state in seed_results.items()))
        print('Worker-written checks: passed (supporting evidence only).')
        if unproven := unproven_goal(items):
            print(unproven)
    print('Configured checks passed for the recorded candidate. Human release review remains required.')


def lock_tests(args):
    path = run_path(args.run)
    require_run(path)
    with locked(path / '.writer-lock'):
        record = path / 'test-lock.json'
        if record.exists():
            raise ValueError('Test lock already exists. A corrected baseline requires a new run and independent review.')
        files = {}
        for name in args.paths:
            target = ROOT / name
            if target.resolve() != target.absolute() or not target.is_file() or not target.is_relative_to(ROOT) or '..' in Path(name).parts:
                raise ValueError('Lock only regular files inside the checkout.')
            files[str(target.relative_to(ROOT))] = digest(target.read_bytes())
        reproduction = run_reproduction(args.reproduce, path / 'reproduction-log.md') if args.reproduce else None
        write_json(record, {'files': files, 'candidate': snapshot(), 'review_source': args.source,
                            'failure_evidence': args.failure_evidence, **({'reproduction': reproduction} if reproduction else {})})
    if reproduction:
        print(f"Reproduction failed with exit {reproduction['exit_code']} on this tree, as a bug baseline must. "
              'Local test baseline recorded. CI must still validate it and protect the baseline.')
    else:
        print('Local test baseline recorded. The failing reproduction was NOT run here (pass --reproduce to run it now); '
              'CI must independently validate it and protect the baseline.')


REPRODUCE_TIMEOUT = 120


def run_reproduction(raw, log_path):
    """Run the reproduction command now; a bug baseline is only real if it fails on the unfixed code."""
    try:
        argv = json.loads(raw)
    except ValueError as error:
        raise ValueError(f'--reproduce must be a JSON argv array, e.g. \'["python3","-m","unittest","tests.test_x"]\': {error}') from error
    validate_check({'argv': argv, 'timeout_seconds': REPRODUCE_TIMEOUT})
    with log_path.open('w') as log:
        log.write(f'$ {json.dumps(argv)}\n')
        log.flush()
        try:
            result = run_check({'argv': argv, 'timeout_seconds': REPRODUCE_TIMEOUT}, log)
        except (subprocess.TimeoutExpired, OSError) as error:
            log.write(f'{type(error).__name__}: {error}\n')
            raise ValueError(f'Reproduction could not run to completion ({type(error).__name__}); see {log_path.relative_to(ROOT)}') from error
    if result.returncode == 0:
        log_path.unlink()
        raise ValueError('Reproduction passed on the current tree. A bug baseline must fail before the fix; '
                         'fix the test (or the reproduction command) and lock again.')
    return {'argv': argv, 'exit_code': result.returncode, 'log': digest(log_path.read_bytes())}


def mark(args):
    path = run_path(args.run)
    require_run(path)
    evidence = args.evidence.strip()
    if not evidence:
        raise ValueError('Evidence must be nonempty.')
    with locked(path / '.writer-lock'):
        items = load_checklist(path)
        if items is None:
            raise ValueError(MISSING_CHECKLIST)
        if args.item not in {item['id'] for item in items}:
            ids = ', '.join(item['id'] for item in items)
            raise ValueError(f'Unknown checklist item: {args.item} (items: {ids})')
        item = next(item for item in items if item['id'] == args.item)
        if item.get('pending'):
            raise ValueError('Provisional seed check is pending; activate it with prerequisite evidence and renew definition approval.')
        if item.get('operator_attestation'):
            human_review(args, path)
        if item.get('operator_attestation') and not (args.reviewer and args.reviewer.strip() and args.source and args.source.strip()):
            raise ValueError('Seed checks require an operator attestation: --reviewer and --source of the actual operator observation.')
        if any(item['id'] == args.item and 'check' in item for item in items):
            raise ValueError(f'item {args.item} has an executable check; run verify to prove it '
                             f'(its check runs during: python3 _system/scripts/sdlc.py verify {args.run})')
        if gate(path, 3) != 'approved':
            raise ValueError('Stage 3 needs a current approval receipt before items can be marked.')
        if args.result == 'unknown' and not item.get('operator_attestation'):
            raise ValueError('unknown is for an operator observation that does not address this expectation.')
        shared = sorted(other for other, record in marks(path, items).items() if other != args.item
                        and record.get('reviewer') and record['evidence'] == evidence)
        if item.get('operator_attestation') and shared and not args.covers_all:
            raise ValueError(f"The same evidence already attests {', '.join(shared)}; give evidence for this "
                             f"expectation ({item['verify']}), mark it unknown, or pass --covers-all if the "
                             'operator meant it to cover each of them.')
        now = time.time_ns()
        write_json(path / 'marks' / f'{args.item}-{now}-{uuid.uuid4().hex}.json', {
            'item': args.item, 'result': args.result, 'evidence': evidence,
            **({'reviewer': args.reviewer, 'source': args.source, 'seed_definition': item['seed_definition']}
               if item.get('operator_attestation') else {}),
            **({'actor': args.actor} if getattr(args, 'actor', None) is not None else {}),
            **({'covers_all': True} if args.covers_all else {}),
            'candidate': snapshot()['tree'], 'time_ns': now})
    if item.get('operator_attestation'):
        print(f"Expectation: {item['verify']}")
    print(f'Recorded {args.result} receipt for {args.item}.')


def checklist_view(path, verified=False):
    try:
        items = load_checklist(path)
    except (ValueError, TypeError) as error:
        return {'state': 'invalid', 'items': [], 'error': str(error)}
    if items is None:
        return {'state': 'missing', 'items': []}
    try:
        tree = snapshot()['tree'] if items else None
    except (ValueError, OSError, subprocess.SubprocessError):
        tree = None
    marked = marks(path, items, include_agents=True)
    result = []
    for item in items:
        record = marked.get(item['id'])
        automatic = 'check' in item
        agent = bool(record and item.get('operator_attestation') and agent_record(record))
        state = ('passed' if verified else 'pending') if automatic else (record['result'] if record and not agent else 'pending')
        result.append({**item, 'status': state, 'automatic': automatic,
                       **({'attribution': 'agent-recorded'} if agent else {}),
                       'proof': 'automated' if automatic else 'attested',
                       'mark': {key: record.get(key) for key in ('item', 'result', 'evidence', 'candidate', 'time_ns', 'covers_all', 'reviewer', 'actor')} if record else None,
                       'needs_recheck': bool(not automatic and record and state == 'passed'
                                             and tree is not None and record.get('candidate') != tree)})
    return {'state': 'defined' if items else 'empty', 'items': result}


def checklist_text(view):
    messages = {'missing': 'MISSING (restore checklist.json from Git)',
                'empty': 'no items defined', 'invalid': f"invalid ({view.get('error')})"}
    if view['state'] in messages:
        return 'Checklist: ' + messages[view['state']]
    items = view['items']
    passed = sum(item['status'] == 'passed' for item in items)
    proven = sum(item['automatic'] for item in items)
    provisional = sum(bool(item.get('pending')) for item in items)
    lines = [f'Checklist: {passed}/{len(items)} passed ({proven} with a check, {len(items) - proven - provisional} attested'
             + (f', {provisional} provisional)' if provisional else ')')]
    for item in items:
        if item['status'] != 'passed':
            suffix = (' (provisional; awaiting prerequisite and reviewed activation)' if item.get('pending') else
                      ' (operator attestation required)' if item.get('operator_attestation') else
                      ' (its check runs in verify)' if item['automatic'] else '')
            lines.append(f"  - {item['id']}: {item['status']}{suffix}")
        elif item['needs_recheck']:
            lines.append(f"  - {item['id']}: attested before the latest changes; re-check it only if the change affects it")
        elif item['mark'] and item['mark'].get('covers_all'):
            lines.append(f"  - {item['id']}: passed; the operator's evidence covers several expectations")
    return '\n'.join(lines)


def evidence_matches(record, current):
    # HEAD records provenance. Evidence-only commits must not stale unchanged
    # candidate contents; CI still needs a check for the actual merge revision.
    return (record.get('candidate', {}).get('tree') == current['candidate']['tree']
            and all(record.get(key) == value for key, value in current.items() if key != 'candidate'))


def verification_matches(path, record):
    _, log = verification_files(path)
    return (record.get('result') == 'passed' and evidence_matches(record, evidence_inputs(path))
            and record['log'] == digest(log.read_bytes()))


def writer_active(path):
    lock = path / '.writer-lock'
    if not lock.exists():
        return False
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Writer lock must be a regular file.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def failed_reason(path, record):
    """A failure's own error while its definitions are current; never an error from definitions since replaced."""
    try:
        replaced = 'inputs' in record and record['inputs'] != inputs(path, 3)
    except (ValueError, OSError, KeyError, TypeError):
        replaced = False
    if replaced:
        return 'The last verification failed against earlier definitions. Run verification again.'
    return str(record.get('error', 'Verification failed. Inspect the log, then rerun it.'))


def build_checkout(run):
    """The other checkout Git records on branch `run`, where its build evidence lives; None if there is none."""
    listed = run_git(ROOT, 'worktree', 'list', '--porcelain')
    for block in listed.stdout.split('\n\n') if listed.returncode == 0 else []:
        fields = dict(line.split(' ', 1) for line in block.splitlines() if ' ' in line)
        if fields.get('branch') == f'refs/heads/{run}' and Path(fields['worktree']).resolve() != ROOT.resolve():
            return fields['worktree']
    return None


@contextlib.contextmanager
def reading_build_checkout(run):
    """Reads `run` from its build checkout, where its evidence lives, with this checkout's own code; never runs code from there."""
    global ROOT
    checkout = build_checkout(run)
    if checkout and (Path(checkout) / 'runs' / run / 'run.json').is_file():
        ROOT = Path(checkout)
    try:
        yield
    finally:
        ROOT = HOME


def status_state(path):
    """One calculation for the human CLI and machine clients; never an approval."""
    meta = require_run(path)
    if not isinstance(meta, dict) or meta.get('profile') not in ('light', 'full'):
        raise ValueError('Run profile must be light or full.')
    if not isinstance(meta.get('kind', 'feature'), str):
        raise ValueError('Run kind must be a string.')
    gates, blocked = [], False
    for stage in stages(path):
        state = gate(path, stage)
        if state not in ('approved', 'pending', 'stale', 'changes-requested'):
            raise ValueError(f'Unknown gate state: {state}')
        entry = {'stage': stage, 'status': state, 'blocked': blocked}
        recorded = latest(path, stage)
        if recorded and agent_record(recorded):
            entry['attribution'] = 'agent-recorded'
        if stage == 3:
            with contextlib.suppress(ValueError, OSError, KeyError, TypeError):
                if evidence := seed_match_evidence(path):
                    entry['evidence'] = evidence
        gates.append(entry)
        blocked = blocked or state != 'approved'
    record_path, _ = verification_files(path)
    record = read_json(record_path) if record_path.exists() else None
    if record is not None:
        if not isinstance(record, dict) or record.get('result') not in ('passed', 'failed', 'running'):
            raise ValueError('Unrecognized verification receipt.')
        for key in ('error', 'log'):
            if record.get(key) is not None and not isinstance(record[key], str):
                raise ValueError(f'Verification {key} must be a string or null.')
        identity = record.get('candidate')
        if identity is not None and (not isinstance(identity, dict) or not all(
                isinstance(identity.get(key), str) and identity[key] for key in ('head', 'tree'))):
            raise ValueError('Verification candidate must contain nonempty head and tree strings.')
    active = writer_active(path)
    verified = False
    if not blocked and not active and record is not None:
        try:
            verified = verification_matches(path, record)
        except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError):
            pass
    if active:
        state = 'running'
    elif blocked:
        state = 'blocked'
    elif verified:
        state = 'current'
    elif record is None:
        state = 'not-run'
    else:
        state = {'passed': 'stale', 'failed': 'failed', 'running': 'interrupted'}[record['result']]
    checklist = checklist_view(path, verified)
    errors = []
    try:
        config = checks_config()
    except (ValueError, OSError, TypeError, AttributeError) as error:
        config = None
        errors.append(str(error))
    try:
        test_lock(path)
    except (ValueError, OSError, KeyError, TypeError) as error:
        errors.append(str(error))
    reproduction = None
    if (path / 'test-lock.json').is_file():
        try:
            recorded = read_json(path / 'test-lock.json').get('reproduction')
            reproduction = {'executed': True, 'argv': recorded['argv'], 'exit_code': recorded['exit_code']} if recorded else {'executed': False}
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            pass
    if checklist['state'] == 'invalid':
        errors.append(checklist['error'])
    first_gate = next((g for g in gates if g['status'] != 'approved'), None)
    gate_reason = f"revise/review stage {first_gate['stage']}; record the human decision." if first_gate else ''
    if first_gate and first_gate['stage'] == 3:
        if meta['profile'] == 'light' and (not (path / 'brief.md').is_file() or checklist['state'] in ('empty', 'missing', 'invalid')):
            gate_reason = f'Define brief.md and checklist.json in runs/{path.name} before requesting human review.'
        elif any(item['automatic'] for item in checklist['items']):
            gate_reason = (f'Run python3 _system/scripts/sdlc.py dry-run {path.name} before definition review; '
                           'inspect each check output, then record the actual human decision.')
    definition = None
    if meta.get('seed_contract') and checklist['state'] != 'invalid':
        with contextlib.suppress(ValueError, OSError, KeyError, TypeError):
            departures = seed_departures(path)
            needs_review = departures and next(g for g in gates if g['stage'] == 3)['status'] != 'approved'
            definition = {'departures': departures, 'evidence': seed_match_evidence(path),
                          'question': 'Accept these departures from the confirmed seed?' if needs_review else None}
            if first_gate and first_gate['stage'] == 3 and departures:
                gate_reason = definition['question'] + ' Record only the actual human decision.'
    unavailable = 'A writer already owns this run.' if active else gate_reason
    verify_reason = unavailable or (errors[0] if errors else '')
    manual = any(not item['automatic'] for item in checklist['items'])
    mark_reason = unavailable or ('' if manual else 'This run has no manual checklist items to attest.')
    checks = (config['checks'] if config else []) + [i['check'] for i in checklist['items'] if i['automatic']]
    timeout = sum(c['timeout_seconds'] for c in checks) + 30 + (120 if config and config['require_tink'] else 0)
    next_action = unavailable or (errors[0] if errors else '') or {
        'current': (f'{unproven} Then independent PR review and release checks.'
                    if (unproven := unproven_goal(checklist['items'])) else 'independent PR review and release checks.'),
        'stale': 'The previous pass no longer matches this candidate or its evidence. Run verification again.',
        'interrupted': 'The previous verification did not finish. Inspect the log, then rerun it.',
        'failed': failed_reason(path, record) if record else '',
    }.get(state, (f'Building in {building}; read status there.' if (building := build_checkout(path.name)) else
                  f"implement the confirmed seed (runs/{path.name}/seed-contract.md), then run verification."
                  if meta.get('seed_contract') else 'implement the approved brief, then run verification.'))
    if ROOT != HOME:
        next_action = f'In {ROOT}: {next_action}'
    bound = meta.get('seed_contract')
    seed_contract = {key: bound[key] for key in ('path', 'sha256', 'source')} if bound is not None else None
    if bound is not None and bound.get('confirmation', {}).get('schema_version') == 3:
        seed_contract['confirmation'] = bound['confirmation']
    return {'slug': path.name, 'run_id': meta['run_id'], 'seed_contract': seed_contract,
            **({'checkout': str(ROOT)} if ROOT != HOME else {}),
            'meta': {'profile': meta['profile'], 'kind': meta.get('kind', 'feature')},
            'gates': gates, 'verification_status': state, 'has_lock': active,
            **({'definition': definition} if definition is not None else {}),
            'verification': {**{key: record.get(key) for key in ('result', 'candidate', 'log', 'error')}, 'passed': verified} if record else None,
            'checklist': checklist['items'], 'checklist_state': checklist['state'],
            'checklist_text': checklist_text(checklist), 'next_action': next_action, 'errors': errors,
            'reproduction': reproduction,
            'verification_config': config,
            'actions': {'verify': {'allowed': not verify_reason, 'reason': verify_reason,
                                   'timeout_seconds': timeout if not verify_reason else None},
                        'mark': {'allowed': not mark_reason, 'reason': mark_reason}}}


def status_text(view):
    bound = view['seed_contract']
    lines = [f"Seed contract: {bound['path']} (sha256: {bound['sha256']}; source: {bound['source']})"] if bound else []
    for gate_info in view['gates']:
        stage, state, blocked = gate_info['stage'], gate_info['status'], gate_info['blocked']
        lines.append(f'Stage {stage}: {state}' + (' (blocked by upstream gate)' if blocked else '')
                     + (' (agent-recorded; not human approval)' if gate_info.get('attribution') else ''))
        if stage == 3 and view.get('definition'):
            lines.extend(view['definition']['departures'])
            if view['definition']['evidence']:
                lines.append(view['definition']['evidence'])
        if state != 'approved' and not blocked:
            lines.append(f"Next: {view['next_action']}")
            if not view['next_action'].startswith('Define brief.md'):
                lines.extend(['After human review, fill in this command from the scaffold root (DECISION: approved or changes-requested):',
                              f"  python3 _system/scripts/sdlc.py decide {view['slug']} {stage} DECISION "
                              "--reviewer 'REVIEWER' --source 'SOURCE' --reason 'REASON'"])
    lines.append(view['checklist_text'])
    repro = view.get('reproduction')
    if repro is not None:
        lines.append(f"Reproduction: failed with exit {repro['exit_code']} when locked ({' '.join(repro['argv'])})" if repro['executed'] else
                     'Reproduction: recorded from review evidence, not run locally; CI must confirm it fails on the unfixed code '
                     '(lock-tests --reproduce runs it at lock time).')
    if view['verification'] is not None:
        lines.append('Verification: ' + ('current' if view['verification_status'] == 'current' else 'failed, stale, or blocked'))
    else:
        lines.append('Verification: not run (implementation may be pending; a text log is not passing evidence)')
    if all(g['status'] == 'approved' for g in view['gates']):
        lines.append(f"Next: {view['next_action']}")
    lines.append('Deployment: not inferred from local review files; consult the deployment system.')
    return '\n'.join(lines)


def regular_evidence(path):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError(f'Symlinked evidence is not supported: {path.relative_to(ROOT)}')
    if path.exists() and not path.is_file():
        raise ValueError(f'Evidence must be a regular file: {path.relative_to(ROOT)}')
    return path


def run_view(slug):
    with reading_build_checkout(run_path(slug).name):
        return checkout_run_view(slug)


def checkout_run_view(slug):
    path = run_path(slug)
    if not (path / 'run.json').is_file():
        raise FileNotFoundError(f"Run '{slug}' not found.")
    names = {'brief': 'brief.md', 'intent': ARTIFACTS[0], 'spec': ARTIFACTS[1], 'plan': ARTIFACTS[2]}
    receipt, log = verification_files(path)
    evidence = [path / name for name in ['run.json', 'checklist.json', 'test-lock.json', '.writer-lock']]
    evidence += [receipt, log] + [path / name for name in names.values()] + [ROOT / '_system/verification.json']
    evidence += list((path / 'decisions').glob('*.json')) + list((path / 'marks').glob('*.json'))
    for file in evidence:
        regular_evidence(file)
    view = status_state(path)
    artifacts = {'brief': names['brief']} if view['meta']['profile'] == 'light' else {k: v for k, v in names.items() if k != 'brief'}
    view['artifacts'] = {key: (path / name).read_text() for key, name in artifacts.items() if (path / name).exists()}
    decisions = []
    for file in sorted((path / 'decisions').glob('*.json')):
        identity = re.fullmatch(r'([1-6])-(\d+)-[a-zA-Z0-9]+', file.stem)
        if not identity:
            raise ValueError(f'Unrecognized decision receipt name: {file.name}')
        record = read_json(file)
        if not isinstance(record, dict) or record.get('decision') not in ('approved', 'changes-requested'):
            raise ValueError(f'Unrecognized decision receipt: {file.name}')
        for key in ('reviewer', 'source', 'reason'):
            if not isinstance(record.get(key), str):
                raise ValueError(f'Decision {key} must be a string: {file.name}')
        decisions.append({'id': file.stem, 'stage': int(identity[1]), 'timestamp_ns': int(identity[2]),
                          **{key: record.get(key) for key in ('decision', 'reviewer', 'source', 'reason')},
                          **({'attribution': 'agent-recorded'} if agent_record(record) else {}),
                          **({key: record[key] for key in ('actor', 'basis', 'seed_evidence', 'departures') if key in record})})
    view['decisions'] = decisions
    agent_marks = []
    for file in sorted((path / 'marks').glob('*.json')):
        with contextlib.suppress(ValueError, KeyError, TypeError, OSError):
            record = read_json(file)
            if isinstance(record, dict) and agent_record(record):
                agent_marks.append({'item': record['item'], 'attribution': 'agent-recorded',
                                    'satisfies_human_gate': False, 'path': str(file.relative_to(ROOT))})
    if agent_marks:
        view['agent_marks'] = agent_marks
    text, truncated = '', False
    if log.exists():
        with log.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            truncated = size > 262144
            stream.seek(max(0, size - 262144))
            text = stream.read(262144).decode('utf-8', errors='replace')
    view['log'] = {'path': str(log.relative_to(ROOT)), 'text': text, 'truncated': truncated}
    view['cli_status'] = status_text(view)
    del view['checklist_text']
    return view


def machine_output(value):
    print(json.dumps({'protocol': 'tink-sdlc', 'api_version': API_VERSION, 'workspace': str(ROOT), **value}))


def capabilities(args):
    if args.json:
        machine_output({'capabilities': API_CAPABILITIES})
    else:
        print(f'tink-sdlc API {API_VERSION}: ' + ', '.join(API_CAPABILITIES))


def status(args):
    if args.run:
        if getattr(args, 'json', False):
            machine_output({'run': run_view(args.run)})
        else:
            with reading_build_checkout(run_path(args.run).name):
                print(status_text(status_state(run_path(args.run))))
        return
    directory = ROOT / 'runs'
    names = [p.name for p in sorted(directory.iterdir()) if p.is_dir() and not p.name.startswith('.')] if directory.exists() else []
    if not getattr(args, 'json', False):
        print('\n'.join(names) if directory.exists() else 'No runs.')
        return
    runs = []
    for name in names:
        try:
            view = run_view(name)
            runs.append({'slug': name, 'run_id': view['run_id'], **view['meta'], 'verification_status': view['verification_status'],
                         'seed_contract': view['seed_contract'],
                         'next_action': view['next_action'], 'has_lock': view['has_lock'],
                         'checklist_summary': {'total': len(view['checklist']),
                                               'passed': sum(i['status'] == 'passed' for i in view['checklist'])}})
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError) as error:
            runs.append({'slug': name, 'verification_status': 'invalid', 'error': str(error),
                         'checklist_summary': {'total': 0, 'passed': 0}})
    machine_output({'runs': runs})


TOOL_ACCEPTABLE_CODES = {
    'tink': (0,),
}


def skills(args):
    allowed = TOOL_ACCEPTABLE_CODES.get(args.tool)
    if allowed is None:
        raise ValueError(f'Unknown skill tool: {args.tool!r}')
    # All cooperating worktrees on this host share a tink mutation lock. This is not
    # a security boundary and cannot coordinate tools invoked outside this wrapper.
    for relative in ['.agents', '.agents/skills', '.tink']:
        target = ROOT / relative
        if target.resolve() != target.absolute():
            raise ValueError('Skill state must not be symlinked across worktrees.')
    arguments = list(args.arguments)
    if arguments[:1] == ['--']:
        arguments.pop(0)
    if not arguments:
        raise ValueError('Supply the authorized Tink operation.')
    lock = tink_lock_path(ROOT)
    with locked(lock):
        result = subprocess.run([args.tool, *arguments], cwd=ROOT)
        if result.returncode not in allowed:
            raise ValueError(f'{args.tool} failed with exit code {result.returncode}; inspect partial state before retrying.')


# --- walk lint -------------------------------------------------------------
# Structural preconditions of the ICM "walk test". It checks structure only; it
# cannot prove that an agent with no memory can actually orient from the files.
ROUTER_BEGIN = '<!-- AI-Native SDLC Router -->'
ROUTER_END = '<!-- End AI-Native SDLC Router -->'
RULES_BEGIN = re.compile(r'<!--\s*tink:rules begin\b(.*?)-->')
RULES_END = re.compile(r'<!--\s*tink:rules end\s*-->')
ENTRY_LINE_LIMIT = 60
GENERATED_BYTE_LIMIT = 8192
TOKEN_WARN, TOKEN_FAIL = 8000, 16000
POINTER_PREFIXES = ('_system/', '_shared/', 'stages/', 'runs/', '.tink/', '.agents/')
SKILLSET_TOKEN = re.compile(r'(?<![A-Za-z0-9_-])[a-z][a-z0-9]*-skillset')
WALK_TIMEOUT = 30


def walk_result(cid, name, status, title, detail='', items=None):
    return {'id': cid, 'name': name, 'status': status, 'title': title, 'detail': detail, 'items': items or []}


def read_text_safe(path):
    try:
        return path.read_text(errors='replace')
    except OSError:
        return None


def rules_blocks(lines):
    """Return (blocks, problems). blocks: (begin_idx, end_idx, begin_line) for closed blocks."""
    blocks, problems, open_at = [], [], None
    for number, line in enumerate(lines):
        if RULES_BEGIN.search(line):
            if open_at is not None:
                problems.append(f'AGENTS.md:{number + 1}: tink:rules begin inside an unclosed block (began line {open_at + 1})')
            open_at = number
        elif RULES_END.search(line):
            if open_at is None:
                problems.append(f'AGENTS.md:{number + 1}: tink:rules end without begin')
            else:
                blocks.append((open_at, number, lines[open_at]))
                open_at = None
    if open_at is not None:
        problems.append(f'AGENTS.md:{open_at + 1}: tink:rules begin without end')
    if len(blocks) > 1:
        problems.append('AGENTS.md: duplicate tink:rules blocks at lines ' + ', '.join(str(b[0] + 1) for b in blocks))
    return blocks, problems


def walk_stage_dirs(root):
    stages_dir = root / 'stages'
    return sorted(p for p in stages_dir.iterdir() if p.is_dir() and (p / 'CONTEXT.md').is_file()) if stages_dir.is_dir() else []


def walk_w1(root, agents_text):
    name = 'entry-file'
    if agents_text is None:
        return walk_result('W1', name, 'fail', 'AGENTS.md is missing', 'The entry file must exist at the repo root.', ['AGENTS.md'])
    lines = agents_text.splitlines()
    problems = []
    begin = next((i for i, l in enumerate(lines) if ROUTER_BEGIN in l), None)
    end = next((i for i, l in enumerate(lines) if ROUTER_END in l), None)
    if begin is None or end is None or end < begin:
        problems.append(f'AGENTS.md: SDLC router markers missing or misordered: {ROUTER_BEGIN} ... {ROUTER_END}')
    blocks, block_problems = rules_blocks(lines)
    problems += block_problems
    generated = set()
    sizes = []
    for start, stop, _ in blocks:
        generated.update(range(start, stop + 1))
        size = sum(len(l.encode()) + 1 for l in lines[start + 1:stop])
        sizes.append(size)
        if size > GENERATED_BYTE_LIMIT:
            problems.append(f'AGENTS.md:{start + 1}: generated block is {size} bytes (limit {GENERATED_BYTE_LIMIT})')
    outside = len(lines) - len(generated)
    router_lines = end - begin + 1 if begin is not None and end is not None and end >= begin else outside
    if router_lines > ENTRY_LINE_LIMIT:
        problems.append(f'AGENTS.md: the SDLC router is {router_lines} lines (limit {ENTRY_LINE_LIMIT})')
    info = f'{outside} lines outside generated blocks; ' + (f'generated block {sizes[0]} bytes (cap {GENERATED_BYTE_LIMIT})' if sizes else 'no generated block')
    if problems:
        return walk_result('W1', name, 'fail', 'entry file breaks the router contract', info, problems)
    if outside > ENTRY_LINE_LIMIT:
        return walk_result('W1', name, 'warn', f'AGENTS.md carries {outside - router_lines} lines of project instructions beside the router',
                           info + f'; install keeps existing AGENTS.md text, but an entry file past {ENTRY_LINE_LIMIT} lines is slow to orient from. '
                           'Consider moving detail into docs/ and linking it.', [f'AGENTS.md: {outside} lines outside generated blocks (soft limit {ENTRY_LINE_LIMIT})'])
    return walk_result('W1', name, 'pass', f'AGENTS.md is a router ({info})', info)


def pointer_candidates(text, first_line=1):
    for offset, line in enumerate(text.splitlines()):
        for span in re.findall(r'`([^`\n]+)`', line):
            token = span.split()[0].rstrip('.,:;)')
            if token.startswith(POINTER_PREFIXES):
                yield first_line + offset, token


def pointer_resolves(root, token):
    if token.startswith('runs/'):
        return True
    if '<' not in token:
        return (root / token).exists()
    prefix = token.split('<', 1)[0]
    prefix_dir = prefix.rsplit('/', 1)[0] if '/' in prefix else ''
    if not (root / prefix_dir).is_dir():
        return False
    pattern = re.sub(r'<[^>]*>', '*', token)
    return any(root.glob(pattern))


def walk_w2(root, agents_text):
    name = 'pointers-resolve'
    sources = []
    if agents_text is not None:
        lines = agents_text.splitlines()
        begin = next((i for i, l in enumerate(lines) if ROUTER_BEGIN in l), None)
        end = next((i for i, l in enumerate(lines) if ROUTER_END in l), None)
        if begin is not None and end is not None and end > begin:
            sources.append(('AGENTS.md', '\n'.join(lines[begin:end + 1]), begin + 1))
    for stage in walk_stage_dirs(root):
        text = read_text_safe(stage / 'CONTEXT.md')
        if text is not None:
            sources.append((f'stages/{stage.name}/CONTEXT.md', text, 1))
    bad, count = [], 0
    for label, text, first in sources:
        for line, token in pointer_candidates(text, first):
            count += 1
            if not pointer_resolves(root, token):
                bad.append(f'{label}:{line}: `{token}` does not resolve')
    if bad:
        return walk_result('W2', name, 'fail', f'{len(bad)} dead pointer(s)', 'Backticked repo paths must exist.', bad)
    return walk_result('W2', name, 'pass', f'{count} pointers resolve', '')


def walk_w3(root):
    name = 'stage-contracts'
    stages_found = walk_stage_dirs(root)
    if not stages_found:
        return walk_result('W3', name, 'fail', 'no stage contracts found', 'Expected stages/<stage-name>/CONTEXT.md.', ['stages/'])
    missing = []
    for stage in stages_found:
        text = read_text_safe(stage / 'CONTEXT.md') or ''
        checks = [('Inputs', r'^\s*inputs?\b\s*:'), ('Output', r'^\s*outputs?\b\s*:'), ('Gate', r'\bgate\s*:|\bhuman\b')]
        for label, pattern in checks:
            if not re.search(pattern, text, re.I | re.M):
                missing.append(f'stages/{stage.name}/CONTEXT.md: missing {label} statement')
    if missing:
        return walk_result('W3', name, 'fail', f'{len(missing)} contract statement(s) missing',
                           'Each stage names Inputs:, Output:, and a human Gate:.', missing)
    return walk_result('W3', name, 'pass', f'{len(stages_found)} stage contracts name inputs, output, and gate', '')


def walk_w4(root, agents_text):
    name = 'token-budget'
    agents_chars = len(agents_text) if agents_text is not None else 0
    tokens = {}
    for stage in walk_stage_dirs(root):
        text = read_text_safe(stage / 'CONTEXT.md') or ''
        total = agents_chars + len(text)
        for shared in sorted(set(re.findall(r'_shared/[A-Za-z0-9_.-]+', text))):
            shared_text = read_text_safe(root / shared) if (root / shared).is_file() else None
            if shared_text is not None:
                total += len(shared_text)
        tokens[stage.name] = total // 4
    over_fail = [f'{s}: ~{t} tokens (limit {TOKEN_FAIL})' for s, t in tokens.items() if t > TOKEN_FAIL]
    over_warn = [f'{s}: ~{t} tokens (warn above {TOKEN_WARN})' for s, t in tokens.items() if TOKEN_WARN < t <= TOKEN_FAIL]
    detail = 'Estimate: (AGENTS.md + CONTEXT.md + referenced _shared files) chars / 4.'
    if over_fail:
        return walk_result('W4', name, 'fail', f'{len(over_fail)} stage(s) over {TOKEN_FAIL} tokens', detail, over_fail + over_warn), tokens
    if over_warn:
        return walk_result('W4', name, 'warn', f'{len(over_warn)} stage(s) over {TOKEN_WARN} tokens', detail, over_warn), tokens
    peak = max(tokens.values(), default=0)
    return walk_result('W4', name, 'pass', f'largest stage context ~{peak} tokens', detail), tokens


def walk_w5(root):
    name = 'skillsets-consistent'
    pins_dir = root / '.tink/skillsets'
    if not pins_dir.is_dir():
        return walk_result('W5', name, 'warn', 'no stage skillset pins installed', 'Expected pins in .tink/skillsets/.')
    pins = {p.stem for p in pins_dir.glob('*.json')}
    sources = [(f'stages/{s.name}/CONTEXT.md', s / 'CONTEXT.md') for s in walk_stage_dirs(root)]
    sources.append(('_system/SDLC.md', root / '_system/SDLC.md'))
    missing, named = [], set()
    for label, path in sources:
        text = read_text_safe(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for token in SKILLSET_TOKEN.findall(line):
                if label.startswith('stages/'):
                    named.add(token)
                if token not in pins:
                    missing.append(f'{label}:{number}: {token} has no .tink/skillsets/{token}.json')
    if missing:
        return walk_result('W5', name, 'fail', f'{len(missing)} skillset name(s) without a pin', 'Every named skillset needs a committed pin.', missing)
    unreferenced = sorted(pins - named)
    if unreferenced:
        return walk_result('W5', name, 'warn', f'{len(unreferenced)} pin(s) no stage names', 'Pins should be named by a stage CONTEXT.md.',
                           [f'.tink/skillsets/{p}.json' for p in unreferenced])
    return walk_result('W5', name, 'pass', f'{len(pins)} pins, all named and present', '')


def tail_lines(text, count=3):
    return [l for l in text.splitlines() if l.strip()][-count:]


def walk_w6(root):
    name = 'status-derivable'
    script = root / '_system/scripts/sdlc.py'
    runs_dir = root / 'runs'
    slugs = sorted(p.name for p in runs_dir.iterdir() if p.is_dir() and not p.name.startswith('.')) if runs_dir.is_dir() else []
    if not slugs:
        return walk_result('W6', name, 'pass', 'sdlc.py is present', 'no runs')
    bad = []
    for slug in slugs:
        try:
            result = subprocess.run([sys.executable, str(script), 'status', slug], cwd=root, capture_output=True, text=True, timeout=WALK_TIMEOUT)
        except subprocess.TimeoutExpired:
            bad.append(f'runs/{slug}: status timed out after {WALK_TIMEOUT}s')
            continue
        except OSError as error:
            bad.append(f'runs/{slug}: cannot run status: {error}')
            continue
        if result.returncode != 0:
            bad.append(f'runs/{slug}: status exit {result.returncode}: ' + ' | '.join(tail_lines(result.stderr + '\n' + result.stdout)))
    if bad:
        return walk_result('W6', name, 'fail', f'{len(bad)} run(s) whose status cannot be derived', 'python3 _system/scripts/sdlc.py status <slug> must exit 0.', bad)
    return walk_result('W6', name, 'pass', f'status derivable for {len(slugs)} run(s)', '')


def walk_w7(root, agents_text):
    name = 'rules-block-current'
    blocks, problems = rules_blocks(agents_text.splitlines()) if agents_text is not None else ([], [])
    if not blocks or problems:
        return walk_result('W7', name, 'pass', 'skipped (no well-formed tink:rules block)', '')
    tink = shutil.which('tink')
    if tink is None:
        return walk_result('W7', name, 'pass', 'skipped (tink not on PATH)', '')
    match = re.search(r'skillset=(\S+)', RULES_BEGIN.search(blocks[0][2]).group(1))
    if not match:
        return walk_result('W7', name, 'warn', 'rules block names no skillset', 'Expected skillset=<name> in the begin marker.', [f'AGENTS.md:{blocks[0][0] + 1}'])
    skillset = match.group(1)
    try:
        result = subprocess.run([tink, 'use', skillset, '--check'], cwd=root, capture_output=True, text=True, timeout=WALK_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as error:
        return walk_result('W7', name, 'warn', 'tink use --check did not complete', str(error))
    message = ' | '.join(tail_lines(result.stdout + '\n' + result.stderr)) or f'exit {result.returncode}'
    if result.returncode == 0:
        return walk_result('W7', name, 'pass', f'rules block current for {skillset}', '')
    if result.returncode == 1:
        return walk_result('W7', name, 'fail', f'rules block is stale for {skillset}', message, [f'AGENTS.md:{blocks[0][0] + 1}'])
    return walk_result('W7', name, 'warn', f'tink use --check exited {result.returncode}', message)





def walk(args):
    agents_text = read_text_safe(ROOT / 'AGENTS.md')
    w4, tokens = walk_w4(ROOT, agents_text)
    checks = [walk_w1(ROOT, agents_text), walk_w2(ROOT, agents_text), walk_w3(ROOT), w4,
              walk_w5(ROOT), walk_w6(ROOT), walk_w7(ROOT, agents_text)]
    summary = {key: sum(1 for c in checks if c['status'] == key) for key in ('pass', 'warn', 'fail')}
    if args.json:
        print(json.dumps({'schema': 1, 'checks': checks, 'tokens': tokens, 'summary': summary}, indent=2))
    else:
        for c in checks:
            print(f"{c['status'].upper()} {c['id']} {c['name']}: {c['title']}")
            if c['status'] != 'pass':
                if c['detail']:
                    print(f"  {c['detail']}")
                for item in c['items']:
                    print(f'  - {item}')
        for stage, count in tokens.items():
            print(f'  tokens {stage}: ~{count}')
        print(f"Walk: {summary['pass']} passed, {summary['warn']} warned, {summary['fail']} failed")
    if summary['fail']:
        raise SystemExit(1)


# --- stage launcher ----------------------------------------------------------
# Run by the human (or a script), never by the agent: commit the approved artifacts,
# create the checkout, compile the stage rules there, and print the launch prompt.
STAGE_DIRS = {1: '01-plan', 2: '02-design', 3: '03-build', 4: '04-test', 5: '05-deploy', 6: '06-maintain'}
STAGE_WORDS = {1: 'plan', 2: 'design', 3: 'build', 4: 'test', 5: 'deploy', 6: 'maintain'}
TINK_TIMEOUT = 60


def run_git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True)


def git_line(result):
    lines = [line for line in (result.stderr + result.stdout).splitlines() if line.strip()]
    return lines[0] if lines else f'git exited {result.returncode}'


def stage_skillset(n):
    """Skillset named by the stage contract's Skills section, or None."""
    text = read_text_safe(ROOT / 'stages' / STAGE_DIRS[n] / 'CONTEXT.md')
    match = re.search(r'^## Skills\b(.*?)(?=^## |\Z)', text or '', re.M | re.S)
    named = re.search(r'Skillset:\s*`([a-z0-9]+-skillset)`', match.group(1)) if match else None
    return named.group(1) if named else None


def stage_entry_gates(path, run, n):
    profile = require_run(path)['profile']
    if n == 2 and profile == 'light':
        raise ValueError('light runs define in stage 1; the single definition gate is recorded as stage 3')
    needed = {1: [], 2: [1], 3: [3] if profile == 'light' else [1, 2]}.get(n) if n <= 3 else stages(path)
    for stage in needed:
        state = gate(path, stage)
        if state != 'approved':
            raise ValueError(f'cannot open stage {n}: the stage {stage} approval is {state}; record it with: '
                             f'sdlc.py decide {run} {stage} approved --reviewer NAME --source REF --reason TEXT')
    if n == 5:
        record_path, _ = verification_files(path)
        current = False
        try:
            current = verification_matches(path, read_json(record_path))
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            pass
        if not current:
            raise ValueError('run verify first: stage 5 reviews current evidence '
                             f'(python3 _system/scripts/sdlc.py verify {run})')


def stage_identity_ok():
    for who in ('COMMITTER', 'AUTHOR'):
        name = os.environ.get(f'GIT_{who}_NAME') or run_git(ROOT, 'config', 'user.name').stdout.strip()
        email = os.environ.get(f'GIT_{who}_EMAIL') or run_git(ROOT, 'config', 'user.email').stdout.strip()
        if not (name and email):
            return False
    return True


def require_tink_use(tink):
    """Refuse early, with the fix, when the installed tink predates `tink use` (which compiles the stage disciplines)."""
    try:
        probe = subprocess.run([tink, 'use', '--help'], capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return
    if probe.returncode == 0:
        return
    try:
        version = subprocess.run([tink, '--version'], capture_output=True, text=True, timeout=15).stdout.strip() or 'the installed tink'
    except (subprocess.TimeoutExpired, OSError):
        version = 'the installed tink'
    raise ValueError(f'{version} has no `tink use` (it compiles the stage disciplines); upgrade tink, then re-run the same command')


def stage_seed_contract(value, tinkery):
    """Use Tinkery's verifier before writes; the supplied executable is trusted code."""
    try:
        resolved = Path(value).resolve()
        exists = resolved.is_file()
    except (OSError, RuntimeError, ValueError):
        raise ValueError('seed contract path is unavailable; supply an existing regular file.') from None
    if not exists:
        raise ValueError(f'seed contract must be an existing regular file: {value}')
    if not tinkery:
        raise ValueError('--seed-contract requires --tinkery PATH to a trusted Tinkery executable.')
    if resolved.name != 'seed-contract.md':
        raise ValueError('Tinkery session input must be named seed-contract.md.')
    try:
        tinkery = Path(tinkery).resolve()
        available = tinkery.is_file() and os.access(tinkery, os.X_OK)
    except (OSError, RuntimeError, ValueError):
        raise ValueError('Tinkery executable is unavailable; supply --tinkery PATH.') from None
    if not available:
        raise ValueError(f'Tinkery executable is unavailable: {tinkery}; supply --tinkery PATH.')
    directory = resolved.parent

    def invalid_constant(value):
        raise ValueError(f'Invalid JSON constant: {value}')

    def call(*arguments):
        try:
            result = subprocess.run([str(tinkery), 'seed', *arguments, str(directory)],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise ValueError('Tinkery could not run the seed verifier; check --tinkery PATH and retry.') from None
        if result.returncode:
            reason = result.stderr.strip()
            detail = f': {reason}; ' if reason else '; '
            raise ValueError(f'Tinkery refused seed intake{detail}check the session in Tinkery and retry.')
        try:
            return json.loads(result.stdout, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise ValueError('Tinkery returned malformed seed output; check --tinkery PATH.') from None

    def status():
        value = call('status', '--json')
        if (not isinstance(value, dict) or not isinstance(value.get('handoff'), dict)
                or type(value['handoff'].get('verified')) is not bool
                or (value['handoff']['verified'] and
                    (not isinstance(value.get('seed'), dict)
                     or not isinstance(value['seed'].get('sha256'), str)
                     or not re.fullmatch(r'[0-9a-f]{64}', value['seed']['sha256'])))):
            raise ValueError('Tinkery returned malformed seed status; check --tinkery PATH.')
        return value

    def read_confirmation():
        try:
            value = read_json(directory / 'seed-confirmation.json')
        except (OSError, ValueError, RecursionError):
            raise ValueError('Tinkery confirmation is missing or unreadable; confirm the seed and retry.') from None
        if not isinstance(value, dict):
            raise ValueError('Tinkery confirmation is malformed; confirm the seed and retry.')
        return value

    def read_seed():
        try:
            return resolved.read_bytes()
        except OSError:
            raise ValueError('Tinkery seed could not be read; confirm the seed and retry.') from None

    before = status()
    if before['handoff']['verified'] is not True:
        raise ValueError('Tinkery handoff is not verified; confirm the current seed in Tinkery and retry.')
    confirmation = read_confirmation()
    if type(confirmation.get('schema_version')) is int and confirmation['schema_version'] == 3:
        actor = confirmation.get('actor')
        # Tinkery launched with `--operator agent` declares the agent at the terminal;
        # older receipts name the agent, its operator, a source and a two-way/small class.
        declared = {'by': 'terminal', 'declaration': 'declared: agent',
                    'action': 'typed confirm at this terminal',
                    'identity': 'declaration is not verified identity'}
        fields = {'by', 'agent', 'authorizing_operator', 'source', 'class'}
        classification = actor.get('class') if isinstance(actor, dict) else None
        class_fields = {'door', 'door_reason', 'blast_radius', 'blast_radius_reason'}
        named = (isinstance(actor, dict) and set(actor) == fields and actor.get('by') == 'agent'
                 and all(isinstance(actor.get(key), str) and actor[key].strip()
                         for key in ('agent', 'authorizing_operator', 'source'))
                 and isinstance(classification, dict) and set(classification) == class_fields
                 and classification.get('door') == 'two_way' and classification.get('blast_radius') == 'small'
                 and all(isinstance(classification.get(key), str) and classification[key].strip()
                         for key in ('door_reason', 'blast_radius_reason')))
        if confirmation.get('operator') != 'agent' or not (actor == declared or named):
            raise ValueError('Tinkery agent confirmation needs agent attribution: a terminal '
                             '`declared: agent` or a complete named agent with its operator, source '
                             'and two-way/small class; confirm the seed in Tinkery and retry.')
    data = read_seed()
    seed_sha256 = digest(data)
    records = call('checks')
    if (not isinstance(records, list) or not records
            or any(not isinstance(record, dict) or not isinstance(record.get('statement'), str)
                   for record in records)):
        raise ValueError('Tinkery returned malformed seed checks; check --tinkery PATH.')
    after = status()
    if (before != after or confirmation != read_confirmation()
            or data != read_seed() or type(confirmation.get('schema_version')) is not int
            or confirmation['schema_version'] not in (2, 3)
            or confirmation.get('seed_sha256') != seed_sha256
            or before['seed']['sha256'] != seed_sha256
            or confirmation.get('checks_sha256') != records_digest(records)):
        raise ValueError('Tinkery confirmation binding changed during intake; confirm and retry.')
    confirmer = 'agent' if confirmation['schema_version'] == 3 else 'operator'
    return resolved, data, record_definitions(records, confirmer), confirmation


def stage_dirty(run):
    result = run_git(ROOT, 'status', '--porcelain', '--untracked-files=all')
    paths = [line[3:].split(' -> ')[-1].strip('"') for line in result.stdout.splitlines() if line[3:]]
    return [p for p in paths if not p.startswith(f'runs/{run}/') and p not in (f'runs/{run}', f'runs/.{run}.lock')]


def tracked_mode_mismatches():
    """Tracked executable-bit changes Git may hide when core.fileMode is false."""
    result = run_git(ROOT, 'ls-files', '--stage', '-z')
    if result.returncode:
        return []
    dirty = []
    for entry in result.stdout.split('\0'):
        metadata, separator, relative = entry.partition('\t')
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[2] != '0' or fields[0] not in ('100644', '100755'):
            continue
        if relative.startswith('runs/'):
            continue
        path = ROOT / relative
        try:
            actual_executable = bool(path.lstat().st_mode & 0o111)
        except OSError:
            continue  # Git's normal status check reports missing or inaccessible paths.
        if actual_executable != (fields[0] == '100755'):
            dirty.append(relative)
    return dirty


def prune_empty_parents(target, stop):
    """Remove empty directories git created above `target`, up to but never including `stop` (the deepest one that existed)."""
    parent = Path(target).parent
    while parent != stop and stop in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def candidate_dirty(run):
    """Uncommitted paths that are part of the verified candidate (what verify fingerprints), not disposable caches or generated rules."""
    dirty = []
    paths = stage_dirty(run)
    paths.extend(path for path in tracked_mode_mismatches() if path not in paths)
    for relative in paths:
        # Run evidence is committed by the launcher when needed and is excluded from
        # the verified candidate fingerprint; another run's working files are unrelated.
        if relative.startswith('runs/'):
            continue
        tracked = run_git(ROOT, 'ls-files', '--error-unmatch', '--', relative).returncode == 0
        if not tracked and disposable(relative):
            continue
        if relative == 'AGENTS.md' and tracked:
            head = run_git(ROOT, 'show', 'HEAD:AGENTS.md')
            if head.returncode == 0 and strip_generated_rules((ROOT / relative).read_bytes()) == strip_generated_rules(head.stdout.encode()):
                continue
        dirty.append(relative)
    return dirty


MIN_TINK_ROUTE = (0, 10, 0)


def tink_route_warning():
    """One actionable warning when the installed version is old or cannot be confirmed."""
    route = shutil.which('tink-route')
    if not route:
        return None
    try:
        result = subprocess.run([route, '--version'], capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = result.stdout.strip() or result.stderr.strip()
    match = re.search(
        r'(?<![A-Za-z0-9])v?(\d+)\.(\d+)(?:\.(\d+))?'
        r'([.-]?(?:rc|a|b|dev|alpha|beta|pre|preview)\.?[0-9A-Za-z.-]*)?'
        r'(?:\+[0-9A-Za-z.-]+)?(?![A-Za-z0-9.+-])', out, re.I)
    upgrade = 'upgrade: pipx install --force git+https://github.com/jon-devlapaz/tink-route.git'
    if not match:
        detail = out[:120] if out else f'exit code {result.returncode} with no version output'
        return ('warning: could not determine whether tink-route supports whole-library routing '
                f'(version output: {detail}); {upgrade}')
    version = match.group(0)
    parts = tuple(int(match.group(index) or 0) for index in (1, 2, 3))
    prerelease = bool(match.group(4))
    if parts >= MIN_TINK_ROUTE and not prerelease:
        return None
    reason = (f'is a prerelease of {".".join(map(str, parts))}' if prerelease and parts >= MIN_TINK_ROUTE
              else f'is older than {".".join(map(str, MIN_TINK_ROUTE))}')
    return (f'warning: tink-route {version} {reason} and may not support whole-library routing; '
            f'upgrade: pipx install --force git+https://github.com/jon-devlapaz/tink-route.git')


DELIVERY_TIMEOUT = 5
GITHUB_REMOTE = re.compile(r'^(?:[a-z+]+://)?(?:[^@/]+@)?github\.com[:/]', re.I)


def delivery_warning(run):
    """One warning when a PR cannot be delivered from this checkout; never prints remote URLs or command output."""
    remote = run_git(ROOT, 'remote', 'get-url', 'origin')
    url = remote.stdout.strip()
    if remote.returncode or not url:
        missing = 'there is no `origin` remote'
    elif not GITHUB_REMOTE.match(url):
        return None
    elif not shutil.which('gh'):
        missing = '`origin` is on GitHub but `gh` is not installed'
    else:
        try:
            auth = subprocess.run(['gh', 'auth', 'status'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=DELIVERY_TIMEOUT)
        except subprocess.TimeoutExpired:
            missing = f'could not confirm `gh` is signed in (`gh auth status` did not finish in {DELIVERY_TIMEOUT}s)'
        except OSError:
            missing = '`origin` is on GitHub but `gh` could not be run'
        else:
            if auth.returncode == 0:
                return None
            missing = f'`gh` is not signed in (`gh auth status` exited {auth.returncode})'
    return (f'warning: PR delivery may not be possible from this checkout: {missing}. If this is still true at the end, '
            f'hand the owner these commands instead of stopping silently: git push -u origin {run}; gh pr create --fill')


def stage(args):
    run, n = args.run, args.n
    path = run_path(run)
    require_run(path)
    if n == 4:
        raise ValueError('stage 4 runs inside the stage-3 session (see stages/04-test/CONTEXT.md)')
    if n not in STAGE_DIRS:
        raise ValueError('stage must be one of 1, 2, 3, 5, 6')
    if args.worktree and args.here:
        raise ValueError('--worktree and --here are mutually exclusive')  # parser already rejects; defensive
    if args.seed_contract and n != 1:
        raise ValueError('--seed-contract applies to stage 1 only')
    top = run_git(ROOT, 'rev-parse', '--show-toplevel')
    if top.returncode or Path(top.stdout.strip()).resolve() != ROOT:
        raise ValueError('the scaffold must sit at the root of a git repository')
    stage_entry_gates(path, run, n)
    if n == 5:
        dirty = candidate_dirty(run)
        if dirty:
            mode_dirty = sorted(set(dirty) & set(tracked_mode_mismatches()))
            mode_note = ('; tracked executable mode is not represented in the Git index: ' + ', '.join(mode_dirty[:5])
                         if mode_dirty else '')
            raise ValueError('stage 5 reviews a commit, and these candidate changes are uncommitted: ' + ', '.join(dirty[:5])
                             + (f' (+{len(dirty) - 5} more)' if len(dirty) > 5 else '')
                             + mode_note + '; commit the candidate changes (verify again only if the code changed since it passed)')
    if args.tinkery and not args.seed_contract:
        raise ValueError('--tinkery requires --seed-contract at stage 1.')
    seed_source, seed_bytes, seed_checks, confirmation = (
        stage_seed_contract(args.seed_contract, args.tinkery) if args.seed_contract else (None, None, None, None))
    seed_contract = f'runs/{run}/seed-contract.md' if seed_source else None
    skillset = stage_skillset(n)
    tink = shutil.which('tink') if skillset else None
    if tink:
        require_tink_use(tink)
    stage_dir = STAGE_DIRS[n]

    make_worktree = not args.here and (args.worktree or n in (3, 5))
    target = ROOT
    first_existing = ROOT
    detached = n != 3
    if make_worktree:
        target = Path(os.path.abspath(args.worktree)) if args.worktree else (
            Path.home() / 'dev/worktrees' / ROOT.name / (f'review-{run}' if n == 5 else run))
        first_existing = target.parent
        while not first_existing.exists():
            first_existing = first_existing.parent
        if (target.exists() or target.is_symlink()) and n == 5 and not args.worktree:
            # A repeat review: name both ways forward and choose neither; the old checkout may hold findings.
            number = 2
            while Path(f'{target}-{number}').exists() or Path(f'{target}-{number}').is_symlink():
                number += 1
            fresh = Path(f'{target}-{number}')
            raise ValueError(f'review checkout already exists: {target}. Once its findings are on this branch, '
                             f'remove it: git worktree remove {target}; or review in a fresh checkout: '
                             f'python3 _system/scripts/sdlc.py stage {run} 5 --worktree {fresh}')
        if target.exists() or target.is_symlink():
            raise ValueError(f'worktree path already exists: {target} (remove it with: git worktree remove {target})')
        if not detached and run_git(ROOT, 'show-ref', '--verify', '--quiet', f'refs/heads/{run}').returncode == 0:
            raise ValueError(f"branch '{run}' already exists (remove it with: git branch -D {run}, after git worktree remove on any checkout using it)")
    if seed_contract and not args.check:
        with locked(path / '.writer-lock'):
            copy = ROOT / seed_contract
            write_json(path / 'seed-checks.json', seed_checks)
            previous = read_json(path / 'run.json').get('seed_contract', {})
            if seed_source != copy.resolve():
                copy.write_bytes(seed_bytes)
            source = previous.get('source', str(seed_source)) if seed_source == copy.resolve() else str(seed_source)
            write_json(path / 'run.json', {**read_json(path / 'run.json'),
                                            'seed_contract': {'path': seed_contract, 'sha256': digest(seed_bytes), 'source': source,
                                                              'definitions_sha256': digest(encoded(seed_checks['items'])),
                                                              'confirmation': confirmation,
                                                              'definitions': seed_checks['items']}})
            record_seed_match(path)
    pending = make_worktree and bool(run_git(ROOT, 'status', '--porcelain', '--', f'runs/{run}').stdout.strip())
    if pending and not stage_identity_ok():
        raise ValueError('no committer identity configured; set user.name and user.email (or GIT_COMMITTER_*) so the launcher can commit '
                         f'runs/{run}')
    notice = None if skillset else f'skills: skipped (stages/{stage_dir}/CONTEXT.md names no skillset)'
    if skillset and not tink:
        notice = 'skills: skipped (tink not installed); the agent will run without stage disciplines'
    snapshot_arg = f'runs/{run}/{stage_dir}'
    prompt = f'Begin stage {n} ({STAGE_WORDS[n]}) of SDLC run `{run}`.'
    if seed_contract:
        confirmer = 'agent' if confirmation['schema_version'] == 3 else 'operator'
        prompt += f' The {confirmer}-confirmed seed contract is at {seed_contract}; it is input, not authorization.'
    warn = stage_dirty(run) if make_worktree else []
    warning = ('warning: not carried into the new checkout: ' + ', '.join(warn[:5]) + (f' (+{len(warn) - 5} more)' if len(warn) > 5 else '')
               if warn else None)

    if args.check:
        if seed_contract and seed_source != (ROOT / seed_contract).resolve():
            print(f'Would copy {seed_source} to {seed_contract}')
            print(f'Would import locked {confirmer}-confirmed acceptance checks')
        print(f'Would commit: {"yes" if pending else "no"}')
        how = f'branch {run}' if not detached else 'detached'
        print(f'Would create worktree: {target} ({how})' if make_worktree else 'Would create worktree: none')
    else:
        committed = None
        if pending:
            message = f'stage {n} open: {run} (approved artifacts and receipts)'
            added = run_git(ROOT, 'add', '-A', '--', f'runs/{run}')
            if added.returncode:
                raise ValueError(f'git add failed: {git_line(added)}')
            done = run_git(ROOT, 'commit', '-q', '-m', message, '--', f'runs/{run}')
            if done.returncode:
                raise ValueError(f'git commit failed: {git_line(done)}')
            committed = (run_git(ROOT, 'rev-parse', '--short', 'HEAD').stdout.strip(), message)
        if make_worktree:
            command = ['worktree', 'add', '--detach', str(target), 'HEAD'] if detached else ['worktree', 'add', '-b', run, str(target), 'HEAD']
            created = run_git(ROOT, *command)
            if created.returncode:
                if not detached:
                    run_git(ROOT, 'branch', '-D', run)
                prune_empty_parents(target, first_existing)
                raise ValueError(f'worktree creation failed: {git_line(created)}'
                                 + (f'\nalready committed {committed[0]}: {committed[1]}' if committed else ''))
    if args.check:
        print(f'Would run: tink use {skillset} --snapshot {snapshot_arg}' if tink else notice)
    elif tink:
        try:
            used = subprocess.run([tink, 'use', skillset, '--snapshot', snapshot_arg], cwd=target, capture_output=True,
                                  text=True, timeout=TINK_TIMEOUT)
            failure = None if used.returncode == 0 else next((l for l in used.stderr.splitlines() if l.strip()), f'tink use exited {used.returncode}')
        except (subprocess.TimeoutExpired, OSError) as error:
            failure = f'tink use did not complete: {error}'
        if failure:
            print(failure, file=sys.stderr)
            print('stage not opened: fix the skillset problem above', file=sys.stderr)
            if make_worktree:
                removed = run_git(ROOT, 'worktree', 'remove', '--force', str(target))
                if removed.returncode == 0 and not detached:
                    run_git(ROOT, 'branch', '-D', run)
                if removed.returncode == 0:
                    prune_empty_parents(target, first_existing)
                print(f'worktree removed; re-run the same command after fixing it' if removed.returncode == 0
                      else f'worktree left at {target}: {git_line(removed)}', file=sys.stderr)
            raise SystemExit(1)
        print(f'skills: compiled {skillset} into {target}')
    else:
        print(notice)
    if warning:
        print(warning)
    if n in (3, 5) and (undeliverable := delivery_warning(run)):
        print(undeliverable)
    if not args.check and (stale_route := tink_route_warning()):
        print(stale_route)
    if args.check and make_worktree:
        print('Checkout: none yet (--check creates nothing; the real run creates the worktree above)')
        print('Launch prompt (start a NEW session in that checkout):')
    else:
        print(f'Checkout: {target}')
        print('Launch prompt (start a NEW session there):')
    print(prompt)


# --- explicit build-step execution ------------------------------------------
# Adapters are trusted executable code. Their resolve operation must be offline;
# launch/resume must honor the resolved pin before making any provider request.
def step_absolute(value, label):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f'{label} must be an explicit absolute path.')
    return Path(value).resolve()


def step_model(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._:/-]*', value):
        raise ValueError('Step model must be an exact provider/model ID, not an alias, wildcard or default.')
    return value


def step_paths(record):
    report = Path(record['report'])
    launcher = Path(record['launcher'])
    for path in (report, launcher):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError(f'Step paths must remain canonical and without symlink substitutions: {path}')
    if not report.parent.is_dir():
        raise ValueError('Step report parent directory is missing.')
    if not launcher.is_file() or not os.access(launcher, os.X_OK) or digest(launcher.read_bytes()) != record['launcher_sha256']:
        raise ValueError('Step launcher changed or is unavailable; inspect it and prepare a new step, never fall back.')
    return report, launcher


def step_adapter(record, operation, contract, resolve=False):
    argv = [record['launcher'], 'resolve' if resolve else operation,
            '--model', record['model'], '--report', record['report'], '--contract', str(contract)]
    if resolve:
        argv += ['--for-operation', operation]
    if operation in ('resume', 'finished'):
        argv += ['--session', record['session']]
    offline = resolve or operation == 'finished'
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=15 if offline else 60)
    if result.returncode:
        reason = next((line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()), '')[:300]
        raise ValueError(f'Step adapter {argv[1]} failed (exit {result.returncode}); inspect the adapter/session before retrying.'
                         + (f' Adapter said: {reason}' if reason else ''))
    reply = json.loads(result.stdout)
    if (not isinstance(reply, dict) or reply.get('protocol') != 'tink-sdlc-launcher'
            or type(reply.get('api_version')) is not int or reply['api_version'] != 1
            or reply.get('operation') != operation or reply.get('model') != record['model']):
        raise ValueError('Step adapter cannot resolve the exact model pin or is incompatible; no default is accepted.')
    if operation == 'resume' and reply.get('session') != record['session']:
        raise ValueError('Step adapter cannot resume the pinned session.')
    if operation == 'finished' and (reply.get('session') != record['session'] or reply.get('finished') is not True):
        raise ValueError('The agent has not finished its latest turn on the pin (paused, stopped, failed or still '
                         'running); resume the step and complete it after the agent ends its turn.')
    if not resolve and (not isinstance(reply.get('session'), str) or not reply['session'].strip()):
        raise ValueError('Step adapter returned no session identity.')
    return reply


def step(args):
    if os.environ.get('TINK_SDLC_STEP_SESSION') == '1' and args.action in ('prepare', 'launch', 'resume', 'complete'):
        raise ValueError('Cannot operate a pinned build step from inside its agent session.')
    path = run_path(args.run)
    meta = require_run(path)
    if not ITEM_ID.fullmatch(args.name):
        raise ValueError('Step name must be 1–40 lowercase letters, digits or hyphens.')
    contract = path / 'steps' / f'{args.name}.json'
    if contract.resolve() != contract.absolute():
        raise ValueError('Step contract paths must not contain symlinks.')
    supplied = (args.report, args.model, args.launcher)
    if args.action != 'prepare' and any(value is not None for value in supplied):
        raise ValueError('Step report, model and launcher are fixed at prepare; overrides are refused.')
    if args.action == 'show':
        print(json.dumps(read_json(contract), indent=2))
        return
    with locked(path / '.writer-lock'):
        if args.action == 'prepare':
            if contract.exists():
                raise ValueError('Step already exists; inspect it instead of replacing its contract.')
            report = step_absolute(args.report, 'Step report')
            launcher = step_absolute(args.launcher, 'Step launcher')
            model = step_model(args.model)
            if report.exists():
                raise ValueError('Step report already exists; choose a fresh destination, never reuse old evidence.')
            record = {'schema': 1, 'run_id': meta['run_id'], 'checkout': str(ROOT),
                      'report': str(report), 'model': model, 'launcher': str(launcher),
                      'launcher_sha256': digest(launcher.read_bytes()), 'state': 'prepared', 'session': None}
            step_paths(record)
            write_json(contract, record)
        else:
            record = read_json(contract)
            if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1
                    or record.get('run_id') != meta['run_id'] or record.get('checkout') != str(ROOT)):
                raise ValueError('Step contract is invalid or belongs to another run/checkout; prepare a new step here.')
            step_model(record['model'])
            if record.get('state') not in ('prepared', 'active', 'uncertain', 'completed'):
                raise ValueError('Step contract has an invalid state.')
            session = record.get('session')
            if ((session is not None and (not isinstance(session, str) or not session.strip()))
                    or (record['state'] in ('active', 'completed') and session is None)
                    or (record['state'] == 'prepared' and session is not None)):
                raise ValueError('Step contract has no valid session for its state.')
            report, _ = step_paths(record)
            if args.action == 'complete':
                if record['state'] != 'active':
                    raise ValueError('Only an active step with a known session can be completed.')
                if not report.is_file():
                    raise ValueError(f'Step report is missing at {report}; completion refused, with no project-root fallback.')
                contents = report.read_bytes()
                if not contents.strip():
                    raise ValueError('Step report is empty; completion refused.')
                # A report alone can be left by an interrupted run; the adapter must confirm the agent finished.
                step_adapter(record, 'finished', contract)
                record.update(state='completed', report_sha256=digest(contents))
                write_json(contract, record)
            else:
                expected = 'prepared' if args.action == 'launch' else 'active'
                if record['state'] != expected or (args.action == 'resume' and not record.get('session')):
                    raise ValueError(f'Cannot {args.action} step in state {record["state"]}; inspect the recorded session first.')
                for n in stages(path):
                    if gate(path, n) != 'approved':
                        raise ValueError(f'Step execution requires current stage {n} definition approval.')
                # Resolve afresh on every launch/resume, before admitting a provider-capable operation.
                step_adapter(record, args.action, contract, resolve=True)
                step_paths(record)
                # A crash or adapter failure must not invite a duplicate session or automatic retry.
                record['state'] = 'uncertain'
                write_json(contract, record)
                reply = step_adapter(record, args.action, contract)
                record.update(state='active', session=reply['session'])
                write_json(contract, record)
        print(json.dumps(record, indent=2))


class ShortErrorParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, f'sdlc.py: error: {message}\n'
                     'Commands: new, status, decide, dry-run, verify, mark, lock-tests, skills, stage, step, walk. See _system/SDLC.md.\n')


def main():
    parser = ShortErrorParser(description=__doc__)
    if any(arg == '--seed-me-helper' or arg.startswith('--seed-me-helper=') for arg in sys.argv[1:]):
        parser.error('--seed-me-helper was removed; use --tinkery PATH.')
    commands = parser.add_subparsers(dest='command', required=True)
    new = commands.add_parser('new')
    new.add_argument('run')
    new.add_argument('--profile', choices=['light', 'full'], default='light')
    new.add_argument('--kind', choices=['feature', 'bug'], default='feature')
    state = commands.add_parser('status')
    state.add_argument('run', nargs='?')
    state.add_argument('--json', action='store_true', help='read-only versioned machine interface')
    protocol = commands.add_parser('capabilities', help='report the supported machine interface')
    protocol.add_argument('--json', action='store_true')
    decision = commands.add_parser('decide')
    decision.add_argument('run')
    decision.add_argument('stage', type=int, choices=[1, 2, 3])
    decision.add_argument('decision', choices=['approved', 'changes-requested'])
    for flag in ['reviewer', 'source', 'reason']:
        decision.add_argument('--' + flag, required=True)
    decision.add_argument('--actor', type=json.loads, help='caller-declared reviewer actor JSON; not authentication')
    preflight = commands.add_parser('dry-run', help='exercise checklist checks before definition review; records no approval or proof')
    preflight.add_argument('run')
    verification = commands.add_parser('verify')
    verification.add_argument('run')
    marking = commands.add_parser('mark')
    marking.add_argument('run')
    marking.add_argument('item')
    marking.add_argument('result', choices=['passed', 'failed', 'unknown'])
    marking.add_argument('--evidence', required=True)
    marking.add_argument('--reviewer', help='actual operator attesting a seed check')
    marking.add_argument('--source', help='reference to the operator observation')
    marking.add_argument('--covers-all', action='store_true',
                         help='the operator meant this evidence to attest every expectation it is already recorded for')
    marking.add_argument('--actor', type=json.loads, help='caller-declared reviewer actor JSON; not authentication')
    baseline = commands.add_parser('lock-tests')
    baseline.add_argument('run')
    baseline.add_argument('paths', nargs='+')
    baseline.add_argument('--source', required=True)
    baseline.add_argument('--failure-evidence', required=True)
    baseline.add_argument('--reproduce', metavar='ARGV_JSON',
                          help='run this command now (JSON argv array) and require it to fail on the unfixed code')
    skill = commands.add_parser('skills')
    skill.add_argument('tool', choices=list(TOOL_ACCEPTABLE_CODES))
    skill.add_argument('arguments', nargs=argparse.REMAINDER)
    launch = commands.add_parser('stage', help='open a stage: commit, checkout, compile rules, print the launch prompt')
    launch.add_argument('run')
    launch.add_argument('n', type=int)
    where = launch.add_mutually_exclusive_group()
    where.add_argument('--worktree', metavar='PATH')
    where.add_argument('--here', action='store_true')
    launch.add_argument('--seed-contract', metavar='PATH')
    launch.add_argument('--tinkery', metavar='PATH', help='trusted Tinkery executable for verified seed intake')
    launch.add_argument('--check', action='store_true', help='validate and print the plan; write nothing')
    execution = commands.add_parser('step', help='explicit pinned build-step launch, resume and report completion')
    execution.add_argument('run')
    execution.add_argument('name')
    execution.add_argument('action', choices=['prepare', 'show', 'launch', 'resume', 'complete'])
    execution.add_argument('--report', metavar='ABSOLUTE_PATH')
    execution.add_argument('--model', metavar='PROVIDER/MODEL')
    execution.add_argument('--launcher', metavar='ABSOLUTE_PATH')
    lint = commands.add_parser('walk', help='structural walk lint (read-only)')
    lint.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        {'new': create, 'status': status, 'capabilities': capabilities, 'decide': decide, 'dry-run': dry_run, 'verify': verify, 'mark': mark, 'lock-tests': lock_tests, 'skills': skills, 'stage': stage, 'step': step, 'walk': walk}[args.command](args)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError) as error:
        if args.command in ('status', 'capabilities') and args.json:
            machine_output({'error': {'code': 'not-found' if isinstance(error, FileNotFoundError) else 'invalid-state',
                                      'message': describe(error)}})
        else:
            print(f'Error: {describe(error)}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
