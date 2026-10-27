"""Sessão da Arena: ocupação visual, relógio próprio e trilha de mudanças."""
import os
import time
import json
import queue
import threading
import logging
from datetime import datetime, timedelta
from collections import deque
from uuid import uuid4


def enabled():
    return os.getenv('REALGUARD_ARENA') == '1'


def allowed(path, method):
    import re
    if not enabled():
        return True
    rules = {
        'GET': [r'/',r'/login',r'/preconfig',r'/fonts/[^/]+',r'/ui/i18n.js',r'/api/ui/locale',r'/favicon.ico',r'/video',r'/api/auth/(me|state)',
                r'/api/system/(process|preconfig)',
                r'/api/status',r'/api/arena/(room|history)',r'/api/students',r'/api/students/\d+/(photo|passagens)',
                r'/api/cameras',r'/api/school/(classes|shifts|calendar)',r'/api/arena/incidents',r'/api/arena/incidents/\d+/photo'],
        'POST': [r'/api/auth/(login|logout|password)',r'/api/system/(stop|preconfig|preconfig/open-native|preconfig/test/(camera|proxy))',r'/api/students',r'/api/students/\d+/(faces|consent)',
                 r'/api/cameras',r'/api/cameras/test',r'/api/cameras/\d+/activate',r'/api/school/(classes|shifts|calendar)'],
        'PUT': [r'/api/students/\d+',r'/api/cameras/\d+',r'/api/arena/cameras/\d+/class',r'/api/arena/incidents/\d+',r'/api/school/(classes|shifts|calendar)/\d+'],
        'DELETE': [r'/api/school/(classes|shifts|calendar)/\d+'],
    }
    return any(re.fullmatch(p,path) for p in rules.get(method,[]))


class Arena:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.writer = None
        self.reset()

    def reset(self):
        with self.lock:
            self.session = uuid4().hex
            self.started = self.clock()
            self.audit_start_id = 0
            self.origin = datetime.now().replace(hour=19, minute=20, second=0, microsecond=0)
            self.people = {}
            self.events = deque(maxlen=500)
            self.updated = None
            self.camera = None
            self.class_name = ''
            self.classes = {}
            self.sequence = 0
            self.metric_at = None
            self.incidents = {}
            self.incident_seen = set()
            self.photos = {}

    def now(self):
        return self.origin + timedelta(seconds=self.clock()-self.started)

    def emit(self, kind, person=None, photo=None):
        self.sequence += 1
        item = dict(person or {}, id=self.sequence, kind=kind, camera_id=self.camera,
                    class_name_target=self.class_name, session=self.session,
                    created_at=self.now().isoformat(sep=' ', timespec='seconds'),
                    real_created_at=datetime.now().isoformat(sep=' ', timespec='seconds'))
        self.events.appendleft(item)
        if photo:
            self.photos[item['id']] = photo
            if len(self.photos) > 128:
                self.photos.pop(next(iter(self.photos)))
        if self.writer:
            self.writer(dict(item, _photo=photo) if photo else item)
        return item

    def bind(self, camera):
        with self.lock:
            self.camera = int(camera)
            self.class_name = self.classes.get(self.camera, '')
            self.people = {}
            self.updated = None
            self.incident_seen.clear()
            self.emit('CAMERA')

    def set_class(self, camera, name):
        with self.lock:
            self.classes[int(camera)] = name
            if self.camera == int(camera):
                self.class_name = name
                for p in self.people.values():
                    p['group'] = self.group(p)
                self.emit('CLASS')

    def group(self, p):
        if p.get('student_id') is None:
            return p.get('state', 'pending')
        if not self.class_name:
            return 'unassigned'
        return 'same' if p.get('class_name', '').casefold() == self.class_name.casefold() else 'other'

    def observe(self, camera, identities, photo_fn=None):
        with self.lock:
            if self.camera != camera:
                self.bind(camera)
            self.updated = self.clock()
            fresh = {}
            for track, identity in identities.items():
                p = dict(identity, track_id=track)
                key = str(track)
                old = self.people.get(key)
                # Identidade lembrada apenas enquanto o mesmo track continua visível.
                if old and old.get('student_id') and not p.get('student_id'):
                    for field in ('student_id','name','class_name','similarity'):
                        p[field] = old.get(field)
                p['group'] = self.group(p)
                fresh[key] = p
                kind = 'obstructed' if p.get('obstructed') or p.get('state') == 'obstructed' else ('unknown' if p.get('state') == 'unknown' and not p.get('student_id') else None)
                incident_key = (track, kind)
                if kind and incident_key not in self.incident_seen:
                    self.incident_seen.add(incident_key)
                    photo = None
                    if photo_fn:
                        try:
                            photo = photo_fn(track)
                        except Exception:
                            logging.getLogger(__name__).exception('Falha ao capturar ocorrência da Arena')
                    event = self.emit('INCIDENT', dict(p, incident_type=kind, status='PENDING', has_photo=bool(photo)), photo=photo)
                    self.incidents[event['id']] = event
                if old is None:
                    self.emit('VISIBLE', p)
                elif any(p.get(k) != old.get(k) for k in ('student_id','state','group','obstructed')):
                    self.emit('STATUS', p)
            for key, old in self.people.items():
                if key not in fresh:
                    self.emit('LEFT_VIEW', old)
            self.people = fresh

    def resolve(self, incident_id, status, username):
        with self.lock:
            item = self.incidents.get(incident_id)
            if item is None:
                raise KeyError(incident_id)
            if item['status'] != 'PENDING':
                raise ValueError('Ocorrência já resolvida.')
            if self.writer:
                from database.connection import get_db_connection
                with get_db_connection() as conn:
                    conn.execute('INSERT OR IGNORE INTO arena_photo_discarded(session,incident_id) VALUES(?,?)', (self.session,incident_id))
                    conn.execute('DELETE FROM arena_photos WHERE session=? AND incident_id=?', (self.session,incident_id))
                    conn.commit()
            self.photos.pop(incident_id, None)
            item = dict(item, status=status, has_photo=False, resolved_by=username, resolved_at=self.now().isoformat(sep=' ', timespec='seconds'))
            self.incidents[incident_id] = item
            self.emit('RESOLVED', dict(item, incident_id=incident_id))
            return item

    def snapshot(self):
        with self.lock:
            stale = self.updated is None or self.clock()-self.updated > 3
            people = [] if stale else list(self.people.values())
            counts = {k:sum(p['group']==k for p in people) for k in ('same','other','unassigned','unknown','pending','obstructed')}
            counts['obstructed'] = sum(bool(p.get('obstructed')) or p.get('state')=='obstructed' for p in people)
            counts['identified'] = sum(p.get('student_id') is not None for p in people)
            return dict(session=self.session, audit_start_id=self.audit_start_id,
                        clock=self.now().isoformat(sep=' ',timespec='seconds'),
                        camera_id=self.camera, class_name=self.class_name, stale=stale,
                        people=people, counts=counts, total=len(people), events=list(self.events)[:100], storage_error=storage_error)

    def metrics(self, status):
        with self.lock:
            now = self.clock()
            if self.metric_at is None or now-self.metric_at >= 5:
                self.metric_at = now
                keys=('camera_online','fps_camera','fps_video','fps_processamento','ms_deteccao','ms_reconhecimento','resolucao','resolucao_video')
                self.emit('METRICS', {'metrics':{k:status.get(k) for k in keys},'counts':self.snapshot()['counts']})


