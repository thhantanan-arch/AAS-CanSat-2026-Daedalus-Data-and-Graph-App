# CFDS v0.5.7 — REPORT IN-PAGE SCROLL FIX

แก้จาก v0.5.6 ตามที่ต้องการ: ไม่ต้องจูน interface ใหม่แล้ว ใช้ฐานนี้ต่อ

## แก้หลัก ๆ

- เอา Report sub choices ออกจาก sidebar
- Sidebar เหลือเฉพาะหน้าหลัก:
  - Game Start
  - Dashboard
  - Setup
  - Preview / Output
  - Flight Report
  - Export Center
  - System
- รายงานทั้งหมดอยู่ในหน้า Flight Report เดียว แล้ว scroll ในหน้านั้น
- Flight Report รวม:
  - Report Summary
  - Report Rule Checks
  - Report Regression
  - Report Advanced Stats
  - Report Definitions
  - Report Markdown
- ไม่แตะ graph engine
- ไม่แตะ preview engine
- ไม่แตะ worker/report calculation

## Run

```bash
pip install -r requirements.txt
python run_app.py
```
