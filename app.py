from flask import Flask, request, Response, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from anthropic import Anthropic
import json, os, re, requests as req
from datetime import datetime

app = Flask(__name__)
claude = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

SYSTEM_PROMPT = """You are Noor, AI receptionist of Madina Sonography hospital.
Detect language and reply in same language (Urdu/Hindi/English/Marathi). Default Urdu.
One question at a time. Be polite.
STEP 1: Ask full name
STEP 2: Ask sonography type: Pregnancy, Abdominal, Thyroid, Doctor referral
STEP 3: Ask day: Aaj/Kal/Parson
STEP 4: Confirm slot. Today:9,9:30,10,11:30,2,3:30,5. Tomorrow:9,10:30,12,2:30,4,6. Day after:8:30,10,11,1:30,3,5:30
STEP 5: Output: BOOK:{"naam":"X","type":"X","din":"X","waqt":"X","token":55}"""

sessions = {}

@app.route("/incoming-call", methods=["POST"])
def incoming_call():
    sid = request.form.get("CallSid")
    caller = request.form.get("From", "")
    sessions[sid] = {"caller": caller, "history": [], "done": False}
    resp = VoiceResponse()
    g = Gather(input="speech", action=f"/handle?sid={sid}", method="POST", language="ur-PK", speech_timeout="auto", timeout=8)
    g.say("Assalamu Alaikum! Madina Sonography mein khush aamdeed. Main Noor hoon. Aap Hindi Urdu English ya Marathi mein baat kar sakte hain. Apna naam batayein.", language="ur-PK", voice="Google.ur-PK-Standard-A")
    resp.append(g)
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")

@app.route("/handle", methods=["POST"])
def handle():
    sid = request.args.get("sid")
    speech = request.form.get("SpeechResult", "").strip()
    resp = VoiceResponse()
    if sid not in sessions:
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")
    session = sessions[sid]
    if not speech:
        g = Gather(input="speech", action=f"/handle?sid={sid}", method="POST", language="ur-PK", speech_timeout="auto", timeout=8)
        g.say("Dobara bataiye.", language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.append(g)
        return Response(str(resp), mimetype="text/xml")
    session["history"].append({"role": "user", "content": speech})
    ai = get_reply(session["history"])
    session["history"].append({"role": "assistant", "content": ai})
    match = re.search(r'BOOK:(\{.*?\})', ai)
    clean = re.sub(r'BOOK:\{.*?\}', '', ai).strip()
    if match and not session["done"]:
        session["done"] = True
        try:
            appt = json.loads(match.group(1))
            sms(session["caller"], appt)
            save(sid, session["caller"], appt)
        except Exception as e:
            print(e)
        resp.say(clean, language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.hangup()
    else:
        g = Gather(input="speech", action=f"/handle?sid={sid}", method="POST", language="ur-PK", speech_timeout="auto", timeout=8)
        g.say(clean, language="ur-PK", voice="Google.ur-PK-Standard-A")
        resp.append(g)
        resp.hangup()
    return Response(str(resp), mimetype="text/xml")

def get_reply(history):
    try:
        r = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=300, system=SYSTEM_PROMPT, messages=history)
        return r.content[0].text
    except Exception as e:
        print(e)
        return "Maafi, masla aa gaya. Dobara bataiye."

def sms(to, appt):
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
        body = f"Madina Sonography\nNaam: {appt.get('naam')}\nType: {appt.get('type')}\nDin: {appt.get('din')}\nWaqt: {appt.get('waqt')}\nToken: #{appt.get('token')}"
        req.post(url, data={"From": TWILIO_NUMBER, "To": to, "Body": body}, auth=(TWILIO_SID, TWILIO_TOKEN))
    except Exception as e:
        print(e)

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
        print(e)

@app.route("/")
def home():
    return "Madina Sonography Online!"

@app.route("/appointments")
def appointments():
    try:
        if os.path.exists("appointments.json"):
            with open("appointments.json") as f:
                return jsonify(json.load(f))
    except:
        pass
    return jsonify([])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
