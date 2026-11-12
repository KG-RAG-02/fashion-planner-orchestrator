"""Private immutable bytes, durable stage checkpoints and request serialization."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import uuid
import warnings


def canonical(value):
    def check(node):
        if isinstance(node, dict):
            if any(not isinstance(k, str) for k in node):
                raise ValueError('NON_STRING_KEY')
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(data):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError('DUPLICATE_JSON_KEY')
            out[k] = v
        return out
    def invalid(_):
        raise ValueError('NONFINITE_NUMBER')
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)


def checked_image(data, mime):
    from PIL import Image
    if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise ValueError('IMAGE_MIME_UNSUPPORTED')
    if not 0 < len(data) <= 10485760:
        raise ValueError('IMAGE_SIZE_INVALID')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if Image.MIME.get(im.format) != mime or im.width * im.height > 40_000_000:
                    raise ValueError('IMAGE_DECODE_INVALID')
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
    except Exception:
        raise ValueError('IMAGE_DECODE_INVALID') from None


class WorkflowStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        # Do not follow an attacker-controlled store or parent symlink.
        if any(p.is_symlink() for p in [self.root, *self.root.parents]):
            raise ValueError('STORE_SYMLINK')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.blobs = self.root / 'blobs'
        self.blobs.mkdir(mode=0o700, exist_ok=True)
        if self.blobs.is_symlink():
            raise ValueError('STORE_SYMLINK')
        self.db_path = self.root / 'checkpoints.sqlite3'
        if self.db_path.is_symlink():
            raise ValueError('STORE_SYMLINK')
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS artifacts(uri TEXT PRIMARY KEY, metadata TEXT NOT NULL, blob TEXT NOT NULL, owner TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints(key TEXT PRIMARY KEY, state TEXT NOT NULL, value TEXT);
            CREATE TABLE IF NOT EXISTS requests(rid TEXT PRIMARY KEY, input_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(rid TEXT NOT NULL, attempt TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(rid,attempt));
            ''')
        os.chmod(self.db_path, 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def lock(self, rid):
        path = self.root / (hashlib.sha256(rid.encode()).hexdigest() + '.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def bind(self, rid, input_hash):
        with self.db() as db:
            row = db.execute('SELECT input_hash FROM requests WHERE rid=?', (rid,)).fetchone()
            if row and row[0] != input_hash:
                raise ValueError('REQUEST_ID_INPUT_CONFLICT')
            db.execute('INSERT OR IGNORE INTO requests VALUES (?,?)', (rid, input_hash))

    def put(self, data, mime='application/json', owner=None):
        sha = hashlib.sha256(data).hexdigest()
        uri = 'artifact://local/' + uuid.uuid4().hex
        artifact = dict(artifact_id=uri.rsplit('/', 1)[1], uri=uri, sha256=sha,
                        media_type=mime, size_bytes=len(data), immutable=True)
        path = self.blobs / sha
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError('ARTIFACT_STORAGE_CORRUPT')
        else:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        with self.db() as db:
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?)', (uri, canonical(artifact).decode(), sha, owner))
        return artifact

    def import_verified(self, artifact, data):
        """Import an explicitly resolved producer artifact without rewriting its URI."""
        if len(data) != artifact['size_bytes'] or hashlib.sha256(data).hexdigest() != artifact['sha256']:
            raise ValueError('ARTIFACT_HASH_MISMATCH')
        stored = self.put(data, artifact['media_type'])
        with self.db() as db:
            row = db.execute('SELECT metadata FROM artifacts WHERE uri=?', (artifact['uri'],)).fetchone()
            if row and strict_json(row[0].encode()) != artifact:
                raise ValueError('ARTIFACT_REFERENCE_COLLISION')
            db.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?,?,NULL)', (artifact['uri'], canonical(artifact).decode(), stored['sha256']))

    def verify(self, artifact, *, owner=None):
        with self.db() as db:
            row = db.execute('SELECT metadata,blob,owner FROM artifacts WHERE uri=?', (artifact.get('uri'),)).fetchone()
        if not row or strict_json(row[0].encode()) != artifact or (owner is not None and row[2] != owner):
            raise ValueError('ARTIFACT_REFERENCE_INVALID')
        path = self.blobs / row[1]
        if path.is_symlink() or path.parent != self.blobs:
            raise ValueError('ARTIFACT_PATH_INVALID')
        data = path.read_bytes()
        if len(data) != artifact['size_bytes'] or hashlib.sha256(data).hexdigest() != artifact['sha256']:
            raise ValueError('ARTIFACT_HASH_MISMATCH')
        return data

    def upload(self, data, mime, rid):
        checked_image(data, mime)
        return self.put(data, mime, owner=rid)

    def checkpoint(self, key):
        with self.db() as db:
            row = db.execute('SELECT state,value FROM checkpoints WHERE key=?', (key,)).fetchone()
        return (row[0], strict_json(row[1].encode()) if row[1] else None) if row else None

    def checkpoint_write(self, key, state, value=None):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)', (key, state, canonical(value).decode()))

    def save_run(self, rid, attempt, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)', (rid, attempt, canonical(value).decode()))

    def run(self, rid, attempt='initial'):
        with self.db() as db:
            row = db.execute('SELECT value FROM runs WHERE rid=? AND attempt=?', (rid, attempt)).fetchone()
        return strict_json(row[0].encode()) if row else None
