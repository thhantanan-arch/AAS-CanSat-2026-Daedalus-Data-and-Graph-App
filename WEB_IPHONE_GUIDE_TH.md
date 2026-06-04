# วิธีใช้ CFDS บน iPhone แบบ Web App

เวอร์ชันนี้เพิ่ม `streamlit_app.py` เพื่อให้ CFDS เปิดผ่าน browser ได้ โดยยังใช้ graph engine เดิม (`run_worker.py`, `log_normalizer.py`, `generate_all_cansat_graphs.py`) ไม่ได้ใช้ PyQt6 บน iPhone โดยตรง

## 1) รันบนคอมก่อน

### Windows
```bat
run_web_windows.bat
```

หรือรันเอง:
```bat
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

### macOS / Linux
```bash
chmod +x run_web_mac_linux.sh
./run_web_mac_linux.sh
```

หรือรันเอง:
```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

## 2) เปิดบน iPhone ใน Wi‑Fi เดียวกัน

หา IP ของคอม เช่น `192.168.1.25` แล้วเปิด Safari บน iPhone:

```text
http://192.168.1.25:8501
```

ถ้าเปิดไม่ได้ ให้เช็กว่า iPhone กับคอมอยู่ Wi‑Fi เดียวกัน และ firewall ของคอมไม่ได้ block port 8501

## 3) ทำเป็นไอคอนบน Home Screen

ใน Safari:

```text
Share → Add to Home Screen → Add
```

## 4) เอาขึ้นออนไลน์จริง

วิธีง่ายสุด:

1. สร้าง GitHub repo
2. อัปโหลดโฟลเดอร์นี้ทั้งหมด
3. ไปที่ Streamlit Community Cloud
4. เลือก repo
5. Main file = `streamlit_app.py`
6. Deploy

จากนั้นเอา URL ไปเปิดบน iPhone แล้ว Add to Home Screen ได้

## 5) Desktop version ยังอยู่ไหม

ยังอยู่ แต่ requirements หลักถูกเปลี่ยนเป็น web version แล้ว
ถ้าจะรัน desktop PyQt6 ให้ใช้:

```bash
pip install -r requirements_desktop.txt
python run_app.py
```

## หมายเหตุ

- `Quick preview` จะ generate altitude preview ก่อน เหมาะกับ iPhone/ทดสอบเร็ว
- `Full export` จะ generate graph families ทั้งหมด และอาจหนักกว่า ขึ้นกับเครื่องหรือ cloud ที่ใช้
- Download ZIP จะรวม graph outputs + diagnostics + flight report