arena = Arena()
_queue = queue.SimpleQueue()
_worker = None
storage_error = None


def initialize():
    from database.connection import get_db_connection
    with get_db_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS arena_classes(camera_id INTEGER PRIMARY KEY, class_name TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS arena_events(id INTEGER PRIMARY KEY, session TEXT, payload TEXT NOT NULL)')
        conn.execute('CREATE INDEX IF NOT EXISTS arena_events_session ON arena_events(session,id)')
        conn.execute('CREATE TABLE IF NOT EXISTS arena_photos(session TEXT NOT NULL, incident_id INTEGER NOT NULL, jpeg BLOB NOT NULL, PRIMARY KEY(session,incident_id))')
        conn.execute('CREATE TABLE IF NOT EXISTS arena_photo_discarded(session TEXT NOT NULL, incident_id INTEGER NOT NULL, PRIMARY KEY(session,incident_id))')
        conn.commit()


def start():
    global _worker, storage_error
    from database.connection import get_db_connection
    initialize()
    with get_db_connection() as conn:
        # Uma apresentação começa sem presenças, ocorrências, fotos ou histórico de sessões anteriores.
        # As turmas atribuídas às câmeras e os cadastros gerais permanecem intactos.
        conn.execute('DELETE FROM arena_photos')
        conn.execute('DELETE FROM arena_photo_discarded')
        conn.execute('DELETE FROM arena_events')
        classes = {r[0]:r[1] for r in conn.execute('SELECT camera_id,class_name FROM arena_classes')}
        audit_start_id = conn.execute('SELECT COALESCE(MAX(id),0) FROM audit_logs').fetchone()[0]
        conn.commit()
    arena.reset()
    arena.classes = classes
    arena.audit_start_id = audit_start_id
    storage_error = None
    arena.writer = _queue.put
    _worker = threading.Thread(target=_save,daemon=True,name='arena-audit')
    _worker.start()


def _save():
    global storage_error
    from database.connection import get_db_connection
    while True:
        item = _queue.get()
        if item is None:
            return
        try:
            item = dict(item)
            photo = item.pop('_photo', None)
            with get_db_connection() as conn:
                conn.execute('INSERT INTO arena_events(session,payload) VALUES(?,?)',(item['session'],json.dumps(item,ensure_ascii=False)))
                if photo:
                    conn.execute('INSERT OR REPLACE INTO arena_photos(session,incident_id,jpeg) SELECT ?,?,? WHERE NOT EXISTS (SELECT 1 FROM arena_photo_discarded WHERE session=? AND incident_id=?)', (item['session'],item['id'],photo,item['session'],item['id']))
                conn.commit()
        except Exception:
            storage_error = 'Falha ao salvar auditoria da Arena; consulte o log.'
            logging.getLogger(__name__).exception(storage_error)


def stop():
    arena.writer = None
    if _worker:
        _queue.put(None)
        _worker.join(timeout=12)
