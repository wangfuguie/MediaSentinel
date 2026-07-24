import os
import time
import subprocess
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error
from whisper_setup import ensure_whisper_cpp

load_dotenv()

# ---------------------- Whisper CLI 包裝器 ----------------------
class ASRWrapper:
    def __init__(self, model_path=None, lang="zh", threads="4"):
        whisper_cli, default_model = ensure_whisper_cpp()
        self.model_path = model_path or default_model
        self.lang = lang
        self.threads = threads
        self.whisper_cli = whisper_cli
        
        # 檢查 whisper-cli 是否存在
        if not os.path.exists(self.whisper_cli):
            raise FileNotFoundError(f"找不到 whisper-cli: {self.whisper_cli}")
        
        # 檢查模型檔案是否存在
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"找不到模型檔案: {self.model_path}")

    def transcribe(self, audio_file_path):
        """使用 whisper-cli 轉錄音頻檔案"""
        command = [
            self.whisper_cli,
            "-m", self.model_path,
            "-f", audio_file_path,
            "-l", self.lang,
            "-t", self.threads
        ]
        
        try:
            result = subprocess.run(
                command, 
                check=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True,
                timeout=300  # 5分鐘超時
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            raise Exception(f"whisper-cli 轉錄超時: {audio_file_path}")
        except subprocess.CalledProcessError as e:
            raise Exception(f"whisper-cli 執行錯誤: {e.stderr}")

    def get_text(self, whisper_output):
        """從 whisper-cli 輸出中提取純文字"""
        if not whisper_output:
            return ""
        
        text_parts = []
        lines = whisper_output.splitlines()
        
        for line in lines:
            # 使用正則表達式過濾掉時間戳，只保留文字
            match = re.search(r'\] *(.*)', line)
            if match:
                text_content = match.group(1).strip()
                if text_content:  # 只加入非空文字
                    text_parts.append(text_content)
        
        return " ".join(text_parts).strip()

# ---------------------- MySQL 設定 ----------------------
class MySQLWrapper:
    def __init__(self, host=None, port=None, database='news_24hr', 
                 user=None, password=None):
        host = host or os.getenv("MYSQL_HOST", "localhost")
        port = port or int(os.getenv("MYSQL_PORT", "3306"))
        user = user or os.getenv("MYSQL_USER", "root")
        password = password if password is not None else os.getenv("MYSQL_PASSWORD", "")
        self.config = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password,
            'charset': 'utf8mb4',
            'collation': 'utf8mb4_unicode_ci'
        }
        self.init_database()
    
    def get_connection(self):
        """建立資料庫連線"""
        try:
            connection = mysql.connector.connect(**self.config)
            return connection
        except Error as e:
            print(f"MySQL 連線錯誤: {e}")
            return None
    
    def init_database(self):
        """初始化資料庫和資料表"""
        connection = None
        cursor = None
        try:
            print(f"[{datetime.utcnow()}] 🔗 測試 MySQL 連線...")
            connection = self.get_connection()
            if not connection:
                raise Exception("無法建立 MySQL 連線")
                
            cursor = connection.cursor()
            
            # 測試連線
            cursor.execute("SELECT 1")
            cursor.fetchone()
            print(f"[{datetime.utcnow()}] ✅ MySQL 連線測試成功")
            
            # 建立資料表
            create_table_query = """
            CREATE TABLE IF NOT EXISTS whisper_transcripts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_timestamp (timestamp)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
            
            cursor.execute(create_table_query)
            connection.commit()
            print(f"[{datetime.utcnow()}] ✅ MySQL 資料表初始化成功")
            
        except Error as e:
            error_msg = f"MySQL 初始化錯誤 ({e.errno}): {e.msg}"
            print(f"[{datetime.utcnow()}] ❌ {error_msg}")
            
            # 提供常見錯誤的解決建議
            if e.errno == 1045:  # Access denied
                print("💡 解決建議：")
                print("   1. 檢查使用者名稱和密碼是否正確")
                print("   2. 確認 MySQL 使用者有足夠權限")
                print("   3. 可嘗試使用其他認證方式：auth_plugin='mysql_native_password'")
            elif e.errno == 1049:  # Unknown database
                print("💡 解決建議：請先建立資料庫 'news_24hr'")
                print("   SQL: CREATE DATABASE news_24hr CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            elif e.errno == 2003:  # Can't connect to MySQL server
                print("💡 解決建議：")
                print("   1. 檢查 MySQL 服務是否正在運行")
                print("   2. 確認主機和連接埠設定是否正確")
            
            raise
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def insert_transcript(self, timestamp, content):
        """插入轉錄資料"""
        try:
            connection = self.get_connection()
            if connection:
                cursor = connection.cursor()
                
                insert_query = """
                INSERT INTO whisper_transcripts (timestamp, content) 
                VALUES (%s, %s)
                """
                
                cursor.execute(insert_query, (timestamp, content))
                connection.commit()
                
                return cursor.lastrowid
                
        except Error as e:
            print(f"[{datetime.utcnow()}] ❌ MySQL 插入錯誤: {e}")
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()

# ---------------------- 主處理邏輯 ----------------------
WAV_DIR = os.getenv("WAV_DIR", "wav_24hr")
os.makedirs(WAV_DIR, exist_ok=True)
processed = set()
MIN_FILES = 10

# ---------------------- MySQL 連線設定 ----------------------
MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_HOST", "localhost"),
    'port': int(os.getenv("MYSQL_PORT", "3306")),
    'database': 'news_24hr',
    'user': os.getenv("MYSQL_USER", "root"),
    'password': os.getenv("MYSQL_PASSWORD", "")
}

# 初始化資料庫連線
try:
    print(f"[{datetime.utcnow()}] 🔗 嘗試連線 MySQL: {MYSQL_CONFIG['user']}@{MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
    db = MySQLWrapper(**MYSQL_CONFIG)
    print(f"[{datetime.utcnow()}] ✅ MySQL 連線初始化成功")
except Exception as e:
    print(f"[{datetime.utcnow()}] ❌ MySQL 初始化失敗: {e}")
    print("請檢查以下設定：")
    print("1. MySQL 服務是否正在運行")
    print("2. 資料庫名稱是否正確")
    print("3. 使用者名稱和密碼是否正確")
    print("4. 使用者是否有存取該資料庫的權限")
    exit(1)

# 初始化 ASR 包裝器
try:
    asr = ASRWrapper(
        lang="zh", 
        threads="4"
    )
    print(f"[{datetime.utcnow()}] ✅ Whisper CLI 初始化成功")
except Exception as e:
    print(f"[{datetime.utcnow()}] ❌ Whisper CLI 初始化失敗: {e}")
    exit(1)

def transcribe_existing_wavs(interval=5):
    print(f"[{datetime.utcnow()}] ▶️ 開始轉錄任務，每次需至少 {MIN_FILES} 筆新檔案")

    while True:
        try:
            files = sorted(f for f in os.listdir(WAV_DIR) if f.endswith(".wav"))
            new_files = [f for f in files if f not in processed]

            if len(new_files) < MIN_FILES:
                print(f"[{datetime.utcnow()}] ⏳ 等待中，目前只有 {len(new_files)} 筆新檔案，至少需要 {MIN_FILES} 筆")
                time.sleep(interval)
                continue

            content_texts = []

            for fname in new_files[:MIN_FILES]:
                wav_path = os.path.join(WAV_DIR, fname)
                try:
                    print(f"[{datetime.utcnow()}] 🎵 處理音頻檔案: {fname}")
                    
                    # 使用 whisper-cli 轉錄
                    whisper_output = asr.transcribe(wav_path)
                    text = asr.get_text(whisper_output)

                    if text:
                        content_texts.append(text)
                        print(f"[{datetime.utcnow()}] ✅ 轉錄成功: {fname} -> {len(text)} 字元")
                    else:
                        print(f"[{datetime.utcnow()}] ⚠️ 無辨識輸出: {fname}")

                    os.remove(wav_path)  # ✅ 無論是否有內容都刪除
                    processed.add(fname)

                except Exception as e:
                    print(f"[{datetime.utcnow()}] ❌ 錯誤處理 {fname}: {e}")
                    # 如果轉錄失敗，仍然刪除檔案並標記為已處理，避免重複處理
                    try:
                        os.remove(wav_path)
                        processed.add(fname)
                    except:
                        pass

            if content_texts:
                timestamp = datetime.utcnow() + timedelta(hours=8)
                content = "\n\n".join(content_texts)
                
                record_id = db.insert_transcript(timestamp, content)
                if record_id:
                    print(f"[{datetime.utcnow()}] ✅ 儲存 {len(content_texts)} 筆內容到 MySQL (ID: {record_id})")
                else:
                    print(f"[{datetime.utcnow()}] ❌ MySQL 儲存失敗")
            else:
                print(f"[{datetime.utcnow()}] ⚠️ 本輪無有效轉錄內容")

            time.sleep(interval)

        except KeyboardInterrupt:
            print("🛑 使用者中斷，結束程式")
            break
        except Exception as e:
            print(f"[{datetime.utcnow()}] ❌ 執行錯誤: {e}")
            time.sleep(interval)

if __name__ == "__main__":
    transcribe_existing_wavs()
