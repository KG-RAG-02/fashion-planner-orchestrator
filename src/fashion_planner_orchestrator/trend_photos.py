"""Trend article photos for the planning sheet (stage 06).

The poster shows two or three example photos of the Trend style it is about. Stage 06 downloads them once
from the URLs in the stored Trend result (``style.example_images``, then the items' ``image_urls``), checks
them like any other image, and keeps them in the run's store; the planning-sheet route later renders from
those stored bytes without network access.

Rules: HTTPS only, hosts on an allowlist (``ORCHESTRATOR_TREND_PHOTO_HOSTS``, default ``vogue.co.kr`` and its
subdomains), at most two redirects that must stay on the allowlist, a byte cap, a pixel cap, and the same decode
checks as uploaded images. A photo that fails, including one with a malformed URL, is skipped; it never fails
the run. Nothing about the photos is added to
the integrated envelope (its contract is unchanged); the store records them under ``trend-photos:<request_id>``.
"""
from __future__ import annotations

import http.client
import io
import os
import ssl
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

from .planning_sheet import trend_image_urls
from .workflow_store import checked_image

DEFAULT_HOSTS = ('vogue.co.kr',)
MAX_PHOTOS = 3
CANDIDATES = 6
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTO_PIXELS = 6_000_000  # e.g. 2000 x 3000 (Vogue article photos are about 1.3 MP); keeps decoding inside the container memory limit
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 2
MEDIA_TYPES = {'image/png', 'image/jpeg', 'image/webp'}


class PhotoRejected(Exception):
    """A photo that is not fetched or not kept; ``code`` says why (no URL or body in the message)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return str((os.environ if env is None else env).get('ORCHESTRATOR_TREND_PHOTOS_ENABLED') or '1').strip() != '0'


def allowed_hosts(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """ORCHESTRATOR_TREND_PHOTO_HOSTS: comma list of host names; each also admits its subdomains. Blank → vogue.co.kr."""
    value = str((os.environ if env is None else env).get('ORCHESTRATOR_TREND_PHOTO_HOSTS') or '').strip().lower()
    hosts = tuple(host.strip().strip('.') for host in value.split(',') if host.strip().strip('.'))
    return hosts or DEFAULT_HOSTS


def host_allowed(host: str | None, hosts: Sequence[str]) -> bool:
    host = (host or '').lower().rstrip('.')
    return bool(host) and any(host == allowed or host.endswith('.' + allowed) for allowed in hosts)


def _https_get(url: str, timeout: float) -> tuple[int, Mapping[str, str], bytes]:
    parts = urlsplit(url)
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout,
                                             context=ssl.create_default_context())
    path = parts.path or '/'
    if parts.query:
        path += '?' + parts.query
    try:
        connection.request('GET', path, headers={'Accept': 'image/png, image/jpeg, image/webp',
                                                 'User-Agent': 'vogue-orchestrator-planning-sheet'})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(MAX_PHOTO_BYTES + 1) if response.status == 200 else b''
        return response.status, headers, body
    finally:
        connection.close()


def fetch_photo(url: str, hosts: Sequence[str], *, timeout: float = TIMEOUT_SECONDS,
                get: Callable[[str, float], tuple[int, Mapping[str, str], bytes]] = _https_get) -> tuple[bytes, str]:
    """Bytes and media type of one allowlisted HTTPS image, or PhotoRejected."""
    for _ in range(MAX_REDIRECTS + 1):
        try:
            parts = urlsplit(url)
            hostname, _ = parts.hostname, parts.port  # .port raises on a malformed port
        except ValueError:
            raise PhotoRejected('PHOTO_URL_INVALID') from None
        if parts.scheme != 'https':
            raise PhotoRejected('PHOTO_SCHEME_NOT_HTTPS')
        if not host_allowed(hostname, hosts):
            raise PhotoRejected('PHOTO_HOST_NOT_ALLOWED')
        try:
            status, headers, body = get(url, timeout)
        except PhotoRejected:
            raise
        except Exception:
            raise PhotoRejected('PHOTO_UNAVAILABLE') from None
        if status in (301, 302, 303, 307, 308) and headers.get('location'):
            try:
                url = urljoin(url, headers['location'])
            except ValueError:
                raise PhotoRejected('PHOTO_URL_INVALID') from None
            continue
        if status != 200:
            raise PhotoRejected('PHOTO_HTTP_STATUS')
        media = str(headers.get('content-type') or '').split(';')[0].strip().lower()
        if media not in MEDIA_TYPES:
            raise PhotoRejected('PHOTO_MEDIA_UNSUPPORTED')
        if len(body) > MAX_PHOTO_BYTES:
            raise PhotoRejected('PHOTO_TOO_LARGE')
        try:
            pixels = _pixels(body)
        except Exception:
            raise PhotoRejected('PHOTO_DECODE_INVALID') from None
        if pixels > MAX_PHOTO_PIXELS:  # checked from the header, before anything is decoded
            raise PhotoRejected('PHOTO_TOO_LARGE')
        try:
            checked_image(body, media)
        except ValueError:
            raise PhotoRejected('PHOTO_DECODE_INVALID') from None
        return body, media
    raise PhotoRejected('PHOTO_TOO_MANY_REDIRECTS')


def _pixels(data: bytes) -> int:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:  # reads the header only
        return image.width * image.height


def checkpoint_key(request_id: str) -> str:
    return f'trend-photos:{request_id}'


class TrendPhotos:
    """Stage 06 hook: fetch and store the photos of one run once; later calls reuse what was stored."""

    def __init__(self, store, env: Mapping[str, str] | None = None, *, fetch: Callable[..., tuple[bytes, str]] = fetch_photo):
        self.store = store
        self.hosts = allowed_hosts(env)
        self.fetch = fetch

    def __call__(self, request_id: str, envelope: Mapping[str, Any]) -> list[tuple[bytes, str]]:
        key = checkpoint_key(request_id)
        if self.store.checkpoint(key):
            return load_trend_photos(self.store, request_id)
        result = envelope.get('result') if isinstance(envelope, Mapping) else None
        photos: list[tuple[bytes, str]] = []
        kept, skipped = [], []
        for url in trend_image_urls(result if isinstance(result, Mapping) else {}, limit=CANDIDATES):
            if len(photos) == MAX_PHOTOS:
                break
            try:
                data, media = self.fetch(url, self.hosts)
            except PhotoRejected as exc:
                skipped.append(dict(url=url, reason=exc.code))
                continue
            except Exception:  # an unexpected failure drops this photo only
                skipped.append(dict(url=url, reason='PHOTO_UNAVAILABLE'))
                continue
            kept.append(dict(url=url, artifact=self.store.put(data, media, owner=request_id)))
            photos.append((data, url))
        self.store.checkpoint_write(key, 'done', dict(photos=kept, skipped=skipped))
        return photos


def load_trend_photos(store, request_id: str) -> list[tuple[bytes, str]]:
    """The photos stage 06 stored for a run, verified against their references; unreadable ones are left out."""
    checkpoint = store.checkpoint(checkpoint_key(request_id))
    record = checkpoint[1] if checkpoint and isinstance(checkpoint[1], Mapping) else {}
    photos = []
    for item in record.get('photos') or []:
        if not isinstance(item, Mapping) or not isinstance(item.get('url'), str):
            continue
        try:
            photos.append((store.verify(item.get('artifact'), owner=request_id), item['url']))
        except Exception:  # a damaged blob drops the photo; the sheet still renders
            continue
    return photos[:MAX_PHOTOS]
