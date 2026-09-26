"""Content addressed local artifacts."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import uuid

from .contracts import canonical, fingerprint, read_json, require, ContractError, file_hash


def implementation_hash():
    package = Path(__file__).parent
    project = package.parent
    sources = sorted([*package.rglob('*.py'), *(project/'tools').glob('*.py')])
    return fingerprint({p.relative_to(project).as_posix(): file_hash(p) for p in sources})


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def writer(self):
        lock = self.root/'.artifact-writer.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ContractError('WRITER_BUSY: inspect the owning process before clearing a stale lock') from exc
        try:
            os.write(descriptor, str(os.getpid()).encode())
            yield
        finally:
            os.close(descriptor)
            lock.unlink()

    def path(self, artifact_id):
        match = re.fullmatch(r'([a-z][a-z0-9_]*)-([0-9a-f]{64})', artifact_id)
        require(match is not None, 'INVALID_ARTIFACT_ID')
        path = self.root/'artifacts'/match.group(1)/f'{artifact_id}.json'
        require(path.resolve().is_relative_to(self.root), 'ARTIFACT_PATH_ESCAPE')
        return path

    def put(self, kind, payload, inputs=(), config=None):
        require(re.fullmatch(r'[a-z][a-z0-9_]*', kind) is not None, 'INVALID_ARTIFACT_TYPE')
        inputs = sorted(set(inputs))
        for item in inputs:
            self.get(item)
        body = {'schema_version': '1.0', 'artifact_type': kind, 'payload': payload,
                'input_artifacts': inputs, 'config_hash': fingerprint(config or {}),
                'code_hash': implementation_hash(), 'visibility': 'internal'}
        aid = f'{kind}-{fingerprint(body)}'
        path = self.path(aid)
        with self.writer():
            if path.exists():
                self.get(aid, kind)
                return aid
            path.parent.mkdir(parents=True, exist_ok=True)
            envelope = dict(body, artifact_id=aid, status='committed', created_at=datetime.now(timezone.utc).isoformat())
            # Keep temp basename short: app workspaces may already be deeply nested on Windows.
            temp = path.parent/f'.{uuid.uuid4().hex}.tmp'
            try:
                with temp.open('xb') as stream:
                    stream.write(canonical(envelope))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                if temp.exists(): temp.unlink()
        return aid

    def get(self, artifact_id, kind=None):
        path = self.path(artifact_id)
        require(path.is_file(), 'ARTIFACT_NOT_FOUND', artifact_id)
        value = read_json(path)
        require(isinstance(value, dict), 'INVALID_ARTIFACT')
        require(value.get('status') == 'committed' and value.get('artifact_id') == artifact_id, 'ARTIFACT_NOT_COMMITTED')
        require(kind is None or value.get('artifact_type') == kind, 'ARTIFACT_TYPE_MISMATCH')
        body = {k: v for k, v in value.items() if k not in ['artifact_id', 'status', 'created_at']}
        require(artifact_id == f'{value.get("artifact_type")}-{fingerprint(body)}', 'ARTIFACT_HASH_MISMATCH')
        return value

    def verify_tree(self, artifact_id, seen=None):
        seen = set() if seen is None else seen
        if artifact_id in seen: return
        seen.add(artifact_id)
        value = self.get(artifact_id)
        for dependency in value['input_artifacts']:
            self.verify_tree(dependency, seen)
