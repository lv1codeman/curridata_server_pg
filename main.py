# 引入您提供的 MSSQL 資料庫輔助函數和例外
from database_helper import execute_query, DatabaseError, UniqueConstraintError, DatabaseCursor
import time
import tempfile
import os
import shutil
import uuid
from urllib.parse import quote, unquote # 🎯 修正點：引入 unquote 來解碼檔案名
import json 
# 修正點：引入 asyncio 
import asyncio
from fastapi.responses import FileResponse
# 修正點：引入 File, UploadFile 來處理檔案上傳
from fastapi import FastAPI, HTTPException, Request, Response, Body, BackgroundTasks, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp
from starlette.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Literal, Any, Dict
# 🎯 新增：引入 pathlib 來處理路徑
from pathlib import Path 

# --- 🎯 新增的依賴：處理異步檔案操作 (推薦) ---
import aiofiles 

# 引入YT影片下載套件
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError



# --- 🎯 伺服器配置：Minecraft 存檔目標目錄 ---
# 修正點：使用 Path 物件
MINECRAFT_SAVE_DIR = Path("C:\\Users\\admin\\.minecraftx\\instances\\001\\saves")

# --- 🎯 Minecraft 檔案管理輔助函式 (新增) ---

def get_safe_path(filename: str) -> Path:
    """
    檢查檔案名是否安全，並返回相對於 MINECRAFT_SAVE_DIR 的完整 Path 物件。
    會檢查路徑遍歷企圖 (e.g., '..', '/').
    """
    # 拒絕包含 '..' 或絕對路徑分隔符 (只允許單一層級的檔案名)
    if ".." in filename or filename.startswith(('/', '\\')):
        raise HTTPException(status_code=400, detail="無效的檔案名格式，不允許路徑操作。")
        
    full_path = MINECRAFT_SAVE_DIR / filename
    
    # 關鍵的安全檢查：確保最終路徑是真正位於 base directory 之下
    # resolve() 處理符號連結並獲取絕對路徑
    if not full_path.resolve().is_relative_to(MINECRAFT_SAVE_DIR.resolve()):
        raise HTTPException(status_code=400, detail="路徑遍歷企圖被阻止。")
        
    return full_path

