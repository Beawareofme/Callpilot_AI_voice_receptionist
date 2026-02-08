import re
import streamlit as st
import uuid
from db import ensure_session, save_message, load_messages
from db import cancel_latest_appointment, create_appointment
from db import reschedule_latest_appointment

from agent import llm_reply_and_extract
from speech import speak            # must return mp3 bytes
from db import get_latest_booked_appointment


# ----------------- Config -----------------
st.set_page_config(page_title="CallPilot", page_icon="📞")
st.title("CallPilot – AI Voice Receptionist")

MAX_INPUT_CHARS = 500
MAX_TTS_CHARS = 600
MAX_LLM_TURNS = 12  # number of messages kept for LLM context (after cleaning)




# ----------------- Session State -----------------
# --- Session persistence via URL query param ---
params = st.query_params  # Streamlit >= 1.30
sid = params.get("sid", None)

if "session_id" not in st.session_state:
    if sid:
        st.session_state.session_id = sid
    else:
        st.session_state.session_id = str(uuid.uuid4())
        st.query_params["sid"] = st.session_state.session_id

# Ensure session exists in DB
ensure_session(st.session_state.session_id)

# Load persisted messages for this session
if "messages" not in st.session_state:
    st.session_state.messages = load_messages(st.session_state.session_id)




if "slots" not in st.session_state:
    st.session_state.slots = {"name": None, "date": None, "time": None}

if "booked" not in st.session_state:
    st.session_state.booked = False

if "expecting_reschedule_confirm" not in st.session_state:
    st.session_state.expecting_reschedule_confirm = False

if "pending_reschedule" not in st.session_state:
    st.session_state.pending_reschedule = {"date": None, "time": None}


if "last_booking" not in st.session_state:
    st.session_state.last_booking = None  # {"name":..,"date":..,"time":..}

# ---- Sync booked appointment from DB (so refresh remembers) ----
existing = get_latest_booked_appointment(st.session_state.session_id)
if existing:
    st.session_state.booked = True
    st.session_state.last_booking = {
        "name": existing["name"],
        "date": existing["appt_date"],
        "time": existing["appt_time"],
    }







# ----------------- Reset Button -----------------
if st.button("Reset chat"):
    new_sid = str(uuid.uuid4())
    st.session_state.session_id = new_sid
    st.query_params["sid"] = new_sid

    # reset local state
    st.session_state.messages = []
    st.session_state.slots = {"name": None, "date": None, "time": None}
    st.session_state.booked = False

    st.session_state.last_booking = None


    # ensure new session exists in DB
    ensure_session(new_sid)
    st.session_state.messages = load_messages(new_sid)

    st.rerun()



# ----------------- Helpers -----------------
def strip_basic_markdown(text: str) -> str:
    """Avoid reading markdown symbols in TTS (basic cleanup)."""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)  # **bold**
    text = re.sub(r"\*(.*?)\*", r"\1", text)      # *italic*
    text = re.sub(r"`(.*?)`", r"\1", text)        # `code`
    return text

def add_assistant_message(text: str):
    audio = None
    try:
        tts_text = strip_basic_markdown(text)[:MAX_TTS_CHARS]
        audio = speak(tts_text)
    except Exception:
        audio = None

    st.session_state.messages.append({"role": "assistant", "content": text, "audio": audio})

    # SAVE to DB
    save_message(st.session_state.session_id, "assistant", text)


def booking_details_complete(slots: dict) -> bool:
    return bool(slots.get("name") and slots.get("date") and slots.get("time"))

def clean_history_for_llm(messages: list) -> list:
    """
    Keep only role+content (no audio), limit turns, and avoid duplicating the current user message.
    IMPORTANT: We already append the user message to UI, but llm_reply_and_extract receives user_text separately.
    """
    cleaned = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("content")]
    cleaned = cleaned[-MAX_LLM_TURNS:]

    # If the last message is the current user's message, remove it from history to prevent duplication.
    if cleaned and cleaned[-1]["role"] == "user":
        cleaned = cleaned[:-1]

    return cleaned


# ----------------- Render Transcript -----------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("audio"):
            st.audio(msg["audio"], format="audio/mp3")


# ----------------- Input -----------------
user_text = st.chat_input("Type your message…")

