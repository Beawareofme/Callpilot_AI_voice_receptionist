import os
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import pooling

load_dotenv()

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="callpilot_pool",
            pool_size=5,
            host=os.getenv("MYSQL_HOST"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            database=os.getenv("MYSQL_DB"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"),
        )
    return _pool

def exec_query(query, params=None, fetch=False):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(query, params or ())
        if fetch:
            return cur.fetchall()
        conn.commit()
    finally:
        cur.close()
        conn.close()

def ensure_session(session_id: str):
    exec_query(
        "INSERT IGNORE INTO chat_session (id) VALUES (%s)",
        (session_id,)
    )

def save_message(session_id: str, role: str, content: str):
    exec_query(
        "INSERT INTO chat_message (session_id, role, content) VALUES (%s, %s, %s)",
        (session_id, role, content)
    )

def load_messages(session_id: str):
    rows = exec_query(
        "SELECT role, content, created_at FROM chat_message WHERE session_id=%s ORDER BY created_at ASC, id ASC",
        (session_id,),
        fetch=True
    )
    # Convert to Streamlit chat format
    return [{"role": r["role"], "content": r["content"], "audio": None} for r in rows]

def create_appointment(session_id: str, name: str, appt_date: str, appt_time: str):
    exec_query(
        "INSERT INTO appointment(session_id, name, appt_date, appt_time, status) VALUES(%s,%s,%s,%s,'booked')",
        (session_id, name, appt_date, appt_time)
    )

def get_latest_booked_appointment(session_id: str):
    rows = exec_query(
        "SELECT * FROM appointment WHERE session_id=%s AND status='booked' ORDER BY created_at DESC, id DESC LIMIT 1",
        (session_id,),
        fetch=True
    )
    return rows[0] if rows else None

def reschedule_latest_appointment(session_id: str, new_date: str, new_time: str):
    appt = get_latest_booked_appointment(session_id)
    if not appt:
        return False, None

    exec_query(
        "UPDATE appointment SET appt_date=%s, appt_time=%s WHERE id=%s",
        (new_date, new_time, appt["id"])
    )
    # return updated appointment
    appt["appt_date"] = new_date
    appt["appt_time"] = new_time
    return True, appt

def cancel_latest_appointment(session_id: str):
    appt = get_latest_booked_appointment(session_id)
    if not appt:
        return False, None

    exec_query(
        "UPDATE appointment SET status='cancelled', cancelled_at=NOW() WHERE id=%s",
        (appt["id"],)
    )
    return True, appt