# --- 檔案下載後清理的自定義 Response ---
# ... (FinalCleanUpFileResponse 保持不變) ...
class FinalCleanUpFileResponse(FileResponse):
    """
    擴展 FileResponse，在檔案發送完成後，嘗試刪除檔案及其臨時目錄。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            # 執行原始 FileResponse 的發送邏輯
            await super().__call__(scope, receive, send)
        finally:
            # 檔案傳輸完成後進行清理
            # 修正點：使用 Path 物件處理路徑
            file_to_remove = Path(self.path)
            temp_dir = file_to_remove.parent
            
            # 1. 嘗試刪除檔案本身
            if file_to_remove.exists():
                file_to_remove.unlink() # 相當於 os.remove
                print(f"🗑️ 已刪除下載文件: {file_to_remove}")
            
            # 2. 嘗試刪除臨時目錄 (如果它是空的)
            if temp_dir.exists() and temp_dir != Path('/'): 
                try:
                    temp_dir.rmdir() # 相當於 os.rmdir，只刪除空目錄
                    print(f"🗑️ 已刪除空臨時目錄: {temp_dir}")
                except OSError:
                    # 如果目錄不為空，則忽略 rmdir 錯誤
                    pass

# --- IP 獲取輔助函式 (針對代理環境優化) ---
# ... (get_client_ip 保持不變) ...
def get_client_ip(request: Request) -> str:
    """
    獲取客戶端 IP，優先檢查反向代理（如 ngrok）設定的標準標頭。
    """
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip
    return request.client.host if request.client else "Unknown"

# --- 1. 定義 Custom Middleware (IP 監控) ---
# ... (ClientIPMiddleware 保持不變) ...
class ClientIPMiddleware(BaseHTTPMiddleware):
    """
    自定義中介軟體，用於記錄客戶端的 IP 位址、請求路徑和處理時間。
    """
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_ip = get_client_ip(request)
        start_time = time.time()
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] IP: {client_ip} | METHOD: {request.method} | PATH: {request.url.path}")

        request.state.client_ip = client_ip

        response = await call_next(request)

        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        
        print(f"IP: {client_ip} 的請求已完成，耗時: {process_time:.4f}s")
        return response

# 初始化 FastAPI 應用
app = FastAPI(title="Curri Data API")

# 允許所有來源進行 CORS 跨域請求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"], # 🎯 確保瀏覽器能看到所有 Header
)

# --- 2. 啟用 IP 監控中介軟體 ---
app.add_middleware(ClientIPMiddleware)

# --- 資料模型 (Pydantic) ---
class LoginRequest(BaseModel):
    username: str 
    password: str
# ... (DownloadRequest, Dept, DeptWithAgent, CAgent, MAP_CLS_DEPT 保持不變) ...
# YT下載請求模型
class DownloadRequest(BaseModel):
    """定義客戶端傳入的請求體結構"""
    url: str
    # 限定格式只能是 'mp3' 或 'mp4'
    format: Literal["mp3", "mp4"]

# 基礎系所資訊
class Dept(BaseModel):
    COLLEGE: str
    COLLEGE_S: str
    DEPT: str
    DEPT_S: str
    STYPE: str
    CAGENT_ID: int

# 新增系所及更新系所使用的模型：繼承自 Dept
class DeptWithAgent(Dept):
    AGENT_NAME: str
    AGENT_EXT: str
    AGENT_EMAIL: str

# 課務組承辦人基礎資訊
class CAgent(BaseModel):
    NAME: str
    EXT: str
    EMAIL: str

# 班級-系所簡稱對照表模型
class MAP_CLS_DEPT(BaseModel):
    CLASS: str
    DEPT_S: str


# --- 資料庫初始化函式 (確保 YT_DOWNLOAD_JOBS 表存在) ---
# ... (initialize_database 保持不變) ...
def initialize_database():
    # print("檢查並初始化 YT_DOWNLOAD_JOBS 表...")
    # SQL Server specific syntax
    # 注意: final_filepath 設為 NVARCHAR(255) 應足夠容納臨時路徑
    sql = """
    IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='YT_DOWNLOAD_JOBS' and xtype='U')
    CREATE TABLE YT_DOWNLOAD_JOBS (
        ID INT IDENTITY(1,1) PRIMARY KEY,
        job_id NVARCHAR(50) UNIQUE NOT NULL,
        client_ip NVARCHAR(50),
        url NVARCHAR(2048) NOT NULL,
        format NVARCHAR(10) NOT NULL,
        status NVARCHAR(20) NOT NULL, -- PENDING, PROCESSING, COMPLETED, FAILED
        progress INT NOT NULL DEFAULT 0,
        final_filepath NVARCHAR(255),
        start_time DATETIME,
        end_time DATETIME,
        created_at DATETIME DEFAULT GETDATE()
    );
    """
    try:
        # 使用同步執行
        execute_query(sql)
        # print("YT_DOWNLOAD_JOBS 表格準備就緒。")
    except Exception as e:
        # 這裡不應中斷應用程式，但必須警告使用者
        print(f"⚠️ 無法初始化 YT_DOWNLOAD_JOBS 表格，輪詢功能將無法運作: {e}")

# 在應用程式啟動時執行資料庫初始化
initialize_database()

# --- 輪詢架構的背景任務執行函式 ---
# ... (download_and_update_db 保持不變) ...
def download_and_update_db(job_id: str, url: str, target_format: str):
    """
    實際執行 yt-dlp 下載和轉碼的背景任務。
    它使用 progress_hooks 將進度更新寫回資料庫。
    """
    temp_dir = tempfile.mkdtemp()
    final_filepath = None
    
    # 1. yt-dlp 進度 Hook 函式
    def hook(d):
        try:
            status_map = {
                'downloading': 'PROCESSING',
                'finished': 'PROCESSING', # 轉碼中也視為 Processing
                'error': 'FAILED'
            }
            current_status = status_map.get(d['status'], 'PROCESSING')
            
            progress_percent = 0
            if current_status == 'PROCESSING':
                if d.get('total_bytes'):
                    # 下載進度 (佔 1% - 90%)
                    progress_percent = int((d.get('downloaded_bytes', 0) / d['total_bytes']) * 90)
                elif d['status'] == 'finished':
                    # 下載完成，進入後處理階段，進度設為 95%
                    progress_percent = 95
                else:
                    # 預設值，例如剛開始或無法計算時
                    progress_percent = 10 
            
            # 寫入資料庫 (同步執行)
            execute_query(
                "UPDATE YT_DOWNLOAD_JOBS SET status=?, progress=? WHERE job_id=?", 
                (current_status, progress_percent, job_id)
            )

        except Exception as hook_e:
            print(f"⚠️ 進度更新錯誤 (Job {job_id}): {hook_e}")

    # 2. 主要下載邏輯
    try:
        # 更新狀態為 PROCESSING (進度 10%) (同步執行)
        execute_query("UPDATE YT_DOWNLOAD_JOBS SET status='PROCESSING', start_time=GETDATE(), progress=10 WHERE job_id=?", (job_id,))
        
        # 根據目標格式設定 yt-dlp 選項
        if target_format == 'mp3':
            ydl_opts = {
                'format': 'bestaudio/best',
                # outtmpl 在後續會被精確設定，這裡使用簡單的 title 佔位
                'outtmpl': os.path.join(temp_dir, '%(title)s'), 
                'noplaylist': True,
                'quiet': True,
                'progress_hooks': [hook], # 啟用進度 Hook
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
            }
            expected_ext = '.mp3'
        elif target_format == 'mp4':
            # MP4 配置 (已修正，移除了冗餘的 postprocessors)
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'merge_output_format': 'mp4',
                'outtmpl': os.path.join(temp_dir, '%(title)s'), 
                'noplaylist': True,
                'quiet': True,
                'progress_hooks': [hook], # 啟用進度 Hook
            }
            expected_ext = '.mp4' 
        
        with YoutubeDL(ydl_opts) as ydl:
            # 獲取資訊
            info_dict = ydl.extract_info(url, download=False)
            
            # 1. 處理檔名：確保檔名乾淨且只包含一個擴展名 (供瀏覽器和 DB 使用)
            base_title = info_dict.get('title', 'download_file')
            # 移除任何不適合檔案名的字符
            base_title = "".join([c for c in base_title if c.isalnum() or c in (' ', '_', '-')]).rstrip()
            
            # 這是我們期望的最終檔名 (含單一擴展名)
            final_filename_for_browser = base_title + expected_ext
            
            # 2. 決定 YTDLP 的輸出路徑模板 (outtmpl)
            if target_format == 'mp3':
                # 🎯 修正點：MP3 使用 post-processor， outtmpl 不應包含 .mp3，讓 post-processor 添加。
                ydl_outtmpl_path = os.path.join(temp_dir, base_title) 
                # 預期的最終路徑 (包含 .mp3)
                final_filepath_temp = os.path.join(temp_dir, final_filename_for_browser)
            else: # MP4
                # MP4 使用 merge，outtmpl 應包含 .mp4 (這樣會產生 MyTitle.mp4)
                ydl_outtmpl_path = os.path.join(temp_dir, final_filename_for_browser)
                # 預期的最終路徑
                final_filepath_temp = ydl_outtmpl_path
            
            # 將正確的 outtmpl 設置回選項
            ydl_opts['outtmpl'] = ydl_outtmpl_path 
            
            print(f"Job {job_id} 預期瀏覽器檔名: {final_filename_for_browser}, YTDLP outtmpl: {ydl_outtmpl_path}")

            # 重新初始化 YDL 並執行下載和後處理
            with YoutubeDL(ydl_opts) as final_ydl:
                final_ydl.download([url])
            
            # 確保 final_filepath 是實際的檔案路徑
            if os.path.exists(final_filepath_temp):
                final_filepath = final_filepath_temp
            
        if not final_filepath or not os.path.exists(final_filepath):
             # 重新檢查目錄內容，以防檔名預測錯誤
             found_files = [f for f in os.listdir(temp_dir) if f.endswith(expected_ext)]
             if found_files:
                 # 如果找到了，使用找到的第一個檔案
                 final_filename = found_files[0]
                 final_filepath = os.path.join(temp_dir, final_filename)
                 print(f"⚠️ 檔名預測失敗，但找到了檔案: {final_filepath}")
             else:
                 raise Exception("文件生成失敗，請檢查 yt-dlp 執行日誌。")

        # 成功完成後更新資料庫 (同步執行)
        # 這裡將使用正確的 final_filepath 存入資料庫
        execute_query(
            "UPDATE YT_DOWNLOAD_JOBS SET status='COMPLETED', progress=100, final_filepath=?, end_time=GETDATE() WHERE job_id=?", 
            (final_filepath, job_id)
        )
        print(f"✅ Job {job_id} 成功完成。檔案: {final_filepath}")

    except Exception as e:
        # 失敗時更新資料庫狀態 (同步執行)
        error_message = f"下載失敗: {str(e)}"
        execute_query(
            "UPDATE YT_DOWNLOAD_JOBS SET status='FAILED', progress=0, end_time=GETDATE(), final_filepath='ERROR' WHERE job_id=?", 
            (job_id,)
        )
        print(f"❌ Job {job_id} 失敗: {error_message}")
        
        # 失敗後立即清理臨時目錄
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


# --- 🎯 Minecraft 存檔管理 API 端點 (新增/修正) ---

# 1. 檔案列表端點
@app.get("/api/list-saves", summary="獲取 Minecraft 存檔目錄下的第一層檔案與資料夾列表")
async def list_saves():
    """
    列出 MINECRAFT_SAVE_DIR 目錄下的所有檔案和資料夾名稱。
    此列表用於前端讓使用者選擇要下載的存檔。
    """
    try:
        # 使用 Path.iterdir() 列出第一層內容
        # p.name 自動獲取檔案或資料夾名稱
        file_list = [p.name for p in MINECRAFT_SAVE_DIR.iterdir()]
        print(f"✅ 成功列出存檔目錄內容：共 {len(file_list)} 個項目。")
        return {"files": file_list}
    except FileNotFoundError:
        # 如果目錄不存在，返回空列表而不是 500 錯誤
        print(f"⚠️ 存檔目錄不存在: {MINECRAFT_SAVE_DIR}")
        return {"files": [], "message": "目標存檔目錄不存在或沒有檔案。"}
    except Exception as e:
        print(f"❌ 列出檔案失敗: {e}")
        # 如果是權限問題或其他伺服器錯誤
        raise HTTPException(status_code=500, detail="伺服器無法存取存檔目錄。")


# 2. 檔案下載端點
@app.get("/api/download-save/{filename}", summary="下載指定的 Minecraft 存檔或檔案")
async def download_save(filename: str):
    """
    接收檔案名，執行安全檢查，並以串流方式返回檔案內容。
    """
    try:
        # 1. 對 URL 編碼的檔案名進行解碼 (處理中文等)
        decoded_filename = unquote(filename)
        
        # 2. 安全檢查：獲取安全的 Path 物件
        safe_path = get_safe_path(decoded_filename)
        
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="檔案未找到。")
        
        if not safe_path.is_file():
            # 防止下載整個資料夾，但可以調整策略 (例如打包成 zip)
            raise HTTPException(status_code=400, detail="請求的項目是資料夾，不支援直接下載資料夾。")
            
        # 3. 處理 Content-Disposition 標頭 (確保中文檔名正確)
        original_filename = safe_path.name
        ascii_filename = original_filename.encode('ascii', 'replace').decode('ascii')
        quoted_filename_utf8 = quote(original_filename)

        content_disposition_header = (
            f'attachment; '
            f'filename="{ascii_filename}"; ' # ASCII fallback
            f"filename*=utf-8''{quoted_filename_utf8}" # UTF-8 規範名稱
        )
        
        response_headers = {
            'Content-Disposition': content_disposition_header,
        }
            
        # 4. 返回 FileResponse 串流檔案
        return FileResponse(
            path=safe_path, 
            headers=response_headers,
            media_type='application/octet-stream' # 通用下載類型
        )

    except HTTPException:
        # 重新拋出 HTTPException 讓 FastAPI 處理
        raise
    except Exception as e:
        print(f"❌ 檔案下載失敗: {e}")
        raise HTTPException(status_code=500, detail=f"伺服器處理下載失敗: {e}")


# 3. 檔案上傳端點 (保持不變)
@app.post("/api/upload-save", summary="上傳 Minecraft 存檔至伺服器指定路徑")
async def upload_save(
    file: UploadFile = File(..., description="要上傳的 Minecraft 存檔或檔案"),
    req: Request = None
):
    """
    接收前端發送的檔案，並將其儲存到 MINECRAFT_SAVE_DIR，同名檔案會被覆蓋。
    檔案名稱將使用上傳時的原始檔案名稱。
    """
    print(f"收到上傳檔案請求...")
    client_ip = get_client_ip(req)
    
    # 修正點：使用 Path 物件處理路徑
    target_dir = MINECRAFT_SAVE_DIR
    
    # 1. 確保目標目錄存在
    if not target_dir.exists():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"伺服器錯誤: 無法創建目標目錄 {target_dir}. 錯誤: {e}")

    # 2. 確定最終儲存路徑 (使用原始檔名，並自動覆蓋)
    # file.filename 已經包含檔案名稱，如 "MyWorld.zip"
    # 修正點：使用 Path 物件組合路徑
    final_path = target_dir / file.filename 

    print(f"Client IP: {client_ip} 正在上傳檔案: {file.filename} 到 {final_path}")

    try:
        # 3. 異步寫入檔案到目標路徑
        async with aiofiles.open(final_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
        
        print(f"✅ 檔案 {file.filename} 儲存成功，路徑: {final_path}")
        
        return {
            "message": "檔案上傳成功並已儲存到目標目錄。",
            "filename": file.filename,
            "target_path": str(final_path), # 轉換回字串以便序列化
            "overwrite_policy": "同名檔案已覆蓋"
        }
    except Exception as e:
        print(f"❌ 檔案上傳/儲存失敗: {e}")
        await file.close() 
        raise HTTPException(status_code=500, detail=f"伺服器處理檔案失敗: {e}")
    finally:
        pass



# --- 以下為不變動的既有 API 端點 ---

# 測試GET功能
# ... (get_test 保持不變) ...
@app.get("/get_test", summary="測試GET")
async def get_test():
    print("get test成功")
    return "伺服器端訪問成功。"
# 測試POST功能
# ... (post_test 保持不變) ...
@app.post("/post_test", summary="測試POST")
async def post_test(item: DownloadRequest):
    print("url: ", item.url)
    print("format: ", item.format)
    
    return "post成功囉"

# --- DEPTS ---
# 1. 讀取系所表(含承辦人及課務組承辦人資料)
# ... (get_depts 保持不變) ...
@app.get("/get_depts", summary="讀取所有系所資料及承辦人資訊")
async def get_depts():
    try:
        sql = """
