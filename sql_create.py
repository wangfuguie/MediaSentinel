#!/usr/bin/env python3
"""
news_24hr 資料庫自動建立工具
專門為 Whisper 轉錄系統建立所需的 MySQL 資料庫和資料表
"""

import os
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import time
import uuid

load_dotenv()

def check_and_create_database():
    """自動檢查並建立 news_24hr 資料庫和所需資料表"""
    
    # 連線設定（不指定資料庫，用於檢查和建立新資料庫）
    config = {
        'host': os.getenv("MYSQL_HOST", "localhost"),
        'port': int(os.getenv("MYSQL_PORT", "3306")),
        'user': os.getenv("MYSQL_USER", "root"),
        'password': os.getenv("MYSQL_PASSWORD", ""),
        'charset': 'utf8mb4',
        'collation': 'utf8mb4_unicode_ci'
    }
    
    connection = None
    cursor = None
    
    try:
        print(f"[{datetime.now()}] 🔗 連線到 MySQL 伺服器...")
        connection = mysql.connector.connect(**config)
        cursor = connection.cursor()
        
        # 檢查資料庫是否存在
        cursor.execute("SHOW DATABASES LIKE 'news_24hr'")
        db_exists = cursor.fetchone()
        
        if not db_exists:
            print(f"[{datetime.now()}] 📊 資料庫 news_24hr 不存在，正在建立...")
            cursor.execute("CREATE DATABASE news_24hr CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            print(f"[{datetime.now()}] ✅ 資料庫建立成功")
        else:
            print(f"[{datetime.now()}] 📊 資料庫 news_24hr 已存在")
        
        # 切換到資料庫
        cursor.execute("USE news_24hr")
        
        # 建立 whisper_transcripts 資料表（主要轉錄資料）
        if not table_exists(cursor, 'whisper_transcripts'):
            print(f"[{datetime.now()}] 📋 建立 whisper_transcripts 資料表...")
            create_whisper_table_sql = """
            CREATE TABLE whisper_transcripts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME NOT NULL COMMENT '轉錄時間戳',
                content TEXT NOT NULL COMMENT '轉錄內容',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '資料建立時間',
                INDEX idx_timestamp (timestamp),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            COMMENT='Whisper 音頻轉錄結果儲存表'
            """
            cursor.execute(create_whisper_table_sql)
            print(f"[{datetime.now()}] ✅ whisper_transcripts 資料表建立成功")
        else:
            print(f"[{datetime.now()}] 📋 whisper_transcripts 資料表已存在")
        
        # 建立 clean_news 資料表（清理後的新聞）
        if not table_exists(cursor, 'clean_news'):
            print(f"[{datetime.now()}] 📋 建立 clean_news 資料表...")
            create_clean_news_table_sql = """
            CREATE TABLE clean_news (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp VARCHAR(50) NOT NULL COMMENT '唯一時間戳標識',
                final_content TEXT NOT NULL COMMENT '經過三階段處理的最終內容',
                source_timestamp VARCHAR(100) COMMENT '來源時間戳範圍',
                processed_at DATETIME NOT NULL COMMENT '處理完成時間（精確到秒）',
                INDEX idx_timestamp (timestamp),
                INDEX idx_processed_at (processed_at),
                UNIQUE KEY uk_timestamp (timestamp)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            COMMENT='經過三階段清理處理的新聞內容表';
            """
            cursor.execute(create_clean_news_table_sql)
            print(f"[{datetime.now()}] ✅ clean_news 資料表建立成功")
        else:
            print(f"[{datetime.now()}] 📋 clean_news 資料表已存在")
        
        # 檢查並建立 news_3period 資料庫
        cursor.execute("SHOW DATABASES LIKE 'news_3period'")
        db_3period_exists = cursor.fetchone()
        
        if not db_3period_exists:
            print(f"[{datetime.now()}] 📊 資料庫 news_3period 不存在，正在建立...")
            cursor.execute("CREATE DATABASE news_3period CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            print(f"[{datetime.now()}] ✅ 資料庫 news_3period 建立成功")
        else:
            print(f"[{datetime.now()}] 📊 資料庫 news_3period 已存在")
        
        # 檢查並建立 random_event 資料庫
        cursor.execute("SHOW DATABASES LIKE 'random_event'")
        db_random_event_exists = cursor.fetchone()
        
        if not db_random_event_exists:
            print(f"[{datetime.now()}] 📊 資料庫 random_event 不存在，正在建立...")
            cursor.execute("CREATE DATABASE random_event CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            print(f"[{datetime.now()}] ✅ 資料庫 random_event 建立成功")
        else:
            print(f"[{datetime.now()}] 📊 資料庫 random_event 已存在")
        
        # 切換到 random_event 資料庫建立 clean_news 資料表（影片爬取專用）
        cursor.execute("USE random_event")
        
        if not table_exists(cursor, 'clean_news'):
            print(f"[{datetime.now()}] 📋 建立 random_event.clean_news 資料表...")
            create_random_event_clean_news_sql = """
            CREATE TABLE clean_news (
                id INT AUTO_INCREMENT PRIMARY KEY,
                video_id VARCHAR(255) NOT NULL COMMENT '影片唯一識別碼',
                title VARCHAR(500) NOT NULL COMMENT '影片標題',
                original_url VARCHAR(1000) NOT NULL COMMENT '原始影片連結',
                duration VARCHAR(50) COMMENT '影片時長',
                date VARCHAR(50) COMMENT '影片日期',
                cleaned_content TEXT NOT NULL COMMENT '清理後的轉錄內容',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '資料建立時間',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '資料更新時間',
                INDEX idx_video_id (video_id),
                INDEX idx_created_at (created_at),
                INDEX idx_title (title(100)),
                UNIQUE KEY uk_video_id (video_id)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            COMMENT='網頁爬取影片的清理轉錄內容表'
            """
            cursor.execute(create_random_event_clean_news_sql)
            print(f"[{datetime.now()}] ✅ random_event.clean_news 資料表建立成功")
        else:
            print(f"[{datetime.now()}] 📋 random_event.clean_news 資料表已存在")
        
        # 切換到 news_3period 資料庫建立時段資料表
        cursor.execute("USE news_3period")
        
        # 建立三個時段資料表的通用結構
        period_table_sql_template = """
        CREATE TABLE {period} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp VARCHAR(20) NOT NULL COMMENT '新聞時間戳',
            content TEXT NOT NULL COMMENT '清理後的轉錄內容',
            news_title VARCHAR(500) NOT NULL COMMENT '新聞標題',
            news_url TEXT COMMENT '新聞來源URL',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '資料建立時間',
            INDEX idx_timestamp (timestamp),
            INDEX idx_created_at (created_at),
            INDEX idx_news_title (news_title(100))
        ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        COMMENT='{period_zh}時段新聞轉錄資料表'
        """
        
        # 建立各時段資料表
        periods = [
            ('morning', '早晨'),
            ('noon', '中午'),
            ('night', '夜晚')
        ]
        
        for period, period_zh in periods:
            if not table_exists(cursor, period):
                print(f"[{datetime.now()}] 📋 建立 {period} 資料表...")
                period_table_sql = period_table_sql_template.format(
                    period=period, 
                    period_zh=period_zh
                )
                cursor.execute(period_table_sql)
                print(f"[{datetime.now()}] ✅ {period} 資料表建立成功")
            else:
                print(f"[{datetime.now()}] 📋 {period} 資料表已存在")
        
        connection.commit()
        
        # 驗證資料表
        print(f"\n[{datetime.now()}] 🔍 驗證資料表狀態...")
        
        # 驗證 news_24hr 資料庫中的資料表
        cursor.execute("USE news_24hr")
        required_tables_24hr = ['whisper_transcripts', 'clean_news']
        existing_tables_24hr = get_existing_tables(cursor)
        
        print(f"📊 news_24hr 資料庫:")
        all_tables_exist = True
        for table in required_tables_24hr:
            if table in existing_tables_24hr:
                cursor.execute(f"DESCRIBE {table}")
                columns = cursor.fetchall()
                print(f"✅ {table} - 正常 (共 {len(columns)} 個欄位)")
                
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"   📊 目前資料筆數: {count}")
            else:
                print(f"❌ {table} - 缺失")
                all_tables_exist = False
        
        # 驗證 news_3period 資料庫中的資料表
        cursor.execute("USE news_3period")
        required_tables_3period = ['morning', 'noon', 'night']
        existing_tables_3period = get_existing_tables(cursor)
        
        print(f"📊 news_3period 資料庫:")
        for table in required_tables_3period:
            if table in existing_tables_3period:
                cursor.execute(f"DESCRIBE {table}")
                columns = cursor.fetchall()
                print(f"✅ {table} - 正常 (共 {len(columns)} 個欄位)")
                
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"   📊 目前資料筆數: {count}")
            else:
                print(f"❌ {table} - 缺失")
                all_tables_exist = False
        
        # 驗證 random_event 資料庫中的資料表
        try:
            cursor.execute("USE random_event")
            required_tables_random_event = ['clean_news']
            existing_tables_random_event = get_existing_tables(cursor)
            
            print(f"📊 random_event 資料庫:")
            for table in required_tables_random_event:
                if table in existing_tables_random_event:
                    cursor.execute(f"DESCRIBE {table}")
                    columns = cursor.fetchall()
                    print(f"✅ {table} - 正常 (共 {len(columns)} 個欄位)")
                    
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    print(f"   📊 目前資料筆數: {count}")
                else:
                    print(f"❌ {table} - 缺失")
                    all_tables_exist = False
        except Error as e:
            print(f"\n❌ 無法存取 random_event 資料庫: {e}")
            all_tables_exist = False
        
        if all_tables_exist:
            print(f"\n[{datetime.now()}] 🎉 所有必要的資料表都已就緒！")
            return True
        else:
            print(f"\n[{datetime.now()}] ❌ 部分資料表建立失敗")
            return False
            
    except Error as e:
        print(f"[{datetime.now()}] ❌ MySQL 錯誤 ({e.errno}): {e.msg}")
        
        # 提供詳細的錯誤解決建議
        if e.errno == 1045:  # Access denied
            print("💡 解決建議：")
            print("   1. 檢查 MySQL 使用者名稱和密碼是否正確")
            print("   2. 確認 MySQL 使用者有足夠權限")
            print("   3. 嘗試重新設定 MySQL 密碼")
            print("   4. 可嘗試使用其他認證方式：auth_plugin='mysql_native_password'")
        elif e.errno == 2003:  # Can't connect to MySQL server
            print("💡 解決建議：")
            print("   1. 檢查 MySQL 服務是否正在運行")
            print("   2. 確認主機和連接埠設定是否正確")
            print("   3. macOS: brew services start mysql")
        elif e.errno == 1044:  # Access denied for user to database
            print("💡 解決建議：")
            print("   1. 使用者可能沒有建立資料庫的權限")
            print("   2. 請聯繫資料庫管理員或使用具有完整權限的帳號")
        elif e.errno == 1049:  # Unknown database
            print("💡 這個錯誤不應該出現在此階段，請檢查程式邏輯")
        
        return False
        
    except Exception as e:
        print(f"[{datetime.now()}] ❌ 未預期的錯誤: {e}")
        return False
        
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()
            print(f"[{datetime.now()}] 🔗 MySQL 連線已關閉")

def table_exists(cursor, table_name):
    """檢查資料表是否存在"""
    try:
        cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
        return cursor.fetchone() is not None
    except Error:
        return False

def get_existing_tables(cursor):
    """取得現有的資料表列表"""
    try:
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        return [table[0] for table in tables]
    except Error:
        return []

def verify_database_setup():
    """驗證資料庫設定是否完整"""
    config = {
        'host': os.getenv("MYSQL_HOST", "localhost"),
        'port': int(os.getenv("MYSQL_PORT", "3306")),
        'user': os.getenv("MYSQL_USER", "root"),
        'password': os.getenv("MYSQL_PASSWORD", ""),
        'charset': 'utf8mb4'
    }
    
    try:
        print(f"[{datetime.now()}] 🔍 驗證資料庫連線...")
        connection = mysql.connector.connect(**config)
        cursor = connection.cursor()
        
        # 測試基本連線
        cursor.execute("SELECT 1")
        cursor.fetchone()
        
        # 檢查 news_24hr 資料庫的資料表
        try:
            cursor.execute("USE news_24hr")
            required_tables_24hr = ['whisper_transcripts', 'clean_news']
            missing_tables_24hr = []
            
            for table in required_tables_24hr:
                if not table_exists(cursor, table):
                    missing_tables_24hr.append(table)
        except Error:
            missing_tables_24hr = required_tables_24hr
        
        # 檢查 news_3period 資料庫的資料表
        try:
            cursor.execute("USE news_3period")
            required_tables_3period = ['morning', 'noon', 'night']
            missing_tables_3period = []
            
            for table in required_tables_3period:
                if not table_exists(cursor, table):
                    missing_tables_3period.append(table)
        except Error:
            missing_tables_3period = required_tables_3period
        
        # 檢查 random_event 資料庫的資料表
        try:
            cursor.execute("USE random_event")
            required_tables_random_event = ['clean_news']
            missing_tables_random_event = []
            
            for table in required_tables_random_event:
                if not table_exists(cursor, table):
                    missing_tables_random_event.append(table)
        except Error:
            missing_tables_random_event = required_tables_random_event
        
        # 顯示 random_event 資料庫資訊
        try:
            cursor.execute("USE random_event")
            cursor.execute("SHOW TABLES")
            tables_random_event = cursor.fetchall()
            print(f"\n📊 random_event 資料庫:")
            print(f"   資料表數量: {len(tables_random_event)}")
            
            for table in tables_random_event:
                table_name = table[0]
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"   - {table_name}: {count} 筆資料")
        except Error as e:
            print(f"\n❌ 無法存取 random_event 資料庫: {e}")
        
        cursor.close()
        connection.close()
        
        if missing_tables_24hr or missing_tables_3period or missing_tables_random_event:
            if missing_tables_24hr:
                print(f"[{datetime.now()}] ⚠️ news_24hr 缺少資料表: {', '.join(missing_tables_24hr)}")
            if missing_tables_3period:
                print(f"[{datetime.now()}] ⚠️ news_3period 缺少資料表: {', '.join(missing_tables_3period)}")
            if missing_tables_random_event:
                print(f"[{datetime.now()}] ⚠️ random_event 缺少資料表: {', '.join(missing_tables_random_event)}")
            return False
        else:
            print(f"[{datetime.now()}] ✅ 所有資料庫驗證通過")
            return True
            
    except Error as e:
        print(f"[{datetime.now()}] ❌ 資料庫驗證失敗 ({e.errno}): {e.msg}")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] ❌ 驗證過程發生錯誤: {e}")
        return False