if user_text:
    # Input length guard
    user_text = user_text.strip()
    if len(user_text) > MAX_INPUT_CHARS:
        user_text = user_text[:MAX_INPUT_CHARS]
        st.session_state.messages.append({"role": "user", "content": user_text, "audio": None})
        add_assistant_message(f"Please keep messages under {MAX_INPUT_CHARS} characters.")
        st.rerun()

    # Add user message to transcript
    st.session_state.messages.append({"role": "user", "content": user_text, "audio": None})
    save_message(st.session_state.session_id, "user", user_text)

    # If we are waiting for reschedule confirmation
    if st.session_state.get("expecting_reschedule_confirm") and user_text.lower() in ["yes", "y", "confirm", "ok", "okay"]:
        new_date = st.session_state.pending_reschedule.get("date")
        new_time = st.session_state.pending_reschedule.get("time")

        ok, appt = reschedule_latest_appointment(st.session_state.session_id, new_date, new_time)

        st.session_state.expecting_reschedule_confirm = False
        st.session_state.pending_reschedule = {"date": None, "time": None}

        if ok:
            st.session_state.last_booking = {
                "name": appt["name"],
                "date": appt["appt_date"],
                "time": appt["appt_time"],
            }
            st.session_state.booked = True
            add_assistant_message(f"Done — I rescheduled it to **{appt['appt_date']}** at **{appt['appt_time']}**.")
        else:
            add_assistant_message("I couldn’t find an active appointment in this session to reschedule.")
        st.rerun()

    if st.session_state.get("expecting_reschedule_confirm") and user_text.lower() in ["no", "n"]:
        st.session_state.expecting_reschedule_confirm = False
        st.session_state.pending_reschedule = {"date": None, "time": None}
        add_assistant_message("No problem — tell me the new date/time you prefer.")
        st.rerun()


    reschedule_keywords = ["reschedule", "move", "change the time", "change the date", "make it earlier", "make it later"]
    is_reschedule = any(k in user_text.lower() for k in reschedule_keywords)

    if is_reschedule:
    # Let LLM extract date/time as usual, but we’ll mark that we expect a confirm
        st.session_state.expecting_reschedule_confirm = True


    # --- Cancel intent (simple rule-based) ---
    cancel_keywords = ["cancel", "cancell", "call off", "delete my appointment"]
    if any(k in user_text.lower() for k in cancel_keywords):
        ok, appt = cancel_latest_appointment(st.session_state.session_id)
        if ok:
            st.session_state.booked = False
            st.session_state.last_booking = None
            msg = f"Done — I cancelled your appointment on **{appt['appt_date']}** at **{appt['appt_time']}** under **{appt['name']}**."
        else:
            msg = "I couldn’t find any active appointment to cancel in this session."
        add_assistant_message(msg)
        st.rerun()


    # ----------------- Normal LLM Mode -----------------
    llm_history = clean_history_for_llm(st.session_state.messages)

    with st.spinner("Thinking…"):
        try:
            assistant_text, extracted = llm_reply_and_extract(
                user_text=user_text,
                conversation_history=llm_history,
                current_slots=st.session_state.slots,
            )
        except Exception as e:
            assistant_text, extracted = (f"Sorry — I hit an AI error. ({e})", {})

    # Update slots safely
    extracted = extracted or {}
    for k in ["name", "date", "time"]:
        val = extracted.get(k)
        if isinstance(val, str) and val.strip():
            st.session_state.slots[k] = val.strip()
     
    # If user asked to reschedule and we have date/time extracted, ask confirmation
    if st.session_state.expecting_reschedule_confirm:
        if st.session_state.slots.get("date") and st.session_state.slots.get("time"):
            st.session_state.pending_reschedule = {
                "date": st.session_state.slots["date"],
                "time": st.session_state.slots["time"],
            }
            add_assistant_message(
                f"Just to confirm — do you want to reschedule your appointment to **{st.session_state.slots['date']}** at **{st.session_state.slots['time']}**? Reply **yes** or **no**."
            )
            st.rerun()


    # Add assistant response to transcript (+ audio)
    add_assistant_message(assistant_text)

   
# ----------------- Booking Trigger (once) -----------------
    if booking_details_complete(st.session_state.slots) and not st.session_state.booked:
        booking = {
            "name": st.session_state.slots["name"],
            "date": st.session_state.slots["date"],
            "time": st.session_state.slots["time"],
        }

        # Book once (idempotent via st.session_state.booked)
        try:
            create_appointment(
                st.session_state.session_id,
                booking["name"],
                booking["date"],
                booking["time"]
            )
        except Exception:
            pass


        st.session_state.booked = True
        st.session_state.last_booking = booking

       

    st.rerun()