SELECT
    d.ID, COLLEGE, COLLEGE_S, DEPT, DEPT_S, STYPE, 
    AGENT_NAME, AGENT_EXT, AGENT_EMAIL,
    ca.ID as CAGENT_ID, ca.NAME as CAGENT_NAME, ca.EXT as CAGENT_EXT, ca.EMAIL as CAGENT_EMAIL
FROM
    DEPTS AS d
LEFT JOIN
    CAGENTS AS ca ON d.CAGENT_ID = ca.ID;
"""
        data = await asyncio.to_thread(execute_query, sql)
        return data
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch departments: {e}")

# 2. 新增系所到DEPTS(含承辦人及課務組承辦人資料)
# ... (create_dept 保持不變) ...
@app.post("/create_dept", summary="新增系所資料")
async def create_dept(item: DeptWithAgent):
    """
    建立新的系所資料，使用標準 INSERT 語句，不回傳 ID。
    """
    sql = """
        INSERT INTO DEPTS (COLLEGE, COLLEGE_S, DEPT, DEPT_S, STYPE, AGENT_NAME, AGENT_EXT, AGENT_EMAIL, CAGENT_ID)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    values = (item.COLLEGE, item.COLLEGE_S, item.DEPT, item.DEPT_S, item.STYPE, item.AGENT_NAME, item.AGENT_EXT, item.AGENT_EMAIL, item.CAGENT_ID)
    
    try:
        await asyncio.to_thread(execute_query, sql, values)
        return {"message": "Department added successfully."}

    except UniqueConstraintError as e:
        raise HTTPException(status_code=409, detail=f"Failed to create department: 唯一約束衝突 (可能系所名稱或簡稱已存在)")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create department: 資料庫錯誤: {e}")

