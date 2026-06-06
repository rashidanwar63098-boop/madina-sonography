from flask import Flask, request, Response, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from anthropic import Anthropic
import json, os, re
from datetime import datetime

app = Flask(__name__)
claude = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

SYSTEM_PROMPT = """
You are Noor, AI receptionist of Madina Sonography hospital.

LANGUAGE RULE: Detect what language the patient speaks and reply in SAME language.
- If Urdu → reply in Urdu
- If Hindi → reply in Hindi
- If English → reply in English
- If Marathi → reply in Marathi
- Default → Urdu

YOUR STEPS (one question at a time):
STEP 1: Ask patient's full name
STEP 2: Ask sonography type: Pregnancy/Hamal, Abdominal/Pet, Thyroid, Doctor referral
STEP 3: Ask preferred day: Today/Aaj, Tomorrow/Kal, Day after/Parson
STEP 4: Confirm time slot:
  Today: 9am, 9:30, 10am, 11:30, 2pm, 3:30, 5pm
  Tomorrow: 9am, 10:30, 12pm, 2:30, 4pm, 6pm
  Day after: 8:30, 10am, 11am, 1:30, 3pm, 5:30
STEP 5: Confirm appointment and output exactly:
  BOOK:{"naam":"...","type":"...","din":"...","waqt":"...","token":TOKEN_NUMBER}
  Use random token between 40-99.

Rules:
- SHORT answers only (phone call)
- Very polite: Ji, Shukriya, Zaroor, Bilkul
- One question at a time only
"""

sessions = {}

@app.route("/incoming-call", methods=["POST"])
def incoming_call():
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From", "Unknown")
    sessions[call_sid] = {"caller": caller, "history": [], "done": False}
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"/handle-speech?sid={call_sid}",
        method="POST",
        language="ur-PK",
        speech_timeout="auto",
        timeout=8
    )
    gather.say(
        "Assalamu Alaikum! Madina Sonography mein aapka khush aamdeed. "
        "Main Noor hoon. Aap Hindi, Urdu, English ya Marathi mein baat kar sakte hain. "
        "Meherbani farmakar apna naam batayein.",
        language="ur-PK",
        voice="Google.ur-PK-Standard-A"
    )
    resp.append(gather)
    resp.say("Koi jawab nahi mila. Dobara call karein.", language="ur-PK")
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")

@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    call_sid = request.args.get("sid")
    speech = request.form.get("SpeechResult", "").strip()
    confidence = float(request.form.get("Confidence", 0.5))
    resp = VoiceResponse()

    if call_sid not in sessions:
        resp.say("Sorry, please call again.", language="en-US")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    session = sessions[call_sid]

    if not speech or confidence < 0.25:
        gather = Gather(
            input="speech",
            action=f"/handle-speech?sid={call_sid}",
            method="POST",
            language="ur-PK",
            speech_timeout="auto",
            timeout=8
        )
        gather.say("Maafi, samajh nahi aaya. Dobara bataiye.", language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.append(gather)
        return Response(str(resp), mimetype="text/xml")

    session["history"].append({"role": "user", "content": speech})
    ai_text = get_claude_reply(session["history"])
    session["history"].append({"role": "assistant", "content": ai_text})

    match = re.search(r'BOOK:(\{.*?\})', ai_text)
    clean_text = re.sub(r'BOOK:\{.*?\}', '', ai_text).strip()

    if match and not session["done"]:
        session["done"] = True
        try:
            appt = json.loads(match.group(1))
            send_sms(session["caller"], appt)
            save(call_sid, session["caller"], appt)
        except Exception as e:
            print(f"Error: {e}")
        resp.say(clean_text, language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.hangup()
    else:
        gather = Gather(
            input="speech",
            action=f"/handle-speech?sid={call_sid}",
            method="POST",
            language="ur-PK",
            speech_timeout="auto",
            timeout=8
        )
        gather.say(clean_text, language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.append(gather)
        resp.hangup()
    return Response(str(resp), mimetype="text/xml")

def get_claude_reply(history):
    try:
        r = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=history
        )
        return r.content[0].text
    except Exception as e:
        print(f"Claude error: {e}")
        return "Maafi chahiye, thoda masla aa gaya. Dobara bataiye."

def send_sms(to, appt):
    try:
        import requests as req
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
        msg = (f"Assalamu Alaikum {appt.get('naam')} ji!\n"
               f"Madina Sonography appointment confirm:\n"
               f"Type: {appt.get('type')}\n"
               f"Din: {appt.get('din')}\n"
               f"Waqt: {appt.get('waqt')}\n"
               f"Token: #{appt.get('token')}\n"
               f"Shukriya!")
        req.post(url, data={"From": TWILIO_NUMBER, "To": to, "Body": msg},
                 auth=(TWILIO_SID, TWILIO_TOKEN))
        print(f"SMS sent to {to}")
    except Exception as e:
        print(f"SMS error: {e}")

def save(sid, caller, appt):
    try:
        data = []
        if os.path.exists("appointments.json"):
            with open("appointments.json") as f:
                data = json.load(f)
        data.append({"sid": sid, "caller": caller,
                     "time": datetime.now().strftime("%Y-%m-%d %H:%M"), **appt})
        with open("appointments.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Save error: {e}")

@app.route("/appointments")
def appointments():
    try:
        if os.path.exists("appointments.json"):
            with open("appointments.json") as f:
                return jsonify(json.load(f))
    except:
        pass
    return jsonify([])

@app.route("/")
def home():
    return "Madina Sonography Voice Agent Online!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
