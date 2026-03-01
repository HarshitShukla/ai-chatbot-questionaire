import streamlit as st
import google.generativeai as genai
import requests, io, json, base64, pandas as pd
from streamlit_lottie import st_lottie
from gtts import gTTS
from fpdf import FPDF
from datetime import datetime
from streamlit_gsheets import GSheetsConnection

# --- SETUP ---
st.set_page_config(page_title="Sirosri AI", page_icon="🤖")

# Securely load API Key from Secrets
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel('gemini-1.5-flash')
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error("Setup Error: Check your secrets.toml file!")
    st.stop()

def load_lottie(url):
    return requests.get(url).json()

# Assets
ANIM_URL = "https://lottie.host/866d9203-39d2-4309-8088-3e477e64177d/EunE5nIqj8.json"

SURVEY_CONFIG = [
    {"id": "name", "q": "I'm Sirosri! What's your name?", "type": "personalized"},
    {"id": "phone", "q": "What is your phone number?", "type": "straight"},
    {"id": "hobby", "q": "What's a hobby that makes you lose track of time?", "type": "personalized"},
]

# --- SESSION LOGIC ---
if "step" not in st.session_state:
    st.session_state.step, st.session_state.answers, st.session_state.history = 0, {}, []
    st.session_state.chat = model.start_chat(history=[])

# --- UI ---
with st.sidebar:
    st_lottie(load_lottie(ANIM_URL), height=150)
    st.title("Sirosri")
    if st.button("🔄 Restart"): 
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()

# --- INTERVIEW FLOW ---
if st.session_state.step < len(SURVEY_CONFIG):
    if not st.session_state.history or st.session_state.history[-1]["role"] == "user":
        curr = SURVEY_CONFIG[st.session_state.step]
        prompt = f"Ask: {curr['q']}. Mode: {curr['type']}. Context: {st.session_state.answers}"
        response = st.session_state.chat.send_message(prompt).text
        st.session_state.history.append({"role": "assistant", "content": response})
        
        # Audio
        tts = gTTS(text=response, lang='en')
        fp = io.BytesIO(); tts.write_to_fp(fp)
        st.audio(fp.getvalue(), format="audio/mp3", autoplay=True)
        st.rerun()

    for msg in st.session_state.history:
        with st.chat_message(msg["role"]): st.write(msg["content"])

    # User Inputs
    u_text = st.chat_input("Type here...")
    u_audio = st.audio_input("Record voice")

    user_val = None
    if u_audio:
        with st.spinner("Sirosri is listening..."):
            res = model.generate_content(["Transcribe:", {"mime_type": "audio/wav", "data": u_audio.getvalue()}])
            user_val = res.text
    elif u_text:
        user_val = u_text

    if user_val:
        st.session_state.answers[SURVEY_CONFIG[st.session_state.step]['id']] = user_val
        st.session_state.history.append({"role": "user", "content": user_val})
        st.session_state.step += 1
        st.rerun()
else:
    # --- ENDING & DATABASE ---
    st.success("Interview Complete!")
    if "saved" not in st.session_state:
        df = conn.read(worksheet="Sheet1")
        new_row = pd.DataFrame([st.session_state.answers])
        new_row['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Sheet1", data=pd.concat([df, new_row]))
        st.session_state.saved = True
    
    st.json(st.session_state.answers)
    # (PDF & Share Link logic from previous step goes here)