# 3. 修改dept資料
# ... (update_dept 保持不變) ...
@app.put("/update_dept/{dept_id}", summary="修改指定 ID 的系所資料")
async def update_dept(dept_id: int, item: DeptWithAgent):
    sql = """
        UPDATE DEPTS SET
        COLLEGE = ?, COLLEGE_S = ?, DEPT = ?, DEPT_S = ?, STYPE = ?, AGENT_NAME = ?, AGENT_EXT = ?, AGENT_EMAIL = ?, CAGENT_ID = ?
        WHERE ID = ?
    """
    values = (item.COLLEGE, item.COLLEGE_S, item.DEPT, item.DEPT_S, item.STYPE, item.AGENT_NAME, item.AGENT_EXT, item.AGENT_EMAIL, item.CAGENT_ID, dept_id)
    try:
        # execute_query(sql, values) 返回的是受影響的行數
        result = await asyncio.to_thread(execute_query, sql, values)
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Department with ID {dept_id} not found.")
        return {"message": "Department updated successfully."}
    except UniqueConstraintError as e:
        raise HTTPException(status_code=409, detail=f"Failed to update department: 唯一約束衝突")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to update department: {e}")

# 4. 刪除dept
# ... (delete_dept 保持不變) ...
@app.delete("/delete_dept/{dept_id}", summary="刪除指定 ID 的系所資料")
async def delete_dept(dept_id: int):
    try:
        # 確保參數以 tuple 形式傳遞
        result = await asyncio.to_thread(execute_query, "DELETE FROM DEPTS WHERE ID = ?", (dept_id,))
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Department with ID {dept_id} not found.")
        return {"message": "Department deleted successfully."}
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete department: {e}")