def test_database_operations():
    """測試資料庫基本操作"""
    config = {
        'host': os.getenv("MYSQL_HOST", "localhost"),
        'port': int(os.getenv("MYSQL_PORT", "3306")),
        'user': os.getenv("MYSQL_USER", "root"),
        'password': os.getenv("MYSQL_PASSWORD", ""),
        'charset': 'utf8mb4'
    }
    
    try:
        print(f"[{datetime.now()}] 🧪 測試資料庫操作...")
        connection = mysql.connector.connect(**config)
        cursor = connection.cursor()
        
        # 測試 news_24hr 資料庫
        cursor.execute("USE news_24hr")
        test_timestamp = datetime.now()
        test_content = "測試轉錄內容 - Database Setup Test"
        
        # 測試 whisper_transcripts 表
        insert_query = """
        INSERT INTO whisper_transcripts (timestamp, content) 
        VALUES (%s, %s)
        """
        
        cursor.execute(insert_query, (test_timestamp, test_content))
        connection.commit()
        test_id = cursor.lastrowid
        
        print(f"[{datetime.now()}] ✅ whisper_transcripts 測試插入成功 (ID: {test_id})")
        
        # 查詢測試
        cursor.execute("SELECT id, timestamp, content FROM whisper_transcripts WHERE id = %s", (test_id,))
        result = cursor.fetchone()
        
        if result:
            print(f"[{datetime.now()}] ✅ whisper_transcripts 測試查詢成功")
        else:
            print(f"[{datetime.now()}] ❌ whisper_transcripts 測試查詢失敗")
            return False
        
        # 清理測試資料
        cursor.execute("DELETE FROM whisper_transcripts WHERE id = %s", (test_id,))
        connection.commit()
        
        # 測試 clean_news 表
        test_timestamp_unique = test_timestamp.strftime("%Y%m%d_%H%M%S_%f") + "_000"
        test_final_content = "測試清理後的新聞內容 - Database Setup Test"
        test_source_timestamp = "20241201_100000-20241201_101500"
        
        insert_clean_query = """
        INSERT INTO clean_news (timestamp, final_content, source_timestamp, processed_at) 
        VALUES (%s, %s, %s, %s)
        """
        
        cursor.execute(insert_clean_query, (test_timestamp_unique, test_final_content, test_source_timestamp, test_timestamp))
        connection.commit()
        clean_test_id = cursor.lastrowid
        
        print(f"[{datetime.now()}] ✅ clean_news 測試插入成功 (ID: {clean_test_id})")
        
        # 查詢測試
        cursor.execute("SELECT id, timestamp, final_content FROM clean_news WHERE id = %s", (clean_test_id,))
        clean_result = cursor.fetchone()
        
        if clean_result:
            print(f"[{datetime.now()}] ✅ clean_news 測試查詢成功")
        else:
            print(f"[{datetime.now()}] ❌ clean_news 測試查詢失敗")
            return False
        
        # 清理測試資料
        cursor.execute("DELETE FROM clean_news WHERE id = %s", (clean_test_id,))
        connection.commit()
        
        # 測試 random_event 資料庫
        cursor.execute("USE random_event")
        test_video_id = str(uuid.uuid4())
        test_title = "測試影片標題 - Database Setup Test"
        test_url = "https://test.example.com/video"
        test_duration = "05:30"
        test_date = "2024-12-01"
        test_cleaned_content = "測試清理後的影片轉錄內容 - Database Setup Test"
        
        insert_random_clean_query = """
        INSERT INTO clean_news (video_id, title, original_url, duration, date, cleaned_content)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        
        cursor.execute(insert_random_clean_query, (test_video_id, test_title, test_url, test_duration, test_date, test_cleaned_content))
        connection.commit()
        random_test_id = cursor.lastrowid
        
        print(f"[{datetime.now()}] ✅ random_event.clean_news 測試插入成功 (ID: {random_test_id})")
        
        # 查詢測試
        cursor.execute("SELECT id, video_id, title FROM clean_news WHERE id = %s", (random_test_id,))
        random_result = cursor.fetchone()
        
        if random_result:
            print(f"[{datetime.now()}] ✅ random_event.clean_news 測試查詢成功")
        else:
            print(f"[{datetime.now()}] ❌ random_event.clean_news 測試查詢失敗")
            return False
        
        # 清理測試資料
        cursor.execute("DELETE FROM clean_news WHERE id = %s", (random_test_id,))
        connection.commit()
        
        # 測試 news_3period 資料庫
        cursor.execute("USE news_3period")
        test_periods = ['morning', 'noon', 'night']
        
        for period in test_periods:
            test_title = f"測試新聞標題 - {period}"
            test_url = f"https://test.com/{period}"
            test_timestamp_str = str(int(time.time()))
            
            insert_query = f"""
            INSERT INTO {period} (timestamp, content, news_title, news_url) 
            VALUES (%s, %s, %s, %s)
            """
            
            cursor.execute(insert_query, (test_timestamp_str, test_content, test_title, test_url))
            connection.commit()
            period_test_id = cursor.lastrowid
            
            print(f"[{datetime.now()}] ✅ {period} 測試插入成功 (ID: {period_test_id})")
            
            # 清理測試資料
            cursor.execute(f"DELETE FROM {period} WHERE id = %s", (period_test_id,))
            connection.commit()
        
        print(f"[{datetime.now()}] 🧹 所有測試資料已清理")
        
        cursor.close()
        connection.close()
        return True
        
    except Error as e:
        print(f"[{datetime.now()}] ❌ 資料庫操作測試失敗: {e}")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] ❌ 測試過程發生錯誤: {e}")
        return False

def ensure_database_ready():
    """確保資料庫已準備就緒的主要函數"""
    print(f"[{datetime.now()}] 🚀 開始檢查資料庫狀態...")
    
    # 首先嘗試驗證現有設定
    if verify_database_setup():
        print(f"[{datetime.now()}] 📊 資料庫已就緒，無需建立")
        
        # 執行操作測試
        if test_database_operations():
            print(f"[{datetime.now()}] 🎉 資料庫功能測試通過！")
            return True
        else:
            print(f"[{datetime.now()}] ⚠️ 資料庫存在但功能測試失敗")
            return False
    
    # 如果驗證失敗，則建立缺失的部分
    print(f"[{datetime.now()}] 🔧 資料庫不完整，開始自動建立...")
    if check_and_create_database():
        # 再次驗證
        if verify_database_setup():
            # 執行操作測試
            if test_database_operations():
                print(f"[{datetime.now()}] 🎉 資料庫建立並測試成功！")
                return True
            else:
                print(f"[{datetime.now()}] ⚠️ 資料庫建立成功但功能測試失敗")
                return False
        else:
            print(f"[{datetime.now()}] ❌ 資料庫建立後驗證失敗")
            return False
    else:
        print(f"[{datetime.now()}] ❌ 資料庫建立失敗")
        return False

def show_database_info():
    """顯示資料庫相關資訊"""
    config = {
        'host': os.getenv("MYSQL_HOST", "localhost"),
        'port': int(os.getenv("MYSQL_PORT", "3306")),
        'user': os.getenv("MYSQL_USER", "root"),
        'password': os.getenv("MYSQL_PASSWORD", ""),
        'charset': 'utf8mb4'
    }
    
    try:
        connection = mysql.connector.connect(**config)
        cursor = connection.cursor()
        
        print(f"\n[{datetime.now()}] 📊 資料庫資訊:")
        print(f"   主機: {config['host']}:{config['port']}")
        print(f"   使用者: {config['user']}")
        print(f"   字元集: {config['charset']}")
        
        # 顯示 news_24hr 資料庫資訊
        try:
            cursor.execute("USE news_24hr")
            cursor.execute("SHOW TABLES")
            tables_24hr = cursor.fetchall()
            print(f"\n📊 news_24hr 資料庫:")
            print(f"   資料表數量: {len(tables_24hr)}")
            
            for table in tables_24hr:
                table_name = table[0]
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"   - {table_name}: {count} 筆資料")
        except Error as e:
            print(f"\n❌ 無法存取 news_24hr 資料庫: {e}")
        
        # 顯示 news_3period 資料庫資訊
        try:
            cursor.execute("USE news_3period")
            cursor.execute("SHOW TABLES")
            tables_3period = cursor.fetchall()
            print(f"\n📊 news_3period 資料庫:")
            print(f"   資料表數量: {len(tables_3period)}")
            
            for table in tables_3period:
                table_name = table[0]
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"   - {table_name}: {count} 筆資料")
        except Error as e:
            print(f"\n❌ 無法存取 news_3period 資料庫: {e}")
        
        cursor.close()
        connection.close()
        
    except Error as e:
        print(f"[{datetime.now()}] ❌ 無法取得資料庫資訊: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("🎯 news_24hr 資料庫自動建立工具")
    print("專為 Whisper 音頻轉錄系統設計")
    print("=" * 50)
    
    # 顯示設定資訊
    print(f"\n📋 設定資訊:")
    print(f"   MySQL 主機: {os.getenv('MYSQL_HOST', 'localhost')}:{os.getenv('MYSQL_PORT', '3306')}")
    print(f"   使用者: {os.getenv('MYSQL_USER', 'root')}")
    print(f"   密碼: {'set' if os.getenv('MYSQL_PASSWORD') else 'not set'}")
    print(f"   需要建立的資料庫:")
    print(f"     - news_24hr (whisper_transcripts, clean_news)")
    print(f"     - news_3period (morning, noon, night)")
    print(f"     - random_event (clean_news)")
    
    print("\n" + "─" * 50)
    
    if ensure_database_ready():
        show_database_info()
        print(f"\n[{datetime.now()}] 🚀 所有資料庫準備完成！")
        print("   您現在可以執行以下程式了:")
        print("   1. Whisper 轉錄程式 (使用 news_24hr.whisper_transcripts)")
        print("   2. 新聞分時段處理程式 (使用 news_3period.morning/noon/night)")
        print("   3. 新聞清理處理程式 (使用 news_24hr.clean_news)")
        print("   4. 網頁爬取轉錄程式 (使用 random_event.clean_news)")
        print("\n💡 使用提示:")
        print("   1. 確保 WAV 檔案目錄存在")
        print("   2. 確保 Whisper CLI 已正確安裝")
        print("   3. 確保 CSV 檔案格式正確")
        print("   4. 確保 FFmpeg 已安裝並配置")
        print("   5. 確保 Gemini API Key 已正確設定")
        print("   6. 確保 Chrome WebDriver 已安裝並配置")
        exit(0)
    else:
        print(f"\n[{datetime.now()}] ❌ 資料庫準備失敗")
        print("\n🔧 故障排除建議:")
        print("   1. 檢查 MySQL 服務是否正在運行")
        print("   2. 確認使用者名稱和密碼是否正確")
        print("   3. 確認使用者是否有建立資料庫的權限")
        print("   4. 檢查網路連接和防火牆設定")
        print("   5. 嘗試使用其他認證方式：auth_plugin='mysql_native_password'")
        print("   6. 確認可以手動建立資料庫：")
        print("      CREATE DATABASE news_24hr CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        print("      CREATE DATABASE news_3period CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        print("      CREATE DATABASE random_event CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        exit(1)