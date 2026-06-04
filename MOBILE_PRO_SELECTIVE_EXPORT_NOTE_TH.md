# CFDS Mobile Pro Selective Export

เวอร์ชันนี้ทำมาสำหรับ iPhone/มือถือโดยเฉพาะ เป้าหมายคือไม่ต้อง generate ทุกกราฟทุกครั้ง และไม่ต้องโหลด preview 80 รูปพร้อมกันถ้าไม่จำเป็น

## เพิ่มใหม่

- Graph preset selector
  - Quick Check = Altitude + Velocity + CONOPS
  - Sensor Health = Voltage + Temperature
  - GPS Pack = GPS only
  - Motion Pack = Multi-axis / accel / gyro / tilt family
  - Report Pack = graph หลักสำหรับรายงาน
  - Full Export = ทุก family
  - Custom = เลือกเอง

- Generate selected graphs only
  - ส่ง selected family เข้า worker โดยตรง
  - ลดเวลา generate บน Streamlit Cloud/iPhone

- Session cache
  - ถ้า log เดิม + preset เดิม + quality เดิม จะใช้ผลลัพธ์เดิมใน session ได้
  - มีปุ่ม Clear session cache

- Export center upgraded
  - Full ZIP
  - PNG-only ZIP
  - Diagnostics ZIP
  - Flight report
  - Normalized CSV
  - Folder ZIP ทีละ folder
  - Individual PNG สูงสุด 80 รูป

- Folder browser
  - เลือก folder ดูทีละกลุ่ม ไม่ต้องโหลดทุก preview พร้อมกัน

- Pre-flight data check
  - เช็กคร่าว ๆ ว่ามี altitude, state, GPS, motion, packet/timebase columns หรือไม่

## GitHub update files

อัปโหลดทับไฟล์เดิมใน repo:

```txt
streamlit_app.py
run_worker.py
generate_all_cansat_graphs.py
requirements.txt
MOBILE_PRO_SELECTIVE_EXPORT_NOTE_TH.md
```

จากนั้นกด Commit changes แล้วรอ Streamlit rebuild

## iPhone recommended setting

```txt
Graph preset: Quick Check หรือ Report Pack
Export quality: Mobile Fast
Max preview images: 12-24
Show original full PNG previews: off
```

ใช้ Full Export + Report Quality เฉพาะตอนจะทำไฟล์ final จริง ๆ