# --- CAGENTS ---
# 5. 查詢課務組承辦人資料
# ... (get_cagents 保持不變) ...
@app.get("/get_cagents", summary="查詢所有課務組承辦人資料")
async def get_cagents():
    try:
        sql = "SELECT * FROM CAGENTS"
        data = await asyncio.to_thread(execute_query, sql)
        return data
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch C Agents: {e}")

# 6. 新增課務組承辦人CAGENTS (使用 CAgent)
# ... (create_cagent 保持不變) ...
@app.post("/create_cagent", summary="新增課務組承辦人資料")
async def create_cagent(item: CAgent):
    sql = """
        INSERT INTO CAGENTS (NAME, EXT, EMAIL)
        VALUES (?, ?, ?);
    """
    values = (item.NAME, item.EXT, item.EMAIL)
    
    try:
        await asyncio.to_thread(execute_query, sql, values)
        return {"message": "Curri agent added successfully."}

    except UniqueConstraintError as e:
        raise HTTPException(status_code=409, detail=f"Failed to create Curri agent: 唯一約束衝突 (可能姓名或 Email 已存在)")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Curri agent: 資料庫錯誤: {e}")

# 7. 修改課務組承辦人 (使用 CAgent)
# ... (update_cagent 保持不變) ...
@app.put("/update_cagent/{cagent_id}", summary="修改指定 ID 的課務組承辦人資料")
async def update_cagent(cagent_id: int, item: CAgent):
    sql = """
        UPDATE CAGENTS SET
        NAME = ?, EXT = ?, EMAIL = ?
        WHERE ID = ?
    """
    values = (item.NAME, item.EXT, item.EMAIL, cagent_id)
    try:
        result = await asyncio.to_thread(execute_query, sql, values)
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Curri agent with ID {cagent_id} not found.")
        return {"message": "Curri agent updated successfully."}
    except UniqueConstraintError as e:
        raise HTTPException(status_code=409, detail=f"Failed to update Curri agent: 唯一約束衝突")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to update Curri agent: {e}")

# 8. 刪除課務組承辦人
# ... (delete_cagent 保持不變) ...
@app.delete("/delete_cagent/{cagent_id}", summary="刪除指定 ID 的課務組承辦人資料")
async def delete_cagent(cagent_id: int):
    try:
        result = await asyncio.to_thread(execute_query, "DELETE FROM CAGENTS WHERE ID = ?", (cagent_id,))
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Curri agent with ID {cagent_id} not found.")
        return {"message": "Curri agent deleted successfully."}
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete Curri agent: {e}")


# 9. 呼叫 sp_GetAll 預存程序 for ClassConverter
# ... (get_all_data 保持不變) ...
@app.get("/get_all_data")
async def get_all_data():
    try:
        data = await asyncio.to_thread(execute_query, "EXEC sp_GetAll")
        return data
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch all data from stored procedure: {e}")

