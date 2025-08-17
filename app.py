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
import re
import Levenshtein # เพิ่มไลบรารีสำหรับ Fuzzy Matching

# ---------------- CONFIG ----------------
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
AQICN_API = os.getenv("AQICN_API")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
app = Flask(__name__)

# ---------------- ฟังก์ชัน ----------------
def get_aqi(city):
    """ดึงค่า AQI จาก API ตามชื่อเมือง"""
    url = f"https://api.waqi.info/feed/{city}/?token={AQICN_API}"
    try:
        r = requests.get(url).json()
        if r.get('status') == 'ok':
            return r['data']['aqi']
    except requests.exceptions.RequestException as e:
        print(f"Error fetching AQI: {e}")
    return None

def assess_risk(age, smoker, family_history, symptoms, aqi):
    """ประเมินความเสี่ยงและให้คำแนะนำตามข้อมูลที่ได้รับ"""
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
        level = "ต่ำ"
        advice = "เดินทางได้ตามปกติ ดูแลสุขภาพทั่วไป"
    elif score <= 5:
        level = "ปานกลาง"
        advice = "ระวัง พกยา inhaler, ใส่หน้ากาก, หลีกเลี่ยงฝุ่น/ควัน"
    else:
        level = "สูง"
        advice = "ไม่ควรเดินทาง ควรปรึกษาแพทย์ก่อน"
    return level, advice

def is_close_match(user_text, target_keywords, threshold=2):
    """ตรวจสอบว่าข้อความของผู้ใช้ใกล้เคียงกับคำหลักที่กำหนดหรือไม่"""
    for keyword in target_keywords:
        if Levenshtein.distance(user_text, keyword) <= threshold:
            return True
    return False

# ---------------- QuickReply ----------------
def get_smoker_qr():
    """สร้าง QuickReply สำหรับคำถามเรื่องการสูบบุหรี่"""
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="สูบบุหรี่", text="smoker:y")),
        QuickReplyButton(action=MessageAction(label="ไม่สูบบุหรี่", text="smoker:n"))
    ])

def get_family_qr():
    """สร้าง QuickReply สำหรับคำถามเรื่องประวัติครอบครัว"""
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="มี", text="family:y")),
        QuickReplyButton(action=MessageAction(label="ไม่มี", text="family:n"))
    ])

def get_symptoms_qr():
    """สร้าง QuickReply สำหรับคำถามเรื่องอาการ"""
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="ไอ", text="อาการ:ไอ")),
        QuickReplyButton(action=MessageAction(label="จาม", text="อาการ:จาม")),
        QuickReplyButton(action=MessageAction(label="หายใจมีเสียงวี้ด", text="อาการ:หายใจมีเสียงวี้ด")),
        QuickReplyButton(action=MessageAction(label="แน่นหน้าอก", text="อาการ:แน่นหน้าอก")),
        QuickReplyButton(action=MessageAction(label="เหนื่อยง่าย", text="อาการ:เหนื่อยง่าย")),
        QuickReplyButton(action=MessageAction(label="ถัดไป", text="symptom:done"))
    ])

def get_city_qr():
    """สร้าง QuickReply สำหรับคำถามเรื่องเมือง"""
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="กรุงเทพ", text="เมือง:กรุงเทพ")),
        QuickReplyButton(action=MessageAction(label="เชียงใหม่", text="เมือง:เชียงใหม่")),
        QuickReplyButton(action=MessageAction(label="ภูเก็ต", text="เมือง:ภูเก็ต")),
        QuickReplyButton(action=MessageAction(label="ขอนแก่น", text="เมือง:ขอนแก่น"))
    ])

# ---------------- Webhook ----------------
user_data = {}  # เก็บ session ผู้ใช้

