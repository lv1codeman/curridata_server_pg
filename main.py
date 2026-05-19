# region 宣告import 
from database_helper_pg import execute_query, DatabaseError, UniqueConstraintError
import time
import tempfile
import os
from dotenv import load_dotenv
import shutil
import uuid

from urllib.parse import quote, unquote # 🎯 修正點：引入 unquote 來解碼檔案名
import json 
# 修正點：引入 asyncio 
import asyncio
from fastapi.responses import FileResponse
from fastapi import FastAPI, HTTPException, Request, Response, Body, BackgroundTasks, File, UploadFile, Form, Depends
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp
from starlette.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any, Dict
# 🎯 新增：引入 pathlib 來處理路徑
from pathlib import Path 

# --- 🎯 新增的依賴：處理異步檔案操作 (推薦) ---
import aiofiles 

# 引入YT影片下載套件
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# endregion

load_dotenv() # 讀取環境變數

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

class Member(BaseModel):
    account: str
    pwd: str
    name: str
    auth: str

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

class MapClsDept(BaseModel):
    cls: str = Field(alias="class")
    dept_s: str

    class Config:
        populate_by_name = True

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

# region (舊版 暫不使用) [API] 使用者登入 驗證帳號密碼 
# @app.post("/api/user_login", summary="使用者登入 (依 account / pwd 驗證)")
# def user_login(request: LoginRequest):

#     sql = """
#         SELECT name, auth
#         FROM members
#         WHERE account = :account AND pwd = :pwd
#         LIMIT 1
#     """

#     try:
#         user_data = execute_query(
#             sql,
#             {
#                 "account": request.username,
#                 "pwd": request.password
#             },
#             fetch_one=True
#         )
#     except Exception as e:
#         print(f"❌ 登入查詢資料庫失敗: {e}")
#         raise HTTPException(status_code=500, detail="伺服器錯誤: 資料庫連線失敗")

#     if not user_data:
#         print(f"request.username=",request.username)
#         print(f"request.password=",request.password)
#         raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

#     return {
#         "message": "登入成功",
#         "user": {
#             "name": user_data["name"],
#             "auth": user_data["auth"],
#             "username": request.username
#         }
#     }
# endregion


# region 使用者登入
@app.post("/api/user_login")
def user_login(request: LoginRequest):

    sql = """
        SELECT id, account, pwd, name, auth
        FROM members
        WHERE account = :account
        LIMIT 1
    """
    
    user = execute_query(sql, {"account": request.username}, fetch_one=True)

    if not user:
        raise HTTPException(401, "帳號錯誤")

    # ✅ 明碼比對
    if request.password != user["pwd"]:
        raise HTTPException(401, "密碼錯誤")

    # ✅ ✅ ✅ 🔥 這裡加入 JWT（關鍵）
    token = create_access_token({
        "user_id": user["id"],
        "account": user["account"],
        "auth": user["auth"]
    })

    # ✅ 回傳 token + user
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "auth": user["auth"]
        }
    }

# endregion

# region JWT 設定 

import os
from dotenv import load_dotenv
from jose import jwt, JWTError
from fastapi.security import HTTPBearer
from fastapi import Depends, HTTPException
from datetime import datetime, timedelta

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise ValueError("❌ SECRET_KEY 未設定")

ALGORITHM = "HS256"
# 設定登入15分鐘後token過期
ACCESS_TOKEN_EXPIRE_MINUTES = 15

security = HTTPBearer()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # expire = datetime.utcnow() + timedelta(seconds=5)
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token=Depends(security)):
    if not token:
        raise HTTPException(status_code=403, detail="未提供登入憑證")

    try:
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload

    except JWTError:
        raise HTTPException(status_code=403, detail="Token 無效或過期")

# endregion

# region 統一權限 dependency 
def require_roles(roles: list[str]):
    def checker(user=Depends(verify_token)):
        user_role = user.get("auth")

        # ✅ admin 全通
        if user_role == "admin":
            return user
        # 有除了自訂角色以外的角色時，返回權限不足，否則回傳user
        if user_role not in roles:
            raise HTTPException(
                status_code=403,
                detail="權限不足"
            )

        return user
    return checker
# endregion

# region MEMBERS - GET 

@app.get("/get_members", summary="查詢所有使用者")
def get_members(user=Depends(require_roles(["admin"]))):
    try:
        sql = """
            SELECT id, account, name, auth
            FROM members
            ORDER BY id
        """
        return execute_query(sql)

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch members: {e}")

# endregion

# region MEMBERS - CREATE 

@app.post("/create_member", summary="新增使用者")
def create_member(item: Member, user=Depends(require_roles(["admin"]))):

    sql = """
        INSERT INTO members (account, pwd, name, auth)
        VALUES (:account, :pwd, :name, :auth)
    """

    params = {
        "account": item.account.strip(),
        "pwd": item.pwd.strip(),  # ✅ 明碼存
        "name": item.name.strip(),
        "auth": item.auth.strip()
    }

    try:
        execute_query(sql, params)
        return {"message": "Member created successfully"}

    except UniqueConstraintError:
        raise HTTPException(409, "帳號已存在")