# --- MAP_CLS_DEPT ---
# 10. 查詢班級-系所簡稱對照表
# ... (get_map_cls_dept 保持不變) ...
@app.get("/get_map_cls_dept", summary="查詢所有班級-系所簡稱對照資料")
async def get_map_cls_dept():
    try:
        sql = "SELECT * FROM MAP_CLS_DEPT"
        data = await asyncio.to_thread(execute_query, sql)
        return data
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch class-dept mapping: {e}")

# 11. 新增班級-系所簡稱
# ... (create_map_cls_dept 保持不變) ...
@app.post("/create_map_cls_dept", summary="新增班級-系所簡稱對照")
async def create_map_cls_dept(item: MAP_CLS_DEPT):
    sql = """
        INSERT INTO MAP_CLS_DEPT (CLASS, DEPT_S)
        VALUES (?, ?);
    """
    values = (item.CLASS, item.DEPT_S)
    
    try:
        await asyncio.to_thread(execute_query, sql, values)
        return {"message": "Class-dept_short added successfully."}

    except UniqueConstraintError as e:
        raise HTTPException(status_code=409, detail=f"Failed to create class-dept_short: 唯一約束衝突 (班級與簡稱組合可能已存在)")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create class-dept_short: 資料庫錯誤: {e}")

# 12. 修改班級-系所簡稱
# ... (update_map_cls_dept 保持不變) ...
@app.put("/update_map_cls_dept/{map_cls_dept_id}", summary="修改指定 ID 的班級-系所簡稱對照")
async def update_map_cls_dept(map_cls_dept_id: int, item: MAP_CLS_DEPT): # 修正：這裡的 MAP_CLS_CLS_DEPT 應該是 MAP_CLS_DEPT
    sql = """
        UPDATE MAP_CLS_DEPT SET
        CLASS = ?, DEPT_S = ?
        WHERE ID = ?
    """
    values = (item.CLASS, item.DEPT_S, map_cls_dept_id)
    try:
        result = await asyncio.to_thread(execute_query, sql, values)
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Class-dept_short with ID {map_cls_dept_id} not found.")
        return {"message": "class-dept_short updated successfully."}
    except UniqueConstraintError as e:
        raise HTTPException(status_code=409, detail=f"Failed to update class-dept_short: 唯一約束衝突")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to update class-dept_short: {e}")

# 13. 刪除班級-系所簡稱
# ... (delete_map_cls_dept 保持不變) ...
@app.delete("/delete_map_cls_dept/{map_cls_dept_id}", summary="刪除指定 ID 的班級-系所簡稱對照")
async def delete_map_cls_dept(map_cls_dept_id: int):
    try:
        result = await asyncio.to_thread(execute_query, "DELETE FROM MAP_CLS_DEPT WHERE ID = ?", (map_cls_dept_id,))
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Class-dept_short with ID {map_cls_dept_id} not found.")
        return {"message": "class-dept_short deleted successfully."}
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete class-dept_short: {e}")

# --- 輪詢架構 API 端點 (取代 /download 與 /download_final) ---
# ... (submit_download_job, get_download_status, download_file 保持不變) ...

# 14. 提交 YouTube 下載任務
@app.post("/submit_download_job", summary="提交 YouTube 下載任務 (非同步輪詢第一步)")
async def submit_download_job(request: DownloadRequest, background_tasks: BackgroundTasks, req: Request):
    """
    客戶端呼叫此 API 提交任務，伺服器立即返回 Job ID 並在背景啟動下載。
    """
    client_ip = get_client_ip(req)
    job_id = str(uuid.uuid4())

    try:
        # 1. 記錄初始任務狀態到資料庫 (Status: PENDING)
        insert_sql = """
            INSERT INTO YT_DOWNLOAD_JOBS (job_id, client_ip, url, format, status, progress)
            VALUES (?, ?, ?, ?, 'PENDING', 0);
        """
        # 使用 asyncio.to_thread 確保 execute_query 在單獨的執行緒中執行
        await asyncio.to_thread(execute_query, insert_sql, (job_id, client_ip, request.url, request.format))

        # 2. 將實際的下載工作加入背景任務
        background_tasks.add_task(download_and_update_db, job_id, request.url, request.format)

        return {"job_id": job_id, "message": "下載任務已提交，請使用 job_id 輪詢狀態。"}
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"提交任務失敗: 資料庫錯誤: {e}")

# 15. 查詢下載任務狀態
@app.get("/download_status/{job_id}", summary="查詢下載任務狀態和進度 (非同步輪詢第二步)")
async def get_download_status(job_id: str):
    """
    客戶端使用 Job ID 輪詢任務狀態和進度。
    返回: status (PENDING/PROCESSING/COMPLETED/FAILED), progress (0-100)
    """
    try:
        sql = "SELECT status, progress FROM YT_DOWNLOAD_JOBS WHERE job_id = ?"
        
        # 使用 fetch_one=True，預期返回字典或 None
        data = await asyncio.to_thread(execute_query, sql, (job_id,), fetch_one=True)
        
        if not data:
            # 如果資料為 None 或空，則表示 Job ID 不存在
            raise HTTPException(status_code=404, detail=f"Job ID {job_id} 未找到。")

        # 修正點：使用欄位名稱 'status' 和 'progress' 作為字典鍵來存取結果
        return {"status": data['status'], "progress": data['progress']} 
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"查詢狀態失敗: {e}")
    except KeyError as e:
        # 捕獲 KeyError，如果資料庫返回的字典缺少預期的鍵
        raise HTTPException(status_code=500, detail=f"查詢狀態失敗: 資料結構錯誤，無法使用鍵 {e} 存取結果。")


