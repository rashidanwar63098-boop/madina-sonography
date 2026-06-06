from flask import Flask, request, Response, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from anthropic import Anthropic
import json, os, re
from datetime import datetime

app = Flask(__name__)
claude = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
twilio_client = TwilioClient(
    username=os.environ.get("TWILIO_ACCOUNT_SID"),
    password=os.environ.get("TWILIO_AUTH_TOKEN")
)
TWILIO_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

SYSTEM_PROMPT = """
Aap Madina Sonography hospital ki AI receptionist hain. Aapka naam Noor hai.
Aap Urdu mein baat karti hain. Hindi ya English ko samjh ker Urdu mein jawab dein.
Ek waqt mein sirf ek sawaal poochein.
STEP 1: Patient ka naam poochein
STEP 2: Sonography type poochein: Hamal, Pet ki sonography, Thyroid, Doctor referral
STEP 3: Din poochein: Aaj, Kal, ya Parson
STEP 4: Waqt confirm karein:
  Aaj: subah 9, saadhe 9, 10 baje, saadhe 11, dopahar 2, saadhe 3, sham 5
  Kal: subah 9, saadhe 10, dopahar 12, saadhe 2, sham 4, 6 baje
  Parson: subah saadhe 8, 10, 11 baje, dopahar saadhe 1, sham 3, saadhe 5
STEP 5: Confirm hone par bolein token number aur likho:
  BOOK:{"naam":"...","type":"...","din":"...","waqt":"...","token":50}
Chote jawab dein. Bahut adab se baat karein.
"""

sessions = {}

@app.route("/incoming-call", methods=["POST"])
def incoming_call():
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From", "Unknown")
    sessions[call_sid] = {"caller": caller, "history": [], "done": False}
    resp = VoiceResponse()
    gather = Gather(input="speech", action=f"/handle-speech?sid={call_sid}",
                    method="POST", language="ur-PK", speech_timeout="auto", timeout=8)
    gather.say("Assalamu Alaikum! Madina Sonography mein aapka khush aamdeed. Main Noor hoon. Meherbani farmakar apna poora naam batayein.",
               language="ur-PK", voice="Google.ur-PK-Standard-A")
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
        resp.say("Maafi chahiye, dobara call karein.", language="ur-PK")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")
    session = sessions[call_sid]
    if not speech or confidence < 0.25:
        gather = Gather(input="speech", action=f"/handle-speech?sid={call_sid}",
                        method="POST", language="ur-PK", speech_timeout="auto", timeout=8)
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
        except:
            pass
        resp.say(clean_text, language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.hangup()
    else:
        gather = Gather(input="speech", action=f"/handle-speech?sid={call_sid}",
                        method="POST", language="ur-PK", speech_timeout="auto", timeout=8)
        gather.say(clean_text, language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.append(gather)
        resp.hangup()
    return Response(str(resp), mimetype="text/xml")

def get_claude_reply(history):
    try:
        r = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=300,
                                    system=SYSTEM_PROMPT, messages=history)
        return r.content[0].text
    except:
        return "Maafi chahiye, thoda masla aa gaya. Dobara bataiye."

def send_sms(to, appt):
    try:
        twilio_client.messages.create(
            body=f"Assalamu Alaikum {appt.get('naam')} ji!\nMadina Sonography appointment confirm:\nQism: {appt.get('type')}\nDin: {appt.get('din')}\nWaqt: {appt.get('waqt')}\nToken: #{appt.get('token')}\nShukriya!",
            from_=TWILIO_NUMBER, to=to)
    except Exception as e:
        print(f"SMS error: {e}")

def save(sid, caller, appt):
    try:
        data = []
        if os.path.exists("appointments.json"):
            with open("appointments.json") as f:
                data = json.load(f)
        data.append({"sid": sid, "caller": caller, "time": datetime.now().strftime("%Y-%m-%d %H:%M"), **appt})
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
