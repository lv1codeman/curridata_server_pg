# 雲端資料庫

已改由雲端資料庫 supabase 維護，連線字串存在.env
SECRET_KEY 用於 JWT(login token)

# database_helper_pg.py

管理資料庫連線和游標的上下文管理器(context manager)

# 本地開發

本地開發時執行  
uvicorn main:app --host 0.0.0.0 --port 8000