# 16. 獲取最終下載文件
@app.get("/download_file/{job_id}", summary="獲取最終下載文件 (非同步輪詢第三步)")
async def download_file(job_id: str):
    
    sql_query = "SELECT final_filepath, status FROM YT_DOWNLOAD_JOBS WHERE job_id = ?"
    
    # 使用 fetch_one=True，預期返回字典
    job_details: Optional[Dict[str, Any]] = await asyncio.to_thread(execute_query, sql_query, (job_id,), fetch_one=True)

    if not job_details:
        raise HTTPException(status_code=404, detail="工作 ID 未找到。")
    
    # 修正點：統一使用字典鍵存取
    file_path = job_details.get('final_filepath')
    current_status = job_details.get('status', 'UNKNOWN')
    
    if current_status != 'COMPLETED':
        # 如果狀態不是完成，則不能下載
        raise HTTPException(status_code=400, detail=f"檔案尚未準備好，目前狀態: {current_status}")

    if not file_path or file_path == 'ERROR':
        raise HTTPException(status_code=404, detail="下載任務已完成但未記錄有效檔案路徑或已失敗。")
    
    if not os.path.exists(file_path):
        # 如果檔案不存在 (可能已被清理或下載失敗)
        raise HTTPException(status_code=404, detail="檔案已完成下載但伺服器上找不到對應文件 (可能已被清理)。")


    # 從路徑中解析出檔案名稱
    original_filename = os.path.basename(file_path)
    
    # 手動建構 Content-Disposition 標頭以支援中文
    # 1. 將原始檔名轉換為 ASCII 安全版本
    ascii_filename = original_filename.encode('ascii', 'replace').decode('ascii')
    
    # 2. 將原始檔名進行 URL 編碼 (用於 filename* 部分)
    quoted_filename_utf8 = quote(original_filename)

    # 3. 建構 RFC 5987 標準的 Content-Disposition 標頭
    content_disposition_header = (
        f'attachment; '
        f'filename="{ascii_filename}"; ' # ASCII fallback
        f"filename*=utf-8''{quoted_filename_utf8}" # UTF-8 規範名稱
    )
    
    response_headers = {
        'Content-Disposition': content_disposition_header,
        # 其他您可能需要的標頭
    }
    
    # 4. 回傳帶有修正標頭的 FinalCleanUpFileResponse
    return FinalCleanUpFileResponse(
        path=file_path,
        headers=response_headers,
        media_type="application/octet-stream" # 這是通用下載類型
    )


# 17. 查詢 MEMBERS 表所有資料
@app.get("/api/members", summary="查詢 MEMBERS 表所有資料")
async def get_members():
    """
    從 MEMBERS 表中讀取所有欄位資料，並以 JSON 格式回傳給客戶端。
    """
    try:
        # 假設您的 MEMBERS 表已經存在
        sql = "SELECT * FROM MEMBERS"
        
        # 由於 execute_query 是同步函數，我們使用 asyncio.to_thread 確保它不會阻塞 FastAPI 的主事件迴圈
        data = await asyncio.to_thread(execute_query, sql)
        
        # execute_query 預期返回一個包含字典的列表，FastAPI 會將其自動序列化為 JSON
        return data
        
    except DatabaseError as e:
        # 如果發生任何資料庫錯誤 (例如表不存在、連線問題等)
        print(f"❌ 查詢 MEMBERS 表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"伺服器錯誤: 無法查詢 MEMBERS 表資料。")
    except Exception as e:
        # 捕捉其他未預期的錯誤
        print(f"❌ 查詢 MEMBERS 表發生未知錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"伺服器錯誤: {e}")

# ... (在 get_members 之前或之後新增)

# 18. 使用者登入 (已更新為 user_login)
@app.post("/api/user_login", summary="使用者登入 (根據 ACCOUNT 及 PWD 驗證)")
async def user_login(request: LoginRequest):
    """
    根據傳入的帳號 (對應 MEMBERS.ACCOUNT) 和密碼 (對應 MEMBERS.PWD) 驗證使用者身份，
    並回傳使用者的 NAME 和 AUTH 權限資訊。
    注意：此處僅為示範，實際應用需加密比對密碼。
    """
    try:
        # 🎯 關鍵修改：SQL 使用 ACCOUNT 和 PWD 欄位進行驗證
        # 回傳欄位為 NAME 和 AUTH
        sql = "SELECT NAME, AUTH FROM MEMBERS WHERE ACCOUNT = ? AND PWD = ?"
        
        # 由於前端傳入的 key 是 username 和 password，我們將其對應到 ACCOUNT 和 PWD
        user_data = await asyncio.to_thread(
            execute_query, 
            sql, 
            (request.username, request.password), 
            fetch_one=True
        )
        
        if user_data:
            # 登入成功，回傳 NAME 和 AUTH
            return {
                "message": "登入成功",
                "user": {
                    # 🎯 欄位對應：NAME 作為顯示名稱
                    "name": user_data['NAME'],
                    # 🎯 欄位對應：AUTH 作為權限標識
                    "auth": user_data['AUTH'],
                    # 額外回傳登入帳號，方便前端顯示
                    "username": request.username 
                }
            }
        else:
            # 登入失敗
            raise HTTPException(status_code=401, detail="帳號或密碼錯誤。")
            
    except DatabaseError as e:
        print(f"❌ 登入查詢資料庫失敗: {e}")
        raise HTTPException(status_code=500, detail="伺服器錯誤: 資料庫連線失敗。")
    except KeyError as e:
        print(f"❌ 登入查詢結果缺少預期欄位: {e}")
        raise HTTPException(status_code=500, detail="伺服器錯誤: 資料庫查詢結果欄位不正確。")


# --- 🎯 新增：梗圖管理配置 ---
# 這裡設定梗圖存放在伺服器的哪個資料夾（建議與 main.py 同一層的 memes 資料夾）
MEME_DIR = Path("memes") 
# 如果資料夾不存在才建立，存在則忽略 (exist_ok=True 的作用)
if not MEME_DIR.exists():
    MEME_DIR.mkdir(parents=True)
    print(f"📁 偵測到缺失目錄，已自動建立: {MEME_DIR}")

# 🎯 修正點：掛載靜態目錄，讓前端可以直接透過 URL (如 http://localhost:8000/memes/abc.png) 存取圖片
# 注意：這行通常放在 app = FastAPI() 之後
# app.mount("/memes", StaticFiles(directory="memes"), name="memes")
@app.get("/memes/{filename}")
async def get_meme_image(filename: str):
    file_path = Path("memes") / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="圖片不存在")
    
    # 直接回傳 FileResponse，並確保標頭包含所有跨域許可
    return FileResponse(
        path=file_path,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            # 🎯 這一行極其重要，讓瀏覽器知道這個靜態資源允許跨域讀取
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Cache-Control": "no-cache", # 測試期間禁用快取
            "ngrok-skip-browser-warning": "69420", 
            "ngrok-skip-browser-warning": "true",
            "Content-Type": "image/png" # 強制指定，防止被誤判
        }
    )

