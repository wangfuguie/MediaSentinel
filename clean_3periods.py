import os
import re
import time
import subprocess
from dotenv import load_dotenv
import requests
import json
import pandas as pd
from datetime import datetime
import mysql.connector
from mysql.connector import Error
from concurrent.futures import ProcessPoolExecutor, as_completed
import threading
from whisper_setup import ensure_whisper_cpp
from llm_client import call_llm

load_dotenv()

# ========== Whisper CLI Wrapper ==========
class ASRWrapper:
    def __init__(self, model_path=None, lang="zh", threads="4"):
        whisper_cli, default_model = ensure_whisper_cpp()
        self.model_path = model_path or default_model
        self.lang = lang
        self.threads = threads
        self.whisper_cli = whisper_cli
        
        # 檢查必要檔案
        if not os.path.exists(self.whisper_cli):
            raise FileNotFoundError(f"找不到 whisper-cli: {self.whisper_cli}")
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"找不到模型檔案: {self.model_path}")

    def transcribe(self, audio_path):
        """使用 whisper-cli 轉錄音頻檔案"""
        command = [
            self.whisper_cli,
            "-m", self.model_path,
            "-f", audio_path,
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
            return self.get_text(result.stdout)
        except subprocess.TimeoutExpired:
            raise Exception(f"whisper-cli 轉錄超時: {audio_path}")
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


def clean_transcript_with_llm(content):
    prompt = f"""你是一個專業的文字編輯器，請對以下語音轉錄內容進行清理和優化。
1. 修正語音識別錯誤和錯別字（如：戰靈→占領、千創白孔→千瘡百孔、配以四列→以色列）
2. 添加適當的標點符號進行斷句，使文字更易閱讀
3. 修正不合理的字詞和語法錯誤
4. 統一使用簡體中文
5. 刪除明顯的亂碼和無意義重複
6. 保持原意不變，只做文字和語法的修正

**輸出要求：**
- 只輸出清理後的純文字內容
- 不要包含任何格式標記、程式碼或結構化標籤
- 確保文字流暢且易於理解
- 如果內容無法理解或修復，輸出：無法處理

現在請處理以下內容：
{content}"""

    try:
        result_text = call_llm(
            prompt, temperature=0.3, max_tokens=1500, timeout=30
        )
        if "無法處理" in result_text:
            return None
        return re.sub(r'\s+', ' ', result_text.strip())
    except Exception as e:
        print(f"❌ LLM 錯誤: {e}")
        return None


# ========== 時段分類 ==========
def determine_period_from_timestamp(ts):
    try:
        dt = datetime.fromtimestamp(int(ts))
    except:
        dt = datetime.utcnow()
    hour = dt.hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "noon"
    else:
        return "night"


# ========== MySQL 資料庫設定 ==========
class MySQLManager:
    def __init__(self, host=None, port=None, database='news_3period', 
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
            'autocommit': True,
            'pool_name': 'mypool',
            'pool_size': 5
        }
        self.test_connection()
    
    def get_connection(self):
        """建立資料庫連線"""
        try:
            connection = mysql.connector.connect(**self.config)
            return connection
        except Error as e:
            print(f"MySQL 連線錯誤: {e}")
            return None
    
    def test_connection(self):
        """測試資料庫連線"""
        connection = None
        cursor = None
        try:
            print(f"[{datetime.utcnow()}] 🔗 測試 MySQL 連線...")
            connection = self.get_connection()
            if not connection:
                raise Exception("無法建立 MySQL 連線")
                
            cursor = connection.cursor()
            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()
            print(f"[{datetime.utcnow()}] ✅ 成功連線到資料庫: {current_db[0]}")
            
            # 檢查資料表是否存在
            cursor.execute("SHOW TABLES")
            tables = [table[0] for table in cursor.fetchall()]
            expected_tables = ['morning', 'noon', 'night']
            
            missing_tables = [table for table in expected_tables if table not in tables]
            if missing_tables:
                print(f"[{datetime.utcnow()}] ⚠️ 缺少資料表: {missing_tables}")
            else:
                print(f"[{datetime.utcnow()}] ✅ 所有必要資料表都存在: {expected_tables}")
            
        except Error as e:
            print(f"[{datetime.utcnow()}] ❌ MySQL 連線測試失敗: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def insert_record(self, period, timestamp, content, news_title, news_url):
        """插入新聞轉錄記錄（避免重複）"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return None

            cursor = connection.cursor()

            # 檢查期間是否有效
            if period not in ['morning', 'noon', 'night']:
                print(f"❌ 無效的時段: {period}")
                return None

            # 檢查是否已存在相同的 news_title + timestamp
            check_query = f"""
            SELECT id FROM {period}
            WHERE news_title = %s AND timestamp = %s
            LIMIT 1
            """
            cursor.execute(check_query, (news_title, timestamp))
            result = cursor.fetchone()
            if result:
                print(f"⚠️ 資料已存在，跳過插入: {news_title[:30]}... ({period})")
                return None  # 不插入

            # 若不存在才插入
            insert_query = f"""
            INSERT INTO {period} (timestamp, content, news_title, news_url) 
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(insert_query, (timestamp, content, news_title, news_url))
            connection.commit()

            return cursor.lastrowid

        except Error as e:
            print(f"[{datetime.utcnow()}] ❌ MySQL 插入錯誤: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()


# 初始化 MySQL 管理器
try:
    mysql_manager = MySQLManager()
except Exception as e:
    print(f"❌ MySQL 管理器初始化失敗: {e}")
    print("請確認：")
    print("1. MySQL 服務正在運行")
    print("2. news_3period 資料庫已建立")
    print("3. 連線設定正確")
    exit(1)


# ========== CSV 管理器 ==========
import portalocker
if os.name != 'nt':
    import fcntl

def remove_processed_row_from_csv(csv_path, row_data):
    """從CSV中移除已處理的行（使用資料匹配而非索引）"""
    try:
        # 使用檔案鎖確保多進程安全
        lock_file = csv_path + '.lock'
        
        with open(lock_file, 'w') as lock_fd:
            try:
                # 嘗試取得獨占鎖
                if os.name == 'nt':  # Windows
                    import msvcrt
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # Unix/Linux/macOS
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                # 讀取當前CSV
                if not os.path.exists(csv_path):
                    print(f"⚠️ CSV檔案不存在: {csv_path}")
                    return False
                
                df = pd.read_csv(csv_path)
                original_count = len(df)
                
                # 使用多個欄位來唯一識別要移除的行
                mask = (
                    (df['news_title'] == row_data['news_title']) & 
                    (df['m3u8_url'] == row_data['m3u8_url']) &
                    (df['timestamp'].astype(str) == str(row_data['timestamp']))
                )
                
                # 檢查是否找到匹配的行
                matching_rows = df[mask]
                if len(matching_rows) == 0:
                    print(f"⚠️ 未找到匹配的行: {row_data['news_title'][:30]}...")
                    return False
                
                if len(matching_rows) > 1:
                    print(f"⚠️ 找到多個匹配行，只移除第一個: {row_data['news_title'][:30]}...")
                
                # 移除匹配的行（只移除第一個匹配項）
                first_match_idx = matching_rows.index[0]
                df = df.drop(index=first_match_idx)
                
                # 重新寫入CSV
                df.to_csv(csv_path, index=False)
                
                remaining_count = len(df)
                removed_count = original_count - remaining_count
                
                if removed_count > 0:
                    print(f"🗑️ 已從CSV移除 {removed_count} 筆資料，剩餘 {remaining_count} 筆")
                    return True
                else:
                    print(f"⚠️ 沒有資料被移除")
                    return False
                
            except (IOError, OSError):
                # 無法取得鎖，可能有其他進程在寫入
                print(f"⚠️ 暫時無法寫入CSV，其他進程正在使用")
                return False
                
    except Exception as e:
        print(f"❌ 移除CSV行失敗: {e}")
        return False
    finally:
        # 清理鎖檔案
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
        except:
            pass

def get_csv_current_count(csv_path):
    """取得當前CSV行數"""
    try:
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            return len(df)
        return 0
    except:
        return 0


# ========== 子任務處理 ==========
def transcribe_and_store(idx, row, csv_path):
    """處理單一新聞項目的轉錄和儲存"""
    # 每個子進程建立自己的 ASR 實例和 MySQL 管理器
    try:
        asr = ASRWrapper()
        local_mysql = MySQLManager()  # 每個進程使用獨立的 MySQL 連線
    except Exception as e:
        return f"❌ 初始化失敗: {e}", False
    
    title = str(row.get("news_title", ""))
    if "完整版" in title:
        return f"⏭️ 跳過【完整版】: {title}", False

    m3u8_url = row.get("m3u8_url")
    news_url = row.get("news_url")
    timestamp = str(row.get("timestamp", str(int(time.time()))))
    period = str(row.get("type", "")).strip().lower()

    if period not in ['morning', 'noon', 'night']:
        return f"❌ 無效的時段類型 (type): {period} - {title}", False
    
    # 使用更安全的暫存檔案名稱
    import tempfile
    temp_wav = None
    
    try:
        # 建立暫存檔案
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_wav = temp_file.name
        
        print(f"📥 [{idx + 1}] 開始下載音頻: {title[:50]}...")
        
        # 下載並轉換音頻
        ffmpeg_cmd = [
            'ffmpeg', '-y', 
            '-headers', 'User-Agent: Mozilla/5.0',
            '-i', m3u8_url,
            '-ar', '16000',
            '-ac', '1',
            '-vn',
            '-acodec', 'pcm_s16le',
            temp_wav
        ]
        
        # 使用 subprocess 而非 os.system 以獲得更好的錯誤處理
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            return f"❌ [{idx + 1}] FFmpeg 錯誤: {result.stderr[:100]}... - {title}", False
        
        if not os.path.exists(temp_wav) or os.path.getsize(temp_wav) == 0:
            return f"❌ [{idx + 1}] 音頻檔案無效: {title}", False

        print(f"🎵 [{idx + 1}] 開始轉錄音頻...")
        
        # 轉錄音頻
        text = asr.transcribe(temp_wav)
        print(f"📝 [{idx + 1}] 原始轉錄 ({len(text)} 字元): {text[:100]}...")

        if not text.strip():
            return f"⚠️ [{idx + 1}] 轉錄為空，跳過: {title}", False

        print(f"🤖 [{idx + 1}] 開始 LLM 清理...")
        
        # 使用 LLM 清理文字
        cleaned = clean_transcript_with_llm(text)
        if not cleaned:
            return f"⚠️ [{idx + 1}] LLM 清理失敗，跳過: {title}", False

        print(f"💾 [{idx + 1}] 儲存到資料庫 ({period})...")
        
        # 存入 MySQL
        record_id = local_mysql.insert_record(
            period=period,
            timestamp=timestamp,
            content=cleaned,
            news_title=title,
            news_url=news_url
        )
        
        if record_id:
            # 成功存入資料庫後，從CSV中移除該行（使用資料匹配而非索引）
            row_data = {
                'news_title': title,
                'm3u8_url': m3u8_url,
                'timestamp': timestamp
            }
            success = remove_processed_row_from_csv(csv_path, row_data)
            if success:
                return f"✅ [{idx + 1}] 成功完成 {period} (ID: {record_id}) 並已從CSV移除 - {title[:50]}...", True
            else:
                return f"✅ [{idx + 1}] 成功完成 {period} (ID: {record_id}) 但CSV移除失敗 - {title[:50]}...", True
        else:
            return f"❌ [{idx + 1}] 資料庫寫入失敗: {title}", False
            
    except subprocess.TimeoutExpired:
        return f"❌ [{idx + 1}] FFmpeg 下載超時: {title}", False
    except Exception as e:
        return f"❌ [{idx + 1}] 處理錯誤: {str(e)[:100]}... - {title}", False
    finally:
        # 清理暫存檔案
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
                print(f"🗑️ [{idx + 1}] 清理暫存檔案: {os.path.basename(temp_wav)}")
            except Exception as e:
                print(f"⚠️ [{idx + 1}] 清理檔案失敗: {e}")


# ========== 持續監控處理主流程 ==========
def process_csv_continuously(csv_path="videos.csv", check_interval=30, batch_size=10):
    """持續監控CSV並處理新資料"""
    if not os.path.exists(csv_path):
        print("❌ 找不到 CSV 檔案")
        return
    
    cycle_count = 0
    total_processed = 0
    total_successful = 0
    total_failed = 0
    overall_start_time = time.time()
    last_csv_size = 0
    
    print(f"🔄 開始持續監控處理模式")
    print(f"📋 CSV檔案: {csv_path}")
    print(f"⏰ 檢查間隔: {check_interval} 秒")
    print(f"📦 批次大小: {batch_size} 筆")
    print("🚨 按 Ctrl+C 可停止程式")
    print("=" * 80)
    
    try:
        while True:
            cycle_count += 1
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 檢查CSV檔案是否存在和有資料
            if not os.path.exists(csv_path):
                print(f"[{current_time}] ⏳ 等待CSV檔案出現...")
                time.sleep(check_interval)
                continue
                
            # 檢查CSV是否有新資料
            try:
                df = pd.read_csv(csv_path)
                current_csv_size = len(df)
                
                if current_csv_size == 0:
                    print(f"[{current_time}] 📭 CSV為空，等待新資料...")
                    last_csv_size = 0
                    time.sleep(check_interval)
                    continue
                
                # 檢查是否有新資料
                if current_csv_size == last_csv_size and cycle_count > 1:
                    print(f"[{current_time}] 😴 無新資料，現有 {current_csv_size} 筆資料 (第{cycle_count}次檢查)")
                    time.sleep(check_interval)
                    continue
                
                # 有新資料或首次執行
                if cycle_count == 1:
                    print(f"[{current_time}] 🚀 首次啟動，發現 {current_csv_size} 筆資料")
                else:
                    new_data_count = current_csv_size - last_csv_size
                    print(f"[{current_time}] 📥 發現 {new_data_count} 筆新資料！總計 {current_csv_size} 筆")
                
                last_csv_size = current_csv_size
                
            except Exception as e:
                print(f"[{current_time}] ❌ 讀取CSV失敗: {e}")
                time.sleep(check_interval)
                continue
            
            # 處理資料
            print(f"[{current_time}] 🔄 開始處理資料...")
            
            # 移除完整版項目
            full_version_removed = remove_full_version_items(csv_path)
            if full_version_removed:
                # 重新讀取CSV
                df = pd.read_csv(csv_path) if os.path.exists(csv_path) else pd.DataFrame()
            
            # 過濾待處理資料
            if len(df) == 0:
                print(f"[{current_time}] ⚠️ 移除完整版後無剩餘資料")
                time.sleep(check_interval)
                continue
                
            df_filtered = df[~df['news_title'].str.contains('完整版', na=False)]
            
            if len(df_filtered) == 0:
                print(f"[{current_time}] ⚠️ 過濾後無有效資料")
                time.sleep(check_interval)
                continue
            
            # 批次處理（避免一次處理太多資料）
            process_count = min(batch_size, len(df_filtered))
            df_to_process = df_filtered.head(process_count)
            
            print(f"[{current_time}] 📋 本批次處理 {process_count} 筆資料")
            
            # 執行處理
            batch_stats = process_single_batch(csv_path, df_to_process, cycle_count, current_time)
            
            # 累計統計
            total_processed += batch_stats['completed']
            total_successful += batch_stats['successful'] 
            total_failed += batch_stats['failed']
            
            # 顯示批次統計
            elapsed_time = time.time() - overall_start_time
            print(f"[{current_time}] 📊 批次統計:")
            print(f"   本批次: 處理 {batch_stats['completed']}, 成功 {batch_stats['successful']}, 失敗 {batch_stats['failed']}")
            print(f"   累計: 處理 {total_processed}, 成功 {total_successful}, 失敗 {batch_stats['failed']}")
            print(f"   運行時間: {elapsed_time/3600:.1f} 小時")
            
            # 檢查剩餘資料
            remaining_count = get_csv_current_count(csv_path)
            print(f"   剩餘資料: {remaining_count} 筆")
            
            if remaining_count > 0:
                print(f"[{current_time}] 🔄 還有資料待處理，{check_interval}秒後繼續...")
                # 如果還有資料，縮短等待時間
                time.sleep(min(check_interval, 10))
            else:
                print(f"[{current_time}] ✅ 當前資料處理完成，等待新資料...")
                time.sleep(check_interval)
            
            print("-" * 80)
    
    except KeyboardInterrupt:
        print(f"\n\n🛑 收到停止信號，正在安全退出...")
        total_time = time.time() - overall_start_time
        
        print(f"\n📊 最終統計 (運行 {total_time/3600:.1f} 小時):")
        print(f"   檢查週期: {cycle_count}")
        print(f"   總處理: {total_processed}")
        print(f"   總成功: {total_successful}")
        print(f"   總失敗: {total_failed}")
        print(f"   平均處理速度: {total_processed/(total_time/3600):.1f} 筆/小時")
        
        remaining_count = get_csv_current_count(csv_path)
        if remaining_count > 0:
            print(f"   未處理: {remaining_count} 筆")
            print(f"💡 下次重啟程式時會繼續處理剩餘資料")
        
        print(f"\n👋 程式已安全退出")
    
    except Exception as e:
        print(f"\n❌ 程式異常: {e}")
        print(f"💡 建議重新啟動程式")


def process_single_batch(csv_path, df_to_process, cycle_num, timestamp):
    """處理單批次資料"""
    start_time = time.time()
    process_count = len(df_to_process)
    
    # 使用適當的進程數量
    max_workers = min(2, process_count)
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(transcribe_and_store, idx, row, csv_path): idx
            for idx, row in df_to_process.iterrows()
        }
        
        completed = 0
        successful = 0
        failed = 0
        csv_removed = 0
        
        for future in as_completed(futures):
            completed += 1
            result, success = future.result()
            
            if success:
                successful += 1
                if "並已從CSV移除" in result:
                    csv_removed += 1
            else:
                failed += 1
            
            # 簡化進度顯示，避免輸出過多
            if completed % 5 == 0 or completed == len(futures):
                current_csv_count = get_csv_current_count(csv_path)
                print(f"   [{timestamp}] 進度 {completed}/{len(futures)}, 成功 {successful}, CSV剩餘 {current_csv_count}")
    
    duration = time.time() - start_time
    
    return {
        'completed': completed,
        'successful': successful,
        'failed': failed,
        'csv_removed': csv_removed,
        'duration': duration/60
    }


def remove_full_version_items(csv_path):
    """移除CSV中的完整版項目"""
    try:
        lock_file = csv_path + '.lock'
        
        with open(lock_file, 'w') as lock_fd:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                if not os.path.exists(csv_path):
                    return False
                    
                df = pd.read_csv(csv_path)
                original_count = len(df)
                
                # 移除完整版項目
                df_filtered = df[~df['news_title'].str.contains('完整版', na=False)]
                removed_count = original_count - len(df_filtered)
                
                if removed_count > 0:
                    df_filtered.to_csv(csv_path, index=False)
                    print(f"🗑️ 自動移除 {removed_count} 個完整版項目")
                    return True
                
                return False
                
            except (IOError, OSError):
                # 檔案被其他進程使用，跳過這次移除
                return False
                
    except Exception as e:
        print(f"❌ 移除完整版項目失敗: {e}")
        return False
    finally:
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
        except:
            pass


# ========== 診斷工具 ==========
def diagnose_remaining_data(csv_path="videos.csv"):
    """診斷剩餘的CSV資料"""
    if not os.path.exists(csv_path):
        print("✅ CSV檔案不存在，所有資料已處理完成")
        return
    
    try:
        df = pd.read_csv(csv_path)
        total_count = len(df)
        
        if total_count == 0:
            print("✅ CSV已完全清空，所有資料已處理完成")
            return
        
        print(f"\n🔍 診斷剩餘的 {total_count} 筆資料：")
        print("=" * 60)
        
        # 檢查完整版項目
        full_version_mask = df['news_title'].str.contains('完整版', na=False)
        full_version_count = full_version_mask.sum()
        
        if full_version_count > 0:
            print(f"📺 完整版項目: {full_version_count} 筆（將被自動移除）")
            print("   標題預覽:")
            for idx, title in enumerate(df[full_version_mask]['news_title'].head(3)):
                print(f"   - {title}")
            if full_version_count > 3:
                print(f"   ... 還有 {full_version_count - 3} 筆完整版")
        
        # 檢查一般項目
        normal_items = df[~full_version_mask]
        normal_count = len(normal_items)
        
        if normal_count > 0:
            print(f"📰 待處理新聞項目: {normal_count} 筆")
            print("   標題預覽:")
            for idx, title in enumerate(normal_items['news_title'].head(5)):
                print(f"   - {title}")
            if normal_count > 5:
                print(f"   ... 還有 {normal_count - 5} 筆")
        
        # 檢查時段分布
        if normal_count > 0:
            print(f"\n📊 時段分布:")
            period_counts = {}
            for _, row in normal_items.iterrows():
                period = str(row.get("type", "")).strip().lower()
                if period in ['morning', 'noon', 'night']:
                    period_counts[period] = period_counts.get(period, 0) + 1
                else:
                    period_counts['unknown'] = period_counts.get('unknown', 0) + 1
            
            for period, count in period_counts.items():
                print(f"   {period}: {count} 筆")
            
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ 診斷失敗: {e}")


# ========== 執行 ==========
if __name__ == "__main__":
    print("🚀 啟動新聞轉錄持續監控系統")
    print("📊 使用 Whisper CLI + MySQL 儲存 + CSV自動移除")
    print("🔄 將持續監控CSV檔案變化並自動處理新資料")
    print("⚡ 支援動態新增資料的即時處理")
    
    # 顯示初始狀態
    print("\n🔍 系統啟動前狀態:")
    diagnose_remaining_data("videos.csv")
    
    # 啟動持續監控處理
    try:
        process_csv_continuously(
            csv_path="videos.csv", 
            check_interval=30,  # 每30秒檢查一次
            batch_size=10       # 每批次最多處理10筆
        )
    except Exception as e:
        print(f"❌ 系統啟動失敗: {e}")
        print("請檢查設定並重新啟動")
