from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction
)
import requests
import os
import unicodedata
import Levenshtein
import redis
import json

# ---------------- CONFIG ----------------
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
AQICN_API = os.getenv("AQICN_API")
REDIS_URL = os.getenv("REDIS_URL")  # เช่น redis://:password@host:port

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
app = Flask(__name__)

# ---------------- Redis ----------------
r = redis.from_url(REDIS_URL, decode_responses=True)

# ---------------- ฟังก์ชัน ----------------
def get_aqi(city):
    url = f"https://api.waqi.info/feed/{city}/?token={AQICN_API}"
    try:
        r_api = requests.get(url).json()
        if r_api.get('status') == 'ok':
            return r_api['data']['aqi']
    except Exception as e:
        print(f"Error fetching AQI: {e}")
    return None

def assess_risk(age, smoker, family_history, symptoms, aqi):
    score = 0
    if age < 12 or age > 60:
        score += 1
    if smoker:
        score += 2
    if family_history:
        score += 2
    score += len(symptoms)
    if aqi is not None and aqi > 100:
        score += 2

    if score <= 2:
        return "ต่ำ", "เดินทางได้ตามปกติ ดูแลสุขภาพทั่วไป"
    elif score <= 5:
        return "ปานกลาง", "ระวัง พกยา inhaler, ใส่หน้ากาก, หลีกเลี่ยงฝุ่น/ควัน"
    else:
        return "สูง", "ไม่ควรเดินทาง ควรปรึกษาแพทย์ก่อน"

def is_close_match(user_text, target_keywords, threshold=2):
    for keyword in target_keywords:
        if Levenshtein.distance(user_text, keyword) <= threshold:
            return True
    return False

# ---------------- QuickReply ----------------
def get_smoker_qr():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="สูบบุหรี่", text="smoker:y")),
        QuickReplyButton(action=MessageAction(label="ไม่สูบบุหรี่", text="smoker:n"))
    ])

def get_family_qr():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="มี", text="family:y")),
        QuickReplyButton(action=MessageAction(label="ไม่มี", text="family:n"))
    ])

def get_symptoms_qr():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="ถัดไป", text="symptom:done")),
        QuickReplyButton(action=MessageAction(label="ไอ", text="อาการ:ไอ")),
        QuickReplyButton(action=MessageAction(label="จาม", text="อาการ:จาม")),
        QuickReplyButton(action=MessageAction(label="หายใจมีเสียงวี้ด", text="อาการ:หายใจมีเสียงวี้ด")),
        QuickReplyButton(action=MessageAction(label="แน่นหน้าอก", text="อาการ:แน่นหน้าอก")),
        QuickReplyButton(action=MessageAction(label="เหนื่อยง่าย", text="อาการ:เหนื่อยง่าย"))
        
    ])

def get_city_qr():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="กรุงเทพมหานคร", text="เมือง:กรุงเทพมหานคร")),
        QuickReplyButton(action=MessageAction(label="ชลบุรี", text="เมือง:ชลบุรี")),
        QuickReplyButton(action=MessageAction(label="กาญจนบุรี", text="เมือง:กาญจนบุรี")),
        QuickReplyButton(action=MessageAction(label="ภูเก็ต", text="เมือง:ภูเก็ต")),
        QuickReplyButton(action=MessageAction(label="เชียงใหม่", text="เมือง:เชียงใหม่")),
        QuickReplyButton(action=MessageAction(label="ประจวบคีรีขันธ์", text="เมือง:ประจวบคีรีขันธ์")),
        QuickReplyButton(action=MessageAction(label="เพชรบุรี", text="เมือง:เพชรบุรี")),
        QuickReplyButton(action=MessageAction(label="พระนครศรีอยุธยา", text="เมือง:พระนครศรีอยุธยา")),
        QuickReplyButton(action=MessageAction(label="สุราษฎร์ธานี", text="เมือง:สุราษฎร์ธานี")),
        QuickReplyButton(action=MessageAction(label="นครราชสีมา", text="เมือง:นครราชสีมา")),
        QuickReplyButton(action=MessageAction(label="กระบี่", text="เมือง:กระบี่")),
        QuickReplyButton(action=MessageAction(label="เชียงราย", text="เมือง:เชียงราย")),
        QuickReplyButton(action=MessageAction(label="สงขลา", text="เมือง:สงขลา")),
    ])

# ---------------- Mapping ไทย → อังกฤษ ----------------
city_map = {
    "กรุงเทพมหานคร": "Bangkok",
    "ชลบุรี": "Chonburi",
    "กาญจนบุรี": "Kanchanaburi",
    "ภูเก็ต": "Phuket",
    "เชียงใหม่": "Chiang Mai",
    "ประจวบคีรีขันธ์": "Prachuap Khiri Khan",
    "เพชรบุรี": "Phetchaburi",
    "พระนครศรีอยุธยา": "Ayutthaya",
    "สุราษฎร์ธานี": "Surat Thani",
    "นครราชสีมา": "Nakhon Ratchasima",
    "กระบี่": "Krabi",
    "เชียงราย": "Chiang Rai",
    "สงขลา": "Songkhla"
}

