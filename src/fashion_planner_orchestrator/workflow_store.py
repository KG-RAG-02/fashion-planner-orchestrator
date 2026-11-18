"""Private immutable bytes, durable stage checkpoints and request serialization."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import warnings

# Conventions section 8: artifact://<namespace>/<relative-path>; P08 mints artifact://orchestrator/blobs/<sha256>.
ARTIFACT_URI = re.compile(r'artifact://[A-Za-z0-9_./-]+')
P08_BLOB_URI = re.compile(r'artifact://orchestrator/blobs/([0-9a-f]{64})')
SHA256_HEX = re.compile(r'[0-9a-f]{64}')
MAX_ARTIFACT_BYTES = 16777216  # retained response limit (conventions section 6)
# Resolver failures that keep their own code and HTTP status (conventions section 8).
RESOLVER_CODES = {'ARTIFACT_READER_REQUIRED': 503, 'INVALID_ARTIFACT_URI': 422, 'INVALID_ARTIFACT_PATH': 422,
                  'ARTIFACT_UNAVAILABLE': 503, 'ARTIFACT_SIZE_MISMATCH': 422, 'ARTIFACT_HASH_MISMATCH': 422,
                  'ARTIFACT_TOO_LARGE': 413}


MD_DECISIONS = ('adopt', 'revise', 'hold')
EXCERPT_CHARS = 120


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def query_excerpt(text, limit=EXCERPT_CHARS):
    """Whitespace-collapsed excerpt of at most `limit` characters."""
    flat = ' '.join(str(text or '').split())
    return flat if len(flat) <= limit else flat[:limit - 1] + '\u2026'


class RunStateError(ValueError):
    """A run-state conflict (404 unknown run, 409 not complete or integrated response mismatch)."""

    def __init__(self, code, http_status):
        self.code, self.http_status = code, http_status
        super().__init__(code)


class ArtifactError(ValueError):
    """Fail-closed artifact failure; a ValueError so existing callers keep treating it as invalid."""

    def __init__(self, code, http_status=None):
        self.code = code
        self.http_status = http_status or RESOLVER_CODES.get(code, 422)
        super().__init__(code)


def artifact_path(uri):
    """Validate the URI grammar and return (namespace, relative segments); no empty, . or .. segment."""
    if not isinstance(uri, str) or not ARTIFACT_URI.fullmatch(uri):
        raise ArtifactError('INVALID_ARTIFACT_URI')
    segments = uri[len('artifact://'):].split('/')
    if len(segments) < 2 or any(segment in ('', '.', '..') for segment in segments):
        raise ArtifactError('INVALID_ARTIFACT_PATH')
    return segments[0], segments[1:]


def p08_artifact(sha, media_type, size):
    """The content-addressed P08 reference: the same bytes always give the same URI."""
    return dict(artifact_id=sha, uri='artifact://orchestrator/blobs/' + sha, sha256=sha, media_type=media_type,
                size_bytes=size, immutable=True)


def _reference(artifact):
    """(size_bytes, sha256) of a well-formed reference."""
    if not isinstance(artifact, dict):
        raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
    size, sha = artifact.get('size_bytes'), artifact.get('sha256')
    if (not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha)):
        raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
    return size, sha


def _read_regular(descriptor, size=None):
    """Read an already opened descriptor only if it is a regular file (of `size` bytes when given)."""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError('INVALID_ARTIFACT_PATH')
        if size is not None and info.st_size != size:
            raise ArtifactError('ARTIFACT_SIZE_MISMATCH')
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, 'rb') as handle:
        return handle.read() if size is None else handle.read(size + 1)


def _check_bytes(data, size, sha):
    if len(data) != size:
        raise ArtifactError('ARTIFACT_SIZE_MISMATCH')
    if hashlib.sha256(data).hexdigest() != sha:
        raise ArtifactError('ARTIFACT_HASH_MISMATCH')


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
            # artifacts: legacy artifact://local/<uuid> rows and imported peer references (exact metadata).
            # blob_refs/blob_owners: content-addressed artifact://orchestrator/blobs/<sha256> references.
            db.executescript('''
            CREATE TABLE IF NOT EXISTS artifacts(uri TEXT PRIMARY KEY, metadata TEXT NOT NULL, blob TEXT NOT NULL, owner TEXT);
            CREATE TABLE IF NOT EXISTS blob_refs(sha TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, PRIMARY KEY(sha, media_type));
            CREATE TABLE IF NOT EXISTS blob_owners(sha TEXT NOT NULL, owner TEXT NOT NULL, PRIMARY KEY(sha, owner));
            CREATE TABLE IF NOT EXISTS checkpoints(key TEXT PRIMARY KEY, state TEXT NOT NULL, value TEXT);
            CREATE TABLE IF NOT EXISTS requests(rid TEXT PRIMARY KEY, input_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(rid TEXT NOT NULL, attempt TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(rid,attempt));
            CREATE TABLE IF NOT EXISTS run_index(rid TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                query_excerpt TEXT NOT NULL, status TEXT NOT NULL, integrated_response_sha256 TEXT);
            CREATE TABLE IF NOT EXISTS md_decisions(rid TEXT NOT NULL, sequence INTEGER NOT NULL, integrated_response_sha256 TEXT NOT NULL,
                decision TEXT NOT NULL, note TEXT, decided_at TEXT NOT NULL, PRIMARY KEY(rid, sequence));
            CREATE TRIGGER IF NOT EXISTS md_decisions_no_update BEFORE UPDATE ON md_decisions
                BEGIN SELECT RAISE(ABORT, 'md_decisions is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS md_decisions_no_delete BEFORE DELETE ON md_decisions
                BEGIN SELECT RAISE(ABORT, 'md_decisions is append-only'); END;
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

    def _write_blob(self, data, sha):
        """Store bytes at blobs/<sha256> atomically (temp file + rename); concurrent writers agree."""
        path = self.blobs / sha
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode):
                raise ArtifactError('ARTIFACT_STORAGE_CORRUPT', 503)
            try:
                if info.st_size == len(data) and hashlib.sha256(self._read_blob(sha)).hexdigest() == sha:
                    return
            except ArtifactError:
                pass
            # A truncated or damaged copy (e.g. left by a crash) is replaced by the bytes its name commits to.
        descriptor, temporary = tempfile.mkstemp(dir=self.blobs, prefix='.tmp-')
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _read_blob(self, sha):
        if not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha):
            raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
        try:
            descriptor = os.open(self.blobs / sha, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ArtifactError('ARTIFACT_UNAVAILABLE') from None
        except OSError as exc:
            raise ArtifactError('INVALID_ARTIFACT_PATH' if exc.errno == errno.ELOOP else 'ARTIFACT_UNAVAILABLE') from None
        return _read_regular(descriptor)

    def put(self, data, mime='application/json', owner=None):
        """Content-addressed: artifact://orchestrator/blobs/<sha256>; identical bytes are stored once."""
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        self._write_blob(data, sha)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO blob_refs VALUES (?,?,?)', (sha, mime, len(data)))
            if owner is not None:
                db.execute('INSERT OR IGNORE INTO blob_owners VALUES (?,?)', (sha, owner))
        return p08_artifact(sha, mime, len(data))

    def media_types(self, sha):
        """[(media_type, size_bytes)] registered for one P08 blob."""
        with self.db() as db:
            return db.execute('SELECT media_type,size_bytes FROM blob_refs WHERE sha=? ORDER BY media_type', (sha,)).fetchall()

    def import_verified(self, artifact, data):
        """Import an explicitly resolved producer artifact without rewriting its URI."""
        size, sha = _reference(artifact)
        artifact_path(artifact.get('uri'))
        if not isinstance(artifact.get('media_type'), str) or not artifact['media_type']:
            raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
        _check_bytes(data, size, sha)
        stored = self.put(data, artifact['media_type'])
        with self.db() as db:
            row = db.execute('SELECT metadata FROM artifacts WHERE uri=?', (artifact['uri'],)).fetchone()
            if row and strict_json(row[0].encode()) != artifact:
                raise ValueError('ARTIFACT_REFERENCE_COLLISION')
            db.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?,?,NULL)', (artifact['uri'], canonical(artifact).decode(), stored['sha256']))

    def verify(self, artifact, *, owner=None):
        """Return the bytes of a reference minted or imported by this store (exact metadata match)."""
        if not isinstance(artifact, dict) or not isinstance(artifact.get('uri'), str):
            raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
        match = P08_BLOB_URI.fullmatch(artifact['uri'])
        if match:
            sha, media = match.group(1), artifact.get('media_type')
            if not isinstance(media, str):
                raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
            with self.db() as db:
                row = db.execute('SELECT size_bytes FROM blob_refs WHERE sha=? AND media_type=?', (sha, media)).fetchone()
                owned = owner is None or db.execute('SELECT 1 FROM blob_owners WHERE sha=? AND owner=?', (sha, owner)).fetchone()
            if not row or not owned or artifact != p08_artifact(sha, media, row[0]):
                raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
            blob = sha
        else:
            with self.db() as db:
                row = db.execute('SELECT metadata,blob,owner FROM artifacts WHERE uri=?', (artifact['uri'],)).fetchone()
            if not row or strict_json(row[0].encode()) != artifact or (owner is not None and row[2] != owner):
                raise ArtifactError('ARTIFACT_REFERENCE_INVALID')
            blob = row[1]
        size, sha = _reference(artifact)
        data = self._read_blob(blob)
        _check_bytes(data, size, sha)
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
            envelope = value.get('envelope') if isinstance(value, dict) else None
            if attempt == 'current' and isinstance(envelope, dict):
                db.execute('UPDATE run_index SET status=?, integrated_response_sha256=?, updated_at=? WHERE rid=?',
                           (str(envelope.get('status')), digest(envelope), utc_now(), rid))
            elif attempt == 'progress':
                db.execute('UPDATE run_index SET updated_at=? WHERE rid=? AND integrated_response_sha256 IS NULL', (utc_now(), rid))

    def index_run(self, rid, text):
        """Record a run for the listing once (created_at is never rewritten)."""
        stamp = utc_now()
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO run_index VALUES (?,?,?,?,?,NULL)', (rid, stamp, stamp, query_excerpt(text), 'in_progress'))

    def run_exists(self, rid):
        with self.db() as db:
            return db.execute('SELECT 1 FROM requests WHERE rid=? UNION ALL SELECT 1 FROM runs WHERE rid=? LIMIT 1',
                              (rid, rid)).fetchone() is not None

    def integrated_response(self, rid):
        """(envelope, python-json-sort sha256) of the latest final integrated response, or None."""
        value = self.run(rid, 'current') or self.run(rid)
        return (value['envelope'], digest(value['envelope'])) if value else None

    def list_runs(self, limit):
        """Newest first: request_id, status, created_at, updated_at, query_excerpt."""
        with self.db() as db:
            rows = db.execute('SELECT r.rid, i.created_at, i.updated_at, i.query_excerpt, i.status FROM requests r '
                              'LEFT JOIN run_index i ON i.rid = r.rid '
                              'ORDER BY (i.created_at IS NULL), i.created_at DESC, r.rowid DESC LIMIT ?', (limit,)).fetchall()
        runs = []
        for rid, created, updated, excerpt, status in rows:
            if created is None:  # recorded before the run index existed
                final = self.run(rid, 'current') or self.run(rid)
                value = final or self.run(rid, 'progress')
                result = value['envelope'].get('result', {}) if value else {}
                status = final['envelope'].get('status') if final else 'in_progress'
                excerpt = query_excerpt((result.get('user_input') or {}).get('text', '') if isinstance(result, dict) else '')
            runs.append(dict(request_id=rid, status=status, created_at=created, updated_at=updated, query_excerpt=excerpt))
        return runs

    def add_md_decision(self, rid, expected_sha256, decision, note):
        """Append one MD decision after checking it targets the stored final integrated response."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')  # the check and the append see one state of the runs table
            row = (db.execute("SELECT value FROM runs WHERE rid=? AND attempt='current'", (rid,)).fetchone()
                   or db.execute("SELECT value FROM runs WHERE rid=? AND attempt='initial'", (rid,)).fetchone())
            if row is None:
                known = db.execute('SELECT 1 FROM requests WHERE rid=? UNION ALL SELECT 1 FROM runs WHERE rid=? LIMIT 1',
                                   (rid, rid)).fetchone()
                raise RunStateError('RUN_NOT_COMPLETE', 409) if known else RunStateError('RUN_NOT_FOUND', 404)
            if digest(strict_json(row[0].encode())['envelope']) != expected_sha256:
                raise RunStateError('INTEGRATED_RESPONSE_MISMATCH', 409)
            decided_at = utc_now()
            db.execute('INSERT INTO md_decisions SELECT ?, COALESCE(MAX(sequence), 0) + 1, ?, ?, ?, ? FROM md_decisions WHERE rid=?',
                       (rid, expected_sha256, decision, note, decided_at, rid))
            sequence = db.execute('SELECT MAX(sequence) FROM md_decisions WHERE rid=?', (rid,)).fetchone()[0]
        return dict(request_id=rid, sequence=sequence, integrated_response_sha256=expected_sha256,
                    decision=decision, note=note, decided_at=decided_at)

    def md_decisions(self, rid):
        """Decision history, oldest first."""
        with self.db() as db:
            rows = db.execute('SELECT sequence, integrated_response_sha256, decision, note, decided_at FROM md_decisions '
                              'WHERE rid=? ORDER BY sequence', (rid,)).fetchall()
        return [dict(request_id=rid, sequence=sequence, integrated_response_sha256=sha, decision=decision, note=note,
                     decided_at=decided_at) for sequence, sha, decision, note, decided_at in rows]

    def run(self, rid, attempt='initial'):
        with self.db() as db:
            row = db.execute('SELECT value FROM runs WHERE rid=? AND attempt=?', (rid, attempt)).fetchone()
        return strict_json(row[0].encode()) if row else None


class PeerArtifactReader:
    """Resolve peer artifacts (artifact://research/...) under a read-only root (ORCHESTRATOR_PEER_ARTIFACT_ROOT).

    artifact://<namespace>/<path> maps to <root>/<namespace>/<path>. Every component below the root
    is opened with O_NOFOLLOW relative to its parent, so escapes and symlinks are refused; the file
    must be regular with exactly size_bytes bytes and the reference's sha256. Fails closed with the
    conventions section 8 codes.
    """

    def __init__(self, root, namespaces=('research',), max_bytes=MAX_ARTIFACT_BYTES):
        self.root = Path(root)
        self.namespaces = frozenset(namespaces)
        self.max_bytes = max_bytes

    def __call__(self, artifact):
        size, sha = _reference(artifact)
        namespace, segments = artifact_path(artifact.get('uri'))
        if namespace not in self.namespaces:
            raise ArtifactError('INVALID_ARTIFACT_URI')
        if size > self.max_bytes:
            raise ArtifactError('ARTIFACT_TOO_LARGE')
        data = self._read([namespace, *segments], size)
        _check_bytes(data, size, sha)
        return data

    @staticmethod
    def _open_error(exc):
        if isinstance(exc, FileNotFoundError):
            return ArtifactError('ARTIFACT_UNAVAILABLE')
        return ArtifactError('INVALID_ARTIFACT_PATH' if exc.errno in (errno.ELOOP, errno.ENOTDIR) else 'ARTIFACT_UNAVAILABLE')

    def _read(self, parts, size):
        directory_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
        try:
            directory = os.open(self.root, directory_flags)
        except OSError:
            raise ArtifactError('ARTIFACT_UNAVAILABLE') from None
        try:
            for part in parts[:-1]:
                try:
                    child = os.open(part, directory_flags | os.O_NOFOLLOW, dir_fd=directory)
                except OSError as exc:
                    raise self._open_error(exc) from None
                os.close(directory)
                directory = child
            try:
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
            except OSError as exc:
                raise self._open_error(exc) from None
        finally:
            os.close(directory)
        return _read_regular(descriptor, size)
