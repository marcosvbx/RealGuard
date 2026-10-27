"""Foto principal autorizada; bancos antigos podem conter somente embeddings."""
from database.connection import get_db_connection


def initialize():
    with get_db_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS student_photos(student_id INTEGER PRIMARY KEY, jpeg BLOB NOT NULL)')
        conn.commit()


def save(student_id, jpeg):
    initialize()
    with get_db_connection() as conn:
        conn.execute('INSERT OR REPLACE INTO student_photos(student_id,jpeg) VALUES(?,?)',(student_id,jpeg))
        conn.commit()


def get(student_id):
    initialize()
    with get_db_connection() as conn:
        row=conn.execute('SELECT p.jpeg FROM student_photos p JOIN students s ON s.id=p.student_id WHERE s.id=? AND s.consent_at IS NOT NULL',(student_id,)).fetchone()
    return row[0] if row else None


def remove(student_id):
    initialize()
    with get_db_connection() as conn:
        conn.execute('DELETE FROM student_photos WHERE student_id=?',(student_id,))
        conn.commit()
