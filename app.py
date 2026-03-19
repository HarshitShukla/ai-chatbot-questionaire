import os
from idlelib.debugger import Debugger

import streamlit as st
import requests
import io
import json
import base64
import pandas as pd
from google import genai
from streamlit_lottie import st_lottie
from gtts import gTTS
from fpdf import FPDF
from datetime import datetime
from streamlit_gsheets import GSheetsConnection

# --- SETUP ---
st.set_page_config(page_title="Sirosri AI", page_icon="🤖")

# The genai client/chat objects hold onto a network connection.  Streamlit
# reruns the script frequently, which can leave an instance in a "closed"
# state when it is stored in session_state.  The mysterious "client closed"
# exception comes from trying to reuse such an object.  To avoid it we create
# the client/chat on demand and recreate them if they are missing or have been
# closed.


def _init_genai():
    """Return a fresh (or cached) chat instance.

    The Chat object is stored in :data:`st.session_state` so that the history
    is preserved between reruns, but the underlying http transport may be
    closed by the library if Streamlit tears down the state.  This helper
    ensures we always have a usable object.
    """
    if "_genai_client" not in st.session_state:
        st.session_state._genai_client = genai.Client(
            api_key=st.secrets["GEMINI_API_KEY"])

    # if the chat instance is missing (first run) or we've detected a send
    # failure previously, rebuild it.  For simplicity we just recreate whenever
    # it isn't present.
    if "chat" not in st.session_state:
        st.session_state.chat = st.session_state._genai_client.chats.create(
            model="gemini-2.5-flash")

    return st.session_state.chat

# Securely load API Key from Secrets
# try:
#    conn = st.connection("gsheets", type=GSheetsConnection)
# except Exception as e:
#    st.error("Setup Error: Check your secrets.toml gsheet file!")


def load_lottie(filename):
    try:
        with open(filename, 'r') as file:
            return json.load(file)

    except FileNotFoundError:
        print(f"Error: The file '{filename}' was not found.")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON from the file (malformed JSON).")