# --- 🎯 梗圖管理 API 端點 (新增) ---

DATA_FILE = Path("memes_data.json")

# 讀取現有描述資料
def load_meme_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# 儲存描述資料
def save_meme_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 1. 獲取梗圖列表
@app.get("/api/memes")
async def get_memes(request: Request):
    meme_data = load_meme_data()
    meme_list = []
    
    # 獲取基礎 URL 並確保結尾沒有斜線
    base_url = str(request.base_url).rstrip('/')
    
    for p in MEME_DIR.glob("*"):
        if p.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif"]:
            filename = p.name
            
            # 🎯 檢查 JSON 資料庫是否有這張圖的詳細描述
            if filename in meme_data:
                display_title = meme_data[filename]
            else:
                # 🎯 如果 JSON 沒紀錄（例如舊圖），則執行「過濾時間戳」邏輯
                # stem 是不含副檔名的檔名，split('-', 1) 只切第一次出現的槓
                parts = p.stem.split('-', 1)
                if len(parts) > 1 and parts[0].isdigit():
                    display_title = parts[1] # 取得槓後面的文字
                else:
                    display_title = p.stem # 沒有槓就用原名
            
            meme_list.append({
                "title": display_title,
                "url": f"{base_url}/memes/{filename}"
            })
    
    # 根據檔名降冪排序 (讓帶有較大時間戳的新圖片排在前面)
    meme_list.sort(key=lambda x: x['url'].split('/')[-1], reverse=True)
    
    return meme_list


# 2. 上傳梗圖
@app.post("/api/upload-meme", summary="上傳新梗圖")
async def upload_meme(
    file: UploadFile = File(...), 
    title: str = Form(...),
    req: Request = None
):
    """
    接收圖片檔案與標題，存入 MEME_DIR。
    """
    client_ip = get_client_ip(req)
    
    # 🎯 修正點：處理檔案安全性，加上時間戳避免重複
    timestamp = int(time.time())
    # 確保原始檔名安全 (過濾掉路徑字元)
    safe_name = "".join([c for c in file.filename if c.isalnum() or c in ('.', '_', '-')])
    final_filename = f"{timestamp}-{safe_name}"
    save_path = MEME_DIR / final_filename

    print(f"📸 IP: {client_ip} 正在上傳梗圖: {title} ({final_filename})")

    try:
        # 1. 異步寫入檔案
        async with aiofiles.open(save_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
        # 2. 🎯 將描述存入 JSON 資料庫
        meme_data = load_meme_data()
        meme_data[final_filename] = title  # 以檔名為 Key，描述為 Value
        save_meme_data(meme_data)

        return {
            "success": True,
            "message": "梗圖上傳成功",
            "title": title,
            "filename": final_filename
        }
    except Exception as e:
        print(f"❌ 梗圖儲存失敗: {e}")
        raise HTTPException(status_code=500, detail=f"伺服器儲存梗圖失敗: {e}")
print(f"curridata_server已啟動，等候客戶端訪問中...")