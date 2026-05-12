# 引入您提供的 MSSQL 資料庫輔助函數和例外
from database_helper_pg import execute_query, DatabaseError, UniqueConstraintError, DatabaseCursor
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

# 初始化 FastAPI 應用
app = FastAPI(title="Curri Data API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 資料模型 (Pydantic) ---
class LoginRequest(BaseModel):
    username: str 
    password: str

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
class map_cls_dept(BaseModel):
    CLASS: str
    DEPT_S: str

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


from fastapi import HTTPException

from fastapi import HTTPException

@app.post("/api/user_login", summary="使用者登入 (依 account / pwd 驗證)")
def user_login(request: LoginRequest):
    """
    根據 MEMBERS.account / MEMBERS.pwd 驗證使用者
    回傳 name 與 auth 權限資訊
    """

    sql = """
        SELECT name, auth
        FROM members
        WHERE account = %s AND pwd = %s
        LIMIT 1
    """

    try:
        user_data = execute_query(
            sql,
            (request.username, request.password),
            fetch_one=True
        )
    except Exception as e:
        print(f"❌ 登入查詢資料庫失敗: {e}")
        raise HTTPException(status_code=500, detail="伺服器錯誤: 資料庫連線失敗")

    if not user_data:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    return {
        "message": "登入成功",
        "user": {
            "name": user_data["name"],
            "auth": user_data["auth"],
            "username": request.username
        }
    }



# --- depts ---
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
    depts AS d
LEFT JOIN
    cagents AS ca ON d.CAGENT_ID = ca.ID;
"""
        data = await asyncio.to_thread(execute_query, sql)
        return data
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch departments: {e}")

print(f"curridata_server已啟動，等候客戶端訪問中...")