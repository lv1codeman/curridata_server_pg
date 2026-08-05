import io
import re
import datetime
import requests
from bs4 import BeautifulSoup
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

# 建立獨立的 Router 物件
router = APIRouter(prefix="/api", tags=["Course Query"])

# 設定台灣 Proxy 伺服器資訊
PROXY_URL = "http://114.35.132.4:37364"
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL
}

def split_course_name(name):
    """拆分中英文課程名稱"""
    if not isinstance(name, str):
        return pd.Series(["", ""])
    match = re.search(r'^(.*?)\s+([A-Za-z].*)$', name.strip())
    if match:
        return pd.Series([match.group(1).strip(), match.group(2).strip()])
    else:
        return pd.Series([name.strip(), ""])

@router.get("/course_query")
def download_courses(
    branch: str = Query(default="", description="部別(日間部D、夜間部N)"),
    year: str = Query(default="115", description="學年度"),
    semester: str = Query(default="1", description="學期"),
    clsid: str = Query(default="", description="班級代碼"),
    eng: str = Query(default="", description="全英語教學(Y,N)"),
    dis_learn: str = Query(default="", description="遠距教學(Y,N)"),
    crsid: str = Query(default="", description="課程代碼"),
    week: str = Query(default="", description="星期"),
    crsnm: str = Query(default="", description="課程名稱"),
    tchnm: str = Query(default="", description="教師名稱"),
):
    url = "https://webapt.ncue.edu.tw/deanv2/other/ob010"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://webapt.ncue.edu.tw/deanv2/other/ob010",
        "Origin": "https://webapt.ncue.edu.tw"
    }

    session = requests.Session()
    # 關鍵修改：將 Proxy 直接綁定給 Session，後續所有 requests 均會透過此 Proxy 轉發
    session.proxies.update(PROXIES)

    try:
        # 1. 取得初始頁面並自動擷取隱藏欄位 (如防偽 Token)
        init_res = session.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(init_res.text, 'html.parser')

        payload = {}
        for hidden_tag in soup.find_all("input", type="hidden"):
            name = hidden_tag.get("name")
            value = hidden_tag.get("value", "")
            if name:
                payload[name] = value

        payload.update({
            "sel_cls_branch": branch if branch else "",
            "sel_yms_year": year if year else "",
            "sel_yms_smester": semester if semester else "",
            "sel_cls_id": crsid if crsid else "",
            "sel_scr_english": eng if eng else "",
            "sel_SCR_IS_DIS_LEARN": dis_learn if dis_learn else "",
            "scr_selcode": crsid if crsid else "",
            "sel_sct_week": week if week else "",
            "sub_name": crsnm if crsnm else "",
            "emp_name": tchnm if tchnm else "",
        })

        # 2. 發送 POST 查詢請求
        search_res = session.post(url, data=payload, headers=headers, timeout=12)
        search_res.encoding = 'utf-8'

        # 3. 解析 HTML 中的表格
        dfs = pd.read_html(io.StringIO(search_res.text), flavor='lxml')
        if not dfs:
            raise HTTPException(status_code=404, detail="未抓取到課程表格資料")

        target_df = max(dfs, key=lambda df: df.shape[0] * df.shape[1])

        # 4. 拆分「課程名稱」
        course_col = next((c for c in target_df.columns if "課程名稱" in str(c) or "課名" in str(c)), None)
        if course_col is not None:
            split_df = target_df[course_col].apply(split_course_name)
            split_df.columns = ['課程中文名稱', '課程英文名稱']
            col_idx = target_df.columns.get_loc(course_col)
            
            target_df = target_df.drop(columns=[course_col])
            target_df.insert(col_idx, '課程英文名稱', split_df['課程英文名稱'])
            target_df.insert(col_idx, '課程中文名稱', split_df['課程中文名稱'])

        # 5. 在記憶體（RAM）中產生 Excel 檔
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            target_df.to_excel(writer, sheet_name='開課列表', index=False)
        
        excel_buffer.seek(0)

        # 動態產生檔名：YYYYMMDD_HHMMSS.xlsx
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{now_str}.xlsx"

        # 6. 回傳串流
        return StreamingResponse(
            excel_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

    except HTTPException:
        # 直接拋出自訂的 HTTPException (如 404)
        raise
    except requests.exceptions.RequestException as req_err:
        # 針對 Proxy 或網路逾時特化錯誤說明
        raise HTTPException(status_code=504, detail=f"Proxy 連線或請求彰師大逾時/失敗: {str(req_err)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"伺服器處理失敗: {str(e)}")