# endregion

# region MEMBERS - UPDATE 

@app.put("/update_member/{id}", summary="修改使用者")
def update_member(id: int, item: Member, user=Depends(require_roles(["admin"]))):

    sql = """
        UPDATE members
        SET account = :account,
            pwd = :pwd,
            name = :name,
            auth = :auth
        WHERE id = :id
    """

    params = {
        "id": id,
        "account": item.account.strip(),
        "pwd": item.pwd.strip(),
        "name": item.name.strip(),
        "auth": item.auth.strip()
    }

    result = execute_query(sql, params)

    if result == 0:
        raise HTTPException(404, "Member not found")

    return {"message": "Member updated successfully"}


# endregion

# region MEMBERS - DELETE 

@app.delete("/delete_member/{id}", summary="刪除使用者")
def delete_member(id: int, user=Depends(require_roles(["admin"]))):

    sql = """
        DELETE FROM members WHERE id = :id
    """

    result = execute_query(sql, {"id": id})

    if result == 0:
        raise HTTPException(404, "Member not found")

    return {"message": "Member deleted successfully"}

# endregion

# region depts - GET 

@app.get("/get_depts", summary="讀取所有系所資料及承辦人資訊")
def get_depts(user=Depends(require_roles(["curri", "user"]))):
    try:
        sql = """
        SELECT
            d.id,
            d.college,
            d.college_s,
            d.dept,
            d.dept_s,
            d.stype,
            d.agent_name,
            d.agent_ext,
            d.agent_email,
            ca.id AS cagent_id,
            ca.name AS cagent_name,
            ca.ext AS cagent_ext,
            ca.email AS cagent_email
        FROM depts d
        LEFT JOIN cagents ca ON d.cagent_id = ca.id
        ORDER BY d.id
        """
        return execute_query(sql)

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch departments: {e}")

# endregion

# region depts - CREATE 

