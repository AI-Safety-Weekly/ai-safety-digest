"""Scratch-only collection checkpoints, bound to the exact window and inputs."""
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import os
import tempfile

from . import dot_handoff as dot


class DeferredSource(Exception):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('HTTP 429: source collection deferred until Retry-After')


def service_aware_http(method, url, **kwargs):
    from .s2_collector import _default_http
    response = _default_http(method, url, **kwargs)
    if response.status_code == 429:
        value = response.headers.get('Retry-After', '60')
        now = datetime.now(timezone.utc)
        try:
            retry_at = now + timedelta(seconds=max(1, int(value)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value).astimezone(timezone.utc)
                retry_at = max(retry_at, now + timedelta(seconds=1))
            except (TypeError, ValueError, OverflowError):
                retry_at = now + timedelta(seconds=60)
        raise DeferredSource(retry_at.isoformat())
    return response


class Checkpoints:
    def __init__(self, directory, binding, progress=None):
        self.directory = Path(directory)
        self.path = self.directory / 'collection-checkpoint.json'
        self.binding = binding
        self.progress = progress or (lambda event: None)
        if self.path.exists():
            self.data = dot.read_json(self.path)
            dot.keys(self.data, {'schema_version', 'binding', 'parts', 'sha256'}, 'checkpoint')
            payload = {k:v for k,v in self.data.items() if k != 'sha256'}
            if self.data['schema_version'] != 1 or self.data['binding'] != binding or self.data['sha256'] != dot.digest(payload):
                raise dot.Invalid('checkpoint hash/window/provenance mismatch')
        else:
            self.data = {'schema_version':1, 'binding':binding, 'parts':{}}
        if not isinstance(self.data['parts'], dict):
            raise dot.Invalid('checkpoint parts must be an object')

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {k:v for k,v in self.data.items() if k != 'sha256'}
        self.data = {**payload, 'sha256':dot.digest(payload)}
        fd, path = tempfile.mkstemp(prefix='.checkpoint-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(dot.canonical(self.data) + b'\n')
            os.replace(path, self.path)
        finally:
            Path(path).unlink(missing_ok=True)

    def collect(self, name, callback, events):
        previous = self.data['parts'].get(name)
        if previous and previous['complete']:
            events.extend(previous['events'])
            papers = [dot.paper_from(p) for p in previous['papers']]
            self.progress({'source':name, 'status':'reused', 'count':len(papers)})
            return papers, previous['extra']
        if previous and previous.get('retry_at') and dot.utc(previous['retry_at']) > datetime.now(timezone.utc):
            events.extend(previous['events'])
            raise DeferredSource(previous['retry_at'])
        self.progress({'source':name, 'status':'fetching'})
        start = len(events)
        try:
            papers, extra = callback()
        except DeferredSource as e:
            defer_count = (previous or {}).get('defer_count', 0) + 1
            if defer_count > 1:
                client_floor = datetime.now(timezone.utc) + timedelta(seconds=min(60 * 2**min(defer_count-1, 6), 3600))
                e.retry_at = max(dot.utc(e.retry_at), client_floor).isoformat()
            event = {'module':'safety_digest.s2_collector', 'level':'WARNING',
                     'event_template':'HTTP 429 Retry-After; pending author',
                     'kind':'deferred', 'source':name, 'retry_at':e.retry_at}
            events.append(event)
            self.data['parts'][name] = {'complete':False, 'papers':[], 'extra':{},
                                       'events':events[start:], 'retry_at':e.retry_at, 'defer_count':defer_count}
            self.save()
            self.progress({'source':name, 'status':'deferred', 'retry_at':e.retry_at})
            raise
        except Exception as error:
            # Preserve completed sources and identify the unresolved source.
            # Never persist exception messages, which may contain request data.
            events.append({'module':'safety_digest.collection', 'level':'ERROR',
                           'event_template':'source collection raised', 'kind':'terminal_failure',
                           'source':name, 'exception_type':type(error).__name__})
            papers, extra = [], {}
        recorded = events[start:]
        complete = not any(e.get('kind') != 'retry' for e in recorded)
        self.data['parts'][name] = {'complete':complete, 'papers':[dot.paper_dict(p) for p in papers],
                                   'extra':extra, 'events':recorded, 'retry_at':None}
        self.save()
        self.progress({'source':name, 'status':'complete' if complete else 'incomplete', 'count':len(papers)})
        return papers, extra
