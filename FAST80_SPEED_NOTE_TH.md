# CFDS Fast 80 Speed Build

เวอร์ชันนี้เพิ่ม `Fast 80 mode` สำหรับมือถือ:

- ข้ามการสร้าง SVG เพื่อประหยัดเวลา
- ลด PNG export จาก 300 dpi เป็น 160 dpi
- เหมาะกับการ preview/export 80 รูปบน Streamlit Cloud และ iPhone
- ถ้าต้องการไฟล์คุณภาพสูงสำหรับ final report ให้เลือก `Full quality mode` ใน sidebar

อัปเดต GitHub อย่างน้อย 3 ไฟล์นี้:

```txt
streamlit_app.py
run_worker.py
generate_all_cansat_graphs.py
```