@app.post("/create_dept", summary="新增系所資料")
def create_dept(item: DeptWithAgent,user=Depends(require_roles(["curri"]))):

    sql = """
        INSERT INTO depts (
            college, college_s, dept, dept_s, stype,
            agent_name, agent_ext, agent_email, cagent_id
        )
        VALUES (
            :college, :college_s, :dept, :dept_s, :stype,
            :agent_name, :agent_ext, :agent_email, :cagent_id
        )
    """

    params = {
        "college": item.college.strip(),
        "college_s": item.college_s.strip(),
        "dept": item.dept.strip(),
        "dept_s": item.dept_s.strip(),
        "stype": item.stype.strip(),
        "agent_name": item.agent_name.strip(),
        "agent_ext": item.agent_ext.strip(),
        "agent_email": item.agent_email.strip(),
        "cagent_id": item.cagent_id
    }

    try:
        execute_query(sql, params)
        return {"message": "Department added successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="系所名稱或簡稱已存在")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"資料庫錯誤: {e}")

# endregion

# region depts - UPDATE 

@app.put("/update_dept/{dept_id}", summary="修改指定 ID 的系所資料")
def update_dept(dept_id: int, item: DeptWithAgent, user=Depends(require_roles(["curri"]))):

    sql = """
        UPDATE depts
        SET
            college = :college,
            college_s = :college_s,
            dept = :dept,
            dept_s = :dept_s,
            stype = :stype,
            agent_name = :agent_name,
            agent_ext = :agent_ext,
            agent_email = :agent_email,
            cagent_id = :cagent_id
        WHERE id = :id
    """

    params = {
        "college": item.college.strip(),
        "college_s": item.college_s.strip(),
        "dept": item.dept.strip(),
        "dept_s": item.dept_s.strip(),
        "stype": item.stype.strip(),
        "agent_name": item.agent_name.strip(),
        "agent_ext": item.agent_ext.strip(),
        "agent_email": item.agent_email.strip(),
        "cagent_id": item.cagent_id,
        "id": dept_id
    }

    try:
        result = execute_query(sql, params)

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Department with ID {dept_id} not found.")

        return {"message": "Department updated successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="唯一約束衝突")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to update department: {e}")

# endregion

# region depts - DELETE 

@app.delete("/delete_dept/{dept_id}", summary="刪除指定 ID 的系所資料")
def delete_dept(dept_id: int, user=Depends(require_roles(["curri"]))):

    sql = """
        DELETE FROM depts
        WHERE id = :id
    """

    try:
        result = execute_query(sql, {"id": dept_id})

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Department with ID {dept_id} not found.")

        return {"message": "Department deleted successfully."}

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete department: {e}")

# endregion

# region cagents - GET 

@app.get("/get_cagents", summary="查詢所有課務組承辦人資料")
def get_cagents(user=Depends(require_roles(["admin"]))):
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

# region cagents - CREATE 

@app.post("/create_cagent", summary="新增課務組承辦人資料")
def create_cagent(item: CAgent, user=Depends(require_roles(["admin"]))):

    sql = """
        INSERT INTO cagents (name, ext, email)
        VALUES (:name, :ext, :email)
    """

    params = {
        "name": item.name.strip(),
        "ext": item.ext.strip(),
        "email": item.email.strip()
    }

    try:
        execute_query(sql, params)
        return {"message": "Curri agent added successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="唯一約束衝突 (可能姓名或 Email 已存在)")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"資料庫錯誤: {e}")

# endregion

# region cagents - UPDATE 

@app.put("/update_cagent/{cagent_id}", summary="修改指定 ID 的課務組承辦人資料")
def update_cagent(cagent_id: int, item: CAgent, user=Depends(require_roles(["admin"]))):

    sql = """
        UPDATE cagents
        SET name = :name,
            ext = :ext,
            email = :email
        WHERE id = :id
    """

    params = {
        "name": item.name.strip(),
        "ext": item.ext.strip(),
        "email": item.email.strip(),
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

# region cagents - DELETE 

@app.delete("/delete_cagent/{cagent_id}", summary="刪除指定 ID 的課務組承辦人資料")
def delete_cagent(cagent_id: int, user=Depends(require_roles(["admin"]))):

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

# region map_cls_dept - GET 

@app.get("/get_map_cls_dept", summary="查詢所有班級-系所簡稱對照資料")
def get_map_cls_dept(user=Depends(verify_token)):
    try:
        sql = """
            SELECT
                id,
                class AS cls,
                dept_s
            FROM map_cls_dept
            ORDER BY id
        """
        return execute_query(sql)

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch class-dept mapping: {e}")

# endregion

# region map_cls_dept - CREATE 

@app.post("/create_map_cls_dept", summary="新增班級-系所簡稱對照")
def create_map_cls_dept(item: MapClsDept, user=Depends(require_roles(["curri"]))):

    sql = """
        INSERT INTO map_cls_dept (class, dept_s)
        VALUES (:class, :dept_s)
    """

    params = {
        "class": item.cls.strip(),   # ⚠️ cls → class mapping
        "dept_s": item.dept_s.strip()
    }

    try:
        execute_query(sql, params)
        return {"message": "Class-dept mapping added successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="班級與系所簡稱已存在")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"資料庫錯誤: {e}")

# endregion

# region map_cls_dept - UPDATE 

@app.put("/update_map_cls_dept/{map_cls_dept_id}", summary="修改指定 ID 的班級-系所簡稱對照")
def update_map_cls_dept(map_cls_dept_id: int, item: MapClsDept, user=Depends(require_roles(["curri"]))):

    sql = """
        UPDATE map_cls_dept
        SET
            class = :class,
            dept_s = :dept_s
        WHERE id = :id
    """

    params = {
        "class": item.cls.strip(),
        "dept_s": item.dept_s.strip(),
        "id": map_cls_dept_id
    }

    try:
        result = execute_query(sql, params)

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Mapping with ID {map_cls_dept_id} not found.")

        return {"message": "Class-dept mapping updated successfully."}

    except UniqueConstraintError:
        raise HTTPException(status_code=409, detail="唯一約束衝突")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to update mapping: {e}")

# endregion

# region map_cls_dept - DELETE 

@app.delete("/delete_map_cls_dept/{map_cls_dept_id}", summary="刪除指定 ID 的班級-系所簡稱對照")
def delete_map_cls_dept(map_cls_dept_id: int, user=Depends(require_roles(["curri"]))):

    sql = """
        DELETE FROM map_cls_dept
        WHERE id = :id
    """

    try:
        result = execute_query(sql, {"id": map_cls_dept_id})

        if result == 0:
            raise HTTPException(status_code=404, detail=f"Mapping with ID {map_cls_dept_id} not found.")

        return {"message": "Class-dept mapping deleted successfully."}

    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete mapping: {e}")

# endregion

# region get_all_data 系所班級轉換用 
@app.get("/get_all_data")
def get_all_data(user=Depends(require_roles(["curri", "user"]))):
    try:
        sql = """
            SELECT
                c.class AS "CLASS",
                c.dept_s AS "DEPT_S",
                d.dept AS "DEPT",
                d.college AS "COLLEGE",
                d.college_s AS "COLLEGE_S",
                d.agent_name AS "AGENT_NAME",
                d.agent_ext AS "AGENT_EXT",
                d.agent_email AS "AGENT_EMAIL",
                ca.id AS "CAGENT_ID",
                ca.name AS "CAGENT_NAME",
                ca.ext AS "CAGENT_EXT",
                ca.email AS "CAGENT_EMAIL"
            FROM map_cls_dept c
            JOIN depts d ON c.dept_s = d.dept_s
            JOIN cagents ca ON ca.id = d.cagent_id
        """

        return execute_query(sql)

    except DatabaseError as e:
        raise HTTPException(500, f"讀取資料失敗: {e}")
# endregion




print(f"curridata_server已啟動，等候客戶端訪問中...")