def save_conversation_as_csv(session_state):
    new_row = pd.DataFrame([session_state.answers])
    new_row['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Also save to a backup CSV file locally for data safety
    try:
        backup_file = f"interview_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        new_row.to_csv(backup_file, index=False)
        st.info(f"✅ Data backed up locally as {backup_file}")
    except Exception as backup_err:
        st.warning(f"⚠️ Local backup failed: {str(backup_err)}")

    return new_row.to_csv(index=False)

def save_conversation_as_json(session_state):
    """Convert session state (answers and history) to JSON format"""
    data = {
        "answers": session_state.answers,
        "history": session_state.history,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return json.dumps(data, indent=2)

# Assets
ANIM_URL = "animebot.json"

SURVEY_CONFIG = {
    "system_prompt": """
        You are Sirosri, a friendly AI interviewer. 
        Your goal is to talk to founders and figure out following-
        1) business ideas
            1.1) brand/product
            1.2) purpose of business
            1.3) story behind the product
            1.4) current challenges
        2) customers / target audience
            2.1) possible first customer
            2.2) customer problem area we are trying to solve
            2.3) what you want for customer
            2.4) targetting to online/offline user
        3) founders's lens 
            3.1) If someone asks “what do you do,” how do you want to answer? 
            3.2) What do you want to be known for as a founder?
            3.3) Share 1–2 personal stories that you think connect to your product.
            3.4) Is there anything you absolutely don’t want your brand to say or look like?
        4) Branding & Product Personality
            4.1) Which of these feels closest to you: bold, helpful, visionary, fun, serious, empathetic?
            4.2) Do you admire any brand — big or small — for how they present themselves?
            4.3) If your product was a person, how would they talk? (warm, smart, cheeky, calm?)
        5) Future Vision and Constraints
            5.1) What’s the one outcome you’d like to see in the next 30 days?
            5.2) How much time and money can you put behind this right now?
            5.3) Do you want us to show your story publicly (as testimonials, case studies)?
            5.4) Where do you want all the deliverables stored (Google Docs, Notion, Figma)?
        6) Feedback & Iterations
            6.1) How do you like to give feedback — written, calls, quick yes/no?
            6.2) Who else (if anyone) will review before approval?
            6.3) What’s your review timeline comfort — fast and rough, or slow and polished?
        
        Use natural conversation. Ask one question at a time based on their responses. 
        Start with a greeting and ask about their name or interests. 
        Follow up with deeper questions based on what they share. 
        Be conversational and show genuine interest.
        Wrap up all of it between 35 to 40 questions and wrap upwith a warm closing and next steps.
        """,
    "max_questions": 40,
}

# --- SESSION LOGIC ---
if "step" not in st.session_state:
    st.session_state.step, st.session_state.answers, st.session_state.history = 0, {}, []
    # Initialize conversation with system context
    st.session_state.chat_context = SURVEY_CONFIG["system_prompt"]
    # make sure we have a working chat instance for the very first question
    _init_genai()

# --- UI ---
with st.sidebar:
    st_lottie(load_lottie(ANIM_URL), height=150)
    st.title("Sirosri")
    if st.button("🔄 Restart"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
    if st.button("📥 Download Data"):
        save_conversation_as_json(st.session_state);

# --- INTERVIEW FLOW ---
if st.session_state.step < SURVEY_CONFIG["max_questions"]:
    # Check if we need to generate AI response
    need_ai_response = not st.session_state.history or st.session_state.history[-1]["role"] == "user"
    audio_bytes = None

    if need_ai_response:
        if len(st.session_state.history) == 0:
            # First message - ask opening question
            prompt = f"{SURVEY_CONFIG['system_prompt']} Start the conversation now with a warm greeting and opening question."
        else:
            # Follow-up: generate next question based on user's response
            user_message = st.session_state.history[-1]["content"]
            prompt = f"{SURVEY_CONFIG['system_prompt']}\n\nUser just said: \"{user_message}\"\n\nAsk a thoughtful follow-up question based on what they shared. Keep it natural and conversational."

        response = None
        try:
            response = _init_genai().send_message(prompt).text
        except Exception as err:
            try:
                st.warning("⚠️ Reinitializing AI client due to connection issue…")
                response = _init_genai().send_message(prompt).text
            except Exception as retry_err:
                st.error(f"❌ Error generating response: {str(retry_err)}\n\nYour responses have been saved. Please refresh or try again in a moment.")
                st.stop()

        if response:
            st.session_state.history.append(
                {"role": "assistant", "content": response})

            # # Audio - generate but store for display (handle errors gracefully)
            # try:
            #     tts = gTTS(text=response, lang='en')
            #     fp = io.BytesIO()
            #     tts.write_to_fp(fp)
            #     audio_bytes = fp.getvalue()
            # except Exception as audio_err:
            #     st.warning(f"⚠️ Audio generation failed: {str(audio_err)}")
    # Display all messages
    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # Play audio if we just generated it
    if audio_bytes:
        st.audio(audio_bytes, format="audio/mp3", autoplay=True)

    # User Input - no explicit rerun needed
    u_text = st.chat_input("Type here...")

    if u_text:
        try:
            # Save answer immediately to prevent data loss
            st.session_state.answers[f"response_{st.session_state.step}"] = u_text
            st.session_state.history.append(
                {"role": "user", "content": u_text})
            st.session_state.step += 1
            # Streamlit automatically reruns when user submits input - no explicit st.rerun() needed
            st.rerun()
        except Exception as input_err:
            st.error(
                f"❌ Error processing your response: {str(input_err)}\n\nPlease try typing again. Your previous responses are saved.")
            st.stop()
else:
    # --- ENDING & DATABASE ---
    st.success("✅ Interview Complete!")
    if "saved" not in st.session_state:
        try:
            st.session_state.saved = True

            # --- DOWNLOAD BUTTONS ---
            st.subheader("📥 Download Your Data")
            col1, col2 = st.columns(2)

            with col1:
                csv_data = save_conversation_as_csv(st.session_state)
                st.download_button(
                    label="📊 Download as CSV",
                    data=csv_data,
                    file_name=f"interview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )

            with col2:
                json_data = save_conversation_as_json(st.session_state)
                st.download_button(
                    label="📄 Download as JSON",
                    data=json_data,
                    file_name=f"interview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )

            st.subheader("📋 Your Responses:")
            st.json(st.session_state.answers)

            st.subheader("📝 Interview Conversation:")
            st.json(st.session_state.history)

        except Exception as save_err:
            st.error(f"❌ Error finalizing interview: {str(save_err)}")
            st.warning("⚠️ Your responses are still displayed below. Please manually save or refresh.")
            st.subheader("📋 Your Responses (unsaved):")
            st.json(st.session_state.answers)