@app.route("/callback", methods=['POST'])
def callback():
    """Handle Callback จาก Line API"""
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
    """Handle ข้อความที่ผู้ใช้ส่งมา"""
    raw_text = event.message.text
    text = unicodedata.normalize('NFC', raw_text).strip().lower()  # แปลงเป็นตัวพิมพ์เล็กทั้งหมด
    user_id = event.source.user_id

    print(f"[DEBUG] user_id={user_id}, text={repr(text)}")

    # ---------------- RESET ----------------
    if is_close_match(text, ["รีเซ็ต", "reset"]):
        user_data.pop(user_id, None)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🔄 รีเซ็ตข้อมูลเรียบร้อยแล้ว\nพิมพ์ 'ประเมิน' เพื่อเริ่มใหม่")
        )
        return

    # ---------------- START ----------------
    if is_close_match(text, ["ประเมิน", "ประเมิณ"]):
        user_data[user_id] = {
            "step": "age",
            "age": None,
            "smoker": None,
            "family": None,
            "symptoms": []
        }
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="กรุณาใส่อายุของคุณ (ตัวเลข):")
        )
        return

    # ---------------- PROCESS STEPS ----------------
    if user_id not in user_data:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="กรุณาคัดลอกข้อความเดิมมาใส่อีกครั้ง")
        )
        return
        
    step = user_data[user_id]["step"]

    # ----- STEP: AGE -----
    if step == "age":
        if text.isdigit():
            user_data[user_id]["age"] = int(text)
            user_data[user_id]["step"] = "smoker"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="คุณสูบบุหรี่หรือไม่?", quick_reply=get_smoker_qr())
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาใส่อายุเป็นตัวเลขอีกครั้ง")
            )
        return

    # ----- STEP: SMOKER -----
    if step == "smoker":
        if is_close_match(text, ["smoker:y", "สูบบุหรี่", "ใช่", "สูบ"]):
            user_data[user_id]["smoker"] = True
            user_data[user_id]["step"] = "family"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ครอบครัวของคุณมีประวัติหอบหืดหรือไม่?", quick_reply=get_family_qr())
            )
        elif is_close_match(text, ["smoker:n", "ไม่สูบบุหรี่", "ไม่", "ไม่สูบ"]):
            user_data[user_id]["smoker"] = False
            user_data[user_id]["step"] = "family"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ครอบครัวของคุณมีประวัติหอบหืดหรือไม่?", quick_reply=get_family_qr())
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาเลือกจากตัวเลือกที่ให้ไว้", quick_reply=get_smoker_qr())
            )
        return

    # ----- STEP: FAMILY -----
    if step == "family":
        if is_close_match(text, ["family:y", "มี", "ใช่"]):
            user_data[user_id]["family"] = True
            user_data[user_id]["step"] = "symptoms"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="เลือกอาการของคุณ (เลือกได้หลายครั้ง กด 'ถัดไป' เมื่อเสร็จ):",
                    quick_reply=get_symptoms_qr()
                )
            )
        elif is_close_match(text, ["family:n", "ไม่มี", "ไม่"]):
            user_data[user_id]["family"] = False
            user_data[user_id]["step"] = "symptoms"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="เลือกอาการของคุณ (เลือกได้หลายครั้ง กด 'ถัดไป' เมื่อเสร็จ):",
                    quick_reply=get_symptoms_qr()
                )
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาเลือกจากตัวเลือกที่ให้ไว้", quick_reply=get_family_qr())
            )
        return

    # ----- STEP: SYMPTOMS -----
    if step == "symptoms":
        if text.startswith("อาการ:"):
            symptom = text.replace("อาการ:", "").strip()
            if symptom and symptom not in user_data[user_id]["symptoms"]:
                user_data[user_id]["symptoms"].append(symptom)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=f"✅ เพิ่มอาการ: {symptom}\nเลือกอาการอื่นต่อ หรือกด 'ถัดไป' เมื่อเสร็จ:",
                    quick_reply=get_symptoms_qr()
                )
            )
        elif is_close_match(text, ["symptom:done", "ถัดไป", "เสร็จสิ้น"]):
            user_data[user_id]["step"] = "city"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="เลือกเมืองที่จะไป:", quick_reply=get_city_qr())
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาเลือกอาการจากตัวเลือก หรือกด 'ถัดไป'", quick_reply=get_symptoms_qr())
            )
        return

    # ----- STEP: CITY -----
    if step == "city":
        city_mapping = {
            "เมือง:กรุงเทพ": "กรุงเทพ",
            "เมือง:เชียงใหม่": "เชียงใหม่",
            "เมือง:ภูเก็ต": "ภูเก็ต",
            "เมือง:ขอนแก่น": "ขอนแก่น"
        }
        
        city = None
        if text in city_mapping:
            city = city_mapping[text]
        elif is_close_match(text, ["กรุงเทพ", "เชียงใหม่", "ภูเก็ต", "ขอนแก่น", "bangkok", "chiang mai", "phuket", "khon kaen"]):
            # ตัวอย่างการใช้ fuzzy matching กับชื่อเมืองโดยตรง
            if "กรุงเทพ" in text: city = "กรุงเทพ"
            elif "เชียงใหม่" in text: city = "เชียงใหม่"
            elif "ภูเก็ต" in text: city = "ภูเก็ต"
            elif "ขอนแก่น" in text: city = "ขอนแก่น"
            else: # ใช้ Levenshtein เพื่อหาเมืองที่ใกล้เคียงที่สุด
                target_cities = ["กรุงเทพ", "เชียงใหม่", "ภูเก็ต", "ขอนแก่น"]
                closest_city = min(target_cities, key=lambda c: Levenshtein.distance(text, c.lower()))
                if Levenshtein.distance(text, closest_city.lower()) <= 2:
                    city = closest_city
        
        if city:
            data = user_data.get(user_id)
            if not data:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="ข้อมูลของคุณหายไป กรุณาเริ่มใหม่ด้วยการพิมพ์ 'ประเมิน'")
                )
                return

            aqi = get_aqi(city)
            level, advice = assess_risk(data["age"], data["smoker"], data["family"], data["symptoms"], aqi)

            reply = f"""
📌 แบบประเมินความเสี่ยงโรคหอบหืด
อายุ: {data['age']}
สูบบุหรี่: {"ใช่" if data['smoker'] else "ไม่ใช่"}
ครอบครัว: {"มี" if data['family'] else "ไม่มี"}
อาการ: {', '.join(data['symptoms']) if data['symptoms'] else "ไม่มี"}

🌫 AQI ({city}): {aqi if aqi is not None else "ไม่สามารถดึงค่าได้"}

⚠️ ระดับความเสี่ยง: {level}
💡 คำแนะนำ: {advice}
"""
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            user_data.pop(user_id, None)  # เคลียร์ session หลังส่งผล
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ กรุณาเลือกเมืองจากตัวเลือก", quick_reply=get_city_qr())
            )
        return