# ---------------- Webhook ----------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ---------------- Event Handler ----------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    raw_text = event.message.text
    text = unicodedata.normalize('NFC', raw_text).strip().lower()
    user_id = event.source.user_id

    # ---------------- RESET ----------------
    if is_close_match(text, ["รีเซ็ต", "reset"]):
        r.delete(user_id)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🔄 รีเซ็ตข้อมูลเรียบร้อยแล้ว\nพิมพ์ 'ประเมิน' เพื่อเริ่มใหม่ ครับ/ค่ะ")
        )
        return

    # ---------------- START ----------------
    if is_close_match(text, ["ประเมิน", "ประเมิณ"]):
        user_data = {"step":"age","age":None,"smoker":None,"family":None,"symptoms":[]}
        r.set(user_id, json.dumps(user_data))
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="กรุณาใส่อายุของคุณ (ตัวเลข) ครับ/ค่ะ:"))
        return

    # ---------------- LOAD SESSION ----------------
    data_json = r.get(user_id)
    if not data_json:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="พิมพ์ 'ประเมิน' เพื่อเริ่มทำแบบสอบถาม ครับ/ค่ะ"))
        return
    user_data = json.loads(data_json)
    step = user_data.get("step")

    # ----- STEP AGE -----
    if step=="age":
        if text.isdigit():
            user_data["age"]=int(text)
            user_data["step"]="smoker"
            r.set(user_id, json.dumps(user_data))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="คุณสูบบุหรี่หรือไม่ ครับ/ค่ะ", quick_reply=get_smoker_qr()))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ กรุณาใส่อายุเป็นตัวเลขอีกครั้ง ครับ/ค่ะ"))
        return

    # ----- STEP SMOKER -----
    if step == "smoker":
        if text in ["smoker:y", "สูบบุหรี่", "ใช่", "สูบ"]:
            user_data["smoker"] = True
        elif text in ["smoker:n", "ไม่สูบบุหรี่", "ไม่", "ไม่สูบ"]:
            user_data["smoker"] = False
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาเลือกจากตัวเลือก ครับ/ค่ะ", quick_reply=get_smoker_qr())
            )
            return
        user_data["step"] = "family"
        r.set(user_id, json.dumps(user_data))
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="ครอบครัวของคุณมีประวัติหอบหืดหรือไม่ ครับ/ค่ะ", quick_reply=get_family_qr())
        )
        return

    # ----- STEP FAMILY -----
    if step == "family":
        if text in ["family:y", "มี", "ใช่"]:
            user_data["family"] = True
        elif text in ["family:n", "ไม่มี", "ไม่"]:
            user_data["family"] = False
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาเลือกจากตัวเลือก ครับ/ค่ะ", quick_reply=get_family_qr())
            )
            return
        user_data["step"] = "symptoms"
        r.set(user_id, json.dumps(user_data))
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="เลือกอาการของคุณ หรือในกรณีที่ไม่มีอาการ ให้กดปุ่ม “ถัดไป” เพื่อเข้าสู่ขั้นตอนถัดไป ครับ/ค่ะ:",
                quick_reply=get_symptoms_qr()
            )
        )
        return

    # ----- STEP SYMPTOMS -----
    if step=="symptoms":
        if text.startswith("อาการ:"):
            symptom=text.replace("อาการ:","").strip()
            if symptom and symptom not in user_data["symptoms"]:
                user_data["symptoms"].append(symptom)
            r.set(user_id, json.dumps(user_data))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ เพิ่มอาการ: {symptom}\nเลือกอาการอื่นต่อ หรือในกรณีที่ไม่มีอาการอื่น ให้กดปุ่ม “ถัดไป” เพื่อเข้าสู่ขั้นตอนถัดไป ครับ/ค่ะ: ", quick_reply=get_symptoms_qr()))
            return
        elif is_close_match(text, ["symptom:done","ถัดไป","เสร็จสิ้น"]):
            user_data["step"]="city"
            r.set(user_id, json.dumps(user_data))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เลือกเมืองที่จะไป ครับ/ค่ะ:", quick_reply=get_city_qr()))
            return
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ กรุณาเลือกอาการจากตัวเลือก หรือกด 'ถัดไป' ครับ/ค่ะ", quick_reply=get_symptoms_qr()))
            return

    # ----- STEP CITY -----
    if step=="city":
        cities=list(city_map.keys())
        city=None
        for c in cities:
            if c in text:
                city=c
                break
        if not city:
            closest=min(cities,key=lambda x: Levenshtein.distance(text,x.lower()))
            if Levenshtein.distance(text,closest.lower())<=2:
                city=closest
        if city:
            aqi=get_aqi(city_map.get(city, city))
            level, advice=assess_risk(user_data["age"],user_data["smoker"],user_data["family"],user_data["symptoms"],aqi)
            reply=f"""
📌 แบบประเมินความเสี่ยงโรคหอบหืด
อายุ: {user_data['age']}
สูบบุหรี่: {"ใช่" if user_data['smoker'] else "ไม่ใช่"}
ครอบครัว: {"มี" if user_data['family'] else "ไม่มี"}
อาการ: {', '.join(user_data['symptoms']) if user_data['symptoms'] else "ไม่มี"}

🌫 AQI ({city}): {aqi if aqi is not None else "ไม่สามารถดึงค่าได้"}

⚠️ ระดับความเสี่ยง: {level}
💡 คำแนะนำ: {advice}
"""
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            r.delete(user_id)  # ลบ session หลังส่งผล
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ กรุณาเลือกเมืองจากตัวเลือก", quick_reply=get_city_qr()))
        return
