# region 宣告import 
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

# endregion

# region 初始化 FastAPI 應用 
app = FastAPI(title="Curri Data API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# endregion

# region 資料模型 (Pydantic) 
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
    college: str
    college_s: str
    dept: str
    dept_s: str
    stype: str
    cagent_id: int

# 新增系所及更新系所使用的模型：繼承自 Dept
class DeptWithAgent(Dept):
    agent_name: str
    agent_ext: str
    agent_email: str

# 課務組承辦人基礎資訊
class CAgent(BaseModel):
    name: str
    ext: str
    email: str

# 班級-系所簡稱對照表模型
class map_cls_dept(BaseModel):
    cls: str = Field(alias="class")
    dept_s: str

# endregion

# region 測試server狀態by GET POST 
# 測試GET功能
@app.get("/get_test", summary="測試GET")
async def get_test():
    print("get test成功")
    return "伺服器端訪問成功。"

# 測試POST功能
@app.post("/post_test", summary="測試POST")
async def post_test(item: DownloadRequest):
    print("url: ", item.url)
    print("format: ", item.format)
    
    return "post成功囉"
#endregion

# region [API] 使用者登入 驗證帳號密碼 
@app.post("/api/user_login", summary="使用者登入 (依 account / pwd 驗證)")
def user_login(request: LoginRequest):

    sql = """
        SELECT name, auth
        FROM members
        WHERE account = :account AND pwd = :pwd
        LIMIT 1
    """

    try:
        user_data = execute_query(
            sql,
            {
                "account": request.username,
                "pwd": request.password
            },
            fetch_one=True
        )
    except Exception as e:
        print(f"❌ 登入查詢資料庫失敗: {e}")
        raise HTTPException(status_code=500, detail="伺服器錯誤: 資料庫連線失敗")

    if not user_data:
        print(f"request.username=",request.username)
        print(f"request.password=",request.password)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    return {
        "message": "登入成功",
        "user": {
            "name": user_data["name"],
            "auth": user_data["auth"],
            "username": request.username
        }
    }
# endregion

# region [API] depts表相關 
# 1. 讀取系所表(含承辦人及課務組承辦人資料)
@app.get("/get_depts", summary="讀取所有系所資料及承辦人資訊")
async def get_depts():
    try:
        sql = """
SELECT
    d.id, college, college_s, dept, dept_s, stype, 
    agent_name, agent_ext, agent_email,
    ca.id as cagent_id, ca.name as cagent_name, ca.ext as cagent_ext, ca.email as cagent_email
FROM
    depts AS d
LEFT JOIN
    cagents AS ca ON d.cagent_id = ca.id;
"""
        data = await asyncio.to_thread(execute_query, sql)
        return data
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch departments: {e}")
# endregion

# region CAGENTS - GET 

@app.get("/get_cagents", summary="查詢所有課務組承辦人資料")
def get_cagents():
    try:
        sql = """
            SELECT id, name, ext, email
            FROM cagents
            ORDER BY id
        """

        data = execute_query(sql)
        return data

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch C Agents: {e}")

# endregion

# region CAGENTS - CREATE

@app.post("/create_cagent", summary="新增課務組承辦人資料")
def create_cagent(item: CAgent):

    sql = """
        INSERT INTO cagents (name, ext, email)
        VALUES (:name, :ext, :email)
    """

    params = {
        "name": item.NAME.strip(),
        "ext": item.EXT.strip(),
        "email": item.EMAIL.strip()
    }

    try:
        execute_query(sql, params)
        return {"message": "Curri agent added successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="唯一約束衝突 (可能姓名或 Email 已存在)")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"資料庫錯誤: {e}")

# endregion

# region CAGENTS - UPDATE

@app.put("/update_cagent/{cagent_id}", summary="修改指定 ID 的課務組承辦人資料")
def update_cagent(cagent_id: int, item: CAgent):

    sql = """
        UPDATE cagents
        SET name = :name,
            ext = :ext,
            email = :email
        WHERE id = :id
    """

    params = {
        "name": item.NAME.strip(),
        "ext": item.EXT.strip(),
        "email": item.EMAIL.strip(),
        "id": cagent_id
    }

    try:
        result = execute_query(sql, params)

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Curri agent with ID {cagent_id} not found.")

        return {"message": "Curri agent updated successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="唯一約束衝突")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"資料庫錯誤: {e}")

# endregion

# region CAGENTS - DELETE

@app.delete("/delete_cagent/{cagent_id}", summary="刪除指定 ID 的課務組承辦人資料")
def delete_cagent(cagent_id: int):

    sql = """
        DELETE FROM cagents
        WHERE id = :id
    """

    try:
        result = execute_query(sql, {"id": cagent_id})

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Curri agent with ID {cagent_id} not found.")

        return {"message": "Curri agent deleted successfully."}

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete Curri agent: {e}")

# endregion














print(f"curridata_server已啟動，等候客戶端訪問中...")