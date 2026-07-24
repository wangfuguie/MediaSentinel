import os
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error
import requests
import json
import re
from datetime import datetime
import time
from datetime import timedelta
from difflib import SequenceMatcher
from typing import List, Dict, Any
from collections import deque
from llm_client import call_llm, LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL

load_dotenv()

taiwan_time = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y%m%d_%H%M%S")

# ========== 配置 ==========
SOURCE_TABLE = "whisper_transcripts"
TARGET_TABLE = "clean_news"

WINDOW_SIZE = 15
SLIDE_STEP = 10
MAX_ROUNDS = 999
SIMILARITY_THRESHOLD = 0.7  # 相似度閾值，超過此值視為重複

# ========== 持續監控配置 ==========
MONITOR_INTERVAL = 30  # 監控間隔（秒）
MIN_NEW_DATA_THRESHOLD = 10  # 最小新資料數量閾值
MAX_WAIT_TIME = 300  # 最大等待時間（秒）

def call_model_api(prompt: str) -> str:
    """統一的模型API呼叫介面"""
    return call_llm(prompt, temperature=0, max_tokens=1500, timeout=300)

# ========== MySQL 資料庫管理器 ==========
class MySQLManager:
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
            'autocommit': True
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
        """測試資料庫連線並檢查欄位格式"""
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
            expected_tables = [SOURCE_TABLE, TARGET_TABLE]
            
            missing_tables = [table for table in expected_tables if table not in tables]
            if missing_tables:
                print(f"[{datetime.utcnow()}] ⚠️ 缺少資料表: {missing_tables}")
            else:
                print(f"[{datetime.utcnow()}] ✅ 所有必要資料表都存在: {expected_tables}")
            
            # 🔍 重點檢查：查看 processed_at 欄位的定義
            print(f"🔍 檢查 {TARGET_TABLE} 表結構...")
            cursor.execute(f"DESCRIBE {TARGET_TABLE}")
            columns = cursor.fetchall()
            
            for column in columns:
                if column[0] == 'processed_at':
                    print(f"📝 processed_at 欄位定義: {column}")
                    print(f"   欄位名稱: {column[0]}")
                    print(f"   資料類型: {column[1]}")
                    print(f"   允許空值: {column[2]}")
                    print(f"   預設值: {column[4]}")
            
        except Error as e:
            print(f"[{datetime.utcnow()}] ❌ MySQL 連線測試失敗: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def fetch_transcripts_after_timestamp(self, last_timestamp=None):
        """從 whisper_transcripts 表取得指定時間戳之後的資料"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return []
                
            cursor = connection.cursor(dictionary=True)
            
            if last_timestamp:
                print(f"🔍 查詢時間戳 > {last_timestamp} 的資料")
                if len(str(last_timestamp).strip()) < 10:
                    print(f"⚠️ 時間戳格式異常，改為查詢所有資料")
                    query = f"SELECT * FROM {SOURCE_TABLE} ORDER BY timestamp"
                    cursor.execute(query)
                else:
                    query = f"SELECT * FROM {SOURCE_TABLE} WHERE timestamp > %s ORDER BY timestamp"
                    cursor.execute(query, (last_timestamp,))
            else:
                print("🔍 查詢所有資料")
                query = f"SELECT * FROM {SOURCE_TABLE} ORDER BY timestamp"
                cursor.execute(query)
            
            results = cursor.fetchall()
            print(f"📥 找到 {len(results)} 筆資料")
            return results
            
        except Error as e:
            print(f"❌ 讀取轉錄資料錯誤: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def insert_clean_news(self, timestamp, final_content, source_timestamp, processed_at):
        """插入清理後的新聞到 clean_news 表 - UTC+8時間版本"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return False
                
            cursor = connection.cursor()
            
            # 🚀 修正為 UTC+8 台灣時間
            from datetime import datetime, timedelta
            utc_time = datetime.utcnow()
            taiwan_time = utc_time + timedelta(hours=8)
            formatted_time = taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
            
            insert_query = f"""
            INSERT INTO {TARGET_TABLE} (timestamp, final_content, source_timestamp, processed_at) 
            VALUES (%s, %s, %s, %s)
            """
            
            print(f"    🕐 UTC時間: {utc_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    🇹🇼 台灣時間(UTC+8): {formatted_time}")
            print(f"    📝 儲存到資料庫的時間: {formatted_time}")
            
            cursor.execute(insert_query, (timestamp, final_content, source_timestamp, formatted_time))
            connection.commit()
            
            # 🔍 多重驗證插入結果
            print(f"    🔍 驗證插入的資料...")
            
            # 方法1: 查詢剛插入的記錄
            cursor.execute(f"SELECT id, timestamp, processed_at FROM {TARGET_TABLE} WHERE timestamp = %s", (timestamp,))
            result = cursor.fetchone()
            if result:
                print(f"    ✅ 記錄ID: {result[0]}, 時間戳: {result[1]}, 處理時間: {result[2]}")
            
            # 方法2: 查詢最新的3筆記錄
            cursor.execute(f"SELECT id, timestamp, DATE_FORMAT(processed_at, '%Y-%m-%d %H:%i:%s') as formatted_time FROM {TARGET_TABLE} ORDER BY id DESC LIMIT 3")
            recent_records = cursor.fetchall()
            print(f"    📊 最新3筆記錄的時間格式:")
            for record in recent_records:
                print(f"       ID {record[0]}: {record[2]}")
            
            # 方法3: 檢查時間欄位的實際值
            cursor.execute(f"SELECT processed_at, UNIX_TIMESTAMP(processed_at) FROM {TARGET_TABLE} WHERE timestamp = %s", (timestamp,))
            time_check = cursor.fetchone()
            if time_check:
                print(f"    🕐 資料庫原始時間值: {time_check[0]}")
                print(f"    🕐 Unix時間戳: {time_check[1]}")
            
            return True
            
        except Error as e:
            print(f"❌ 插入清理新聞錯誤: {e}")
            print(f"   SQL錯誤詳情: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def get_recent_clean_news(self, limit=10):
        """從 clean_news 表取得最近的資料"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return []
                
            cursor = connection.cursor(dictionary=True)
            
            query = f"""
            SELECT * FROM {TARGET_TABLE} 
            ORDER BY processed_at DESC 
            LIMIT %s
            """
            cursor.execute(query, (limit,))
            
            results = cursor.fetchall()
            return results
            
        except Error as e:
            print(f"❌ 讀取最近新聞錯誤: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def get_last_processed_source_timestamp(self):
        """獲取最後處理的來源時間戳"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return None
                
            cursor = connection.cursor()
            
            query = f"""
            SELECT source_timestamp FROM {TARGET_TABLE} 
            ORDER BY processed_at DESC 
            LIMIT 1
            """
            cursor.execute(query)
            
            result = cursor.fetchone()
            if result and result[0]:
                source_timestamp = str(result[0]).strip()
                print(f"🔍 資料庫中的source_timestamp: {source_timestamp}")
                
                if '-' in source_timestamp:
                    end_timestamp = source_timestamp.split('-')[1].strip()
                    print(f"📝 解析出的結束時間戳: {end_timestamp}")
                    return end_timestamp
                else:
                    print(f"📝 使用單一時間戳: {source_timestamp}")
                    return source_timestamp
            
            print("📝 資料庫中沒有找到已處理的記錄")
            return None
            
        except Error as e:
            print(f"❌ 獲取最後處理時間戳錯誤: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def count_clean_news(self):
        """計算 clean_news 表的總記錄數"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return 0
                
            cursor = connection.cursor()
            
            query = f"SELECT COUNT(*) FROM {TARGET_TABLE}"
            cursor.execute(query)
            
            result = cursor.fetchone()
            return result[0] if result else 0
            
        except Error as e:
            print(f"❌ 計算新聞數量錯誤: {e}")
            return 0
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

# ========== 三階段處理器 ==========
class ThreeStageNewsProcessor:
    """三階段新聞處理器（MySQL版本）"""
    
    def __init__(self, mysql_manager):
        self.mysql_manager = mysql_manager
        # 儲存已處理的新聞內容（最多保留10筆）
        self.processed_news_history = deque(maxlen=10)
        
        # 載入最近的新聞到歷史記錄
        self.load_recent_news_to_history()
        
        # 記錄處理統計
        self.stats = {
            'total_windows': 0,
            'total_articles': 0,
            'stage1_calls': 0,
            'stage2_calls': 0,
            'stage3_calls': 0,
            'saved_articles': 0,
            'duplicate_articles': 0
        }
    
    def load_recent_news_to_history(self):
        """載入最近的新聞到歷史記錄"""
        try:
            recent_news = self.mysql_manager.get_recent_clean_news(10)
            for news in reversed(recent_news):  # 反轉以保持時間順序
                content = news.get('final_content', '')
                if content and len(content.strip()) >= 10:
                    self.processed_news_history.append(content)
            
            print(f"📝 載入 {len(self.processed_news_history)} 筆歷史新聞到記錄中")
        except Exception as e:
            print(f"⚠️ 載入歷史新聞失敗: {e}")
    
    def split_content_by_tags(self, content: str) -> List[Dict[str, str]]:
        """根據標籤分割內容"""
        tag_pattern = r'【(新聞|其他|新闻|其他)】'
        matches = list(re.finditer(tag_pattern, content))
        
        if not matches:
            return [{'tag': '未分類', 'content': content.strip()}]
        
        results = []
        for i, match in enumerate(matches):
            tag = match.group(1)
            if tag in ['新聞', '新闻']:
                tag = '新聞'
            elif tag in ['其他']:
                tag = '其他'
            
            start_pos = match.end()
            if i + 1 < len(matches):
                end_pos = matches[i + 1].start()
            else:
                end_pos = len(content)
            
            article_content = content[start_pos:end_pos].strip()
            if article_content:
                results.append({
                    'tag': tag,
                    'content': article_content
                })
        
        return results
    
    def stage1_windowed_processing(self, data_chunk: List[Dict]) -> List[Dict]:
        """階段1：滑動視窗處理並分段標記"""
        print(f"  🔍 [階段1] 處理 {len(data_chunk)} 筆資料的滑動視窗")
        
        text_block = "\n".join([item["content"] for item in data_chunk])
        start_ts = data_chunk[0]["timestamp"]
        end_ts = data_chunk[-1]["timestamp"]
        
        print(f"    📝 時間範圍：{start_ts} ~ {end_ts}")
        print(f"    📝 文字長度：{len(text_block)} 字元")
        
        prompt = f"""你是一個專業的新聞編輯，請將以下語音轉錄內容整理成清晰的新聞稿。

**處理規則：**
1. 修正語音辨識錯誤和錯別字
2. 刪除亂碼和無意義重複（如：Đểươ ah F크、1.1.1.1.1）
3. 將相關內容合併成完整段落
4. 統一使用簡體中文
5. 使用正確標點符號
6. 若出現外國政治人物或知名人士，請翻譯為常見中文譯名（例如：Trump → 川普），並在整段文字中保持名稱一致。

**輸出格式：**
每則完整內容前加上分類標籤，並且只能有下列兩種分類標籤：
- 新聞報導、政治、軍事、經濟等 → 【新聞】
- 廣告、生活資訊、節目預告等 → 【其他】
- 請務必為每段內容加上標籤，只能【新聞】或【其他】這兩種標籤，並且一筆資料只能有一個標籤，標籤必須在資料開頭，否則將視為錯誤。

**示例輸出：**
【新聞】今日美國總統川普宣布新的貿易政策，預計將對中美關係產生重大影響。該政策主要針對高科技產業，將在未來三個月內正式實施。

【其他】本節目由XX品牌贊助播出，為您提供最優質的服務體驗。詳情請撥打客服專線。

現在請處理以下內容：
{text_block}"""

        try:
            result = call_model_api(prompt)
            self.stats['stage1_calls'] += 1
            
            articles = self.split_content_by_tags(result.strip())
            
            for i, article in enumerate(articles):
                article['window_start'] = start_ts
                article['window_end'] = end_ts
                article['article_index'] = i
                article['stage1_content'] = article['content']
            
            print(f"    ✅ [階段1] 完成，產生 {len(articles)} 篇文章")
            return articles
            
        except Exception as e:
            print(f"    ❌ [階段1] 處理失敗：{e}")
            return []
    
    def stage2_deep_cleaning(self, article: Dict) -> Dict:
        """階段2：單篇文章深度清理"""
        print(f"    🔄 [階段2] 清理文章：{article['tag']}")
        
        content = article['content']
        
        prompt = f"""你是一個專業的新聞編輯，請對以下文章進行深度清理和優化。

**主要任務：**
1. 修正語音識別造成的錯字和同音異字（如：戰靈→占領、千創白孔→千瘡百孔、配以四列→以色列）
2. 改善語法結構，使句子更通順自然
3. 統一專業術語的表達方式
4. 刪除重複或冗餘的表述
5. 保持新聞事實的準確性和客觀性
6. **將所有內容統一轉換為簡體中文**
7. **移除所有製作相關資訊：包含但不限於「中文字幕：」、「英文字幕：」、「翻譯：」、「製作人：」、「編輯：」、「攝影：」、「導演：」、「字幕組：」等製作團隊標記和人員名單**
8. **僅刪除明確無意義的內容：只有當內容完全是技術標記、亂碼、重複無意義字符或純雜訊時才刪除，有任何可能包含新聞資訊的內容都應保留並嘗試修復**
9. 每筆資料只能【新聞】或【其他】這兩種標籤，並且一筆資料只能有一個標籤，標籤必須在資料開頭。

**處理原則：**
- 只修正明顯的語音識別錯誤和語法問題
- 不改變新聞事實和立場
- 保持原有的資訊完整性
- 使用標準的新聞簡體中文表達
- 維持邏輯順序和因果關係
- 確保所有繁體字都轉換為對應的簡體字

**輸出要求：**
- 只輸出清理後的文章內容
- 如果內容邏輯混亂無法理解，輸出：無法修復
- 輸出純文本新聞內容，必須使用簡體中文
- 人名、地名等專有名詞也需要使用簡體中文常見表達

現在請處理以下內容：
{content}"""

        try:
            result = call_model_api(prompt)
            self.stats['stage2_calls'] += 1
            
            cleaned_content = result.strip()
            
            if "無法修復" in cleaned_content or "无法修复" in cleaned_content:
                print(f"    ⚠️ [階段2] 內容無法修復")
                article['stage2_content'] = "【無法修復】"
            else:
                article['stage2_content'] = cleaned_content
                print(f"    ✅ [階段2] 清理完成，長度：{len(cleaned_content)}")
            
            return article
            
        except Exception as e:
            print(f"    ❌ [階段2] 處理失敗：{e}")
            article['stage2_content'] = f"階段2處理失敗：{str(e)}"
            return article
    
    def stage3_deduplication(self, article: Dict, article_index: int) -> Dict:
        """階段3：去重複處理"""
        print(f"    🔄 [階段3] 去重複處理：{article['tag']}")
        
        content = article['stage2_content']
        
        if content.startswith(("【無法修復】", "階段2處理失敗")):
            article['stage3_content'] = content
            return article
        
        if not self.processed_news_history:
            article['stage3_content'] = content
            print(f"    ✅ [階段3] 無歷史資料，直接使用階段2結果")
            return article
        
        comparison_articles = list(self.processed_news_history)
        print(f"    📊 與 {len(comparison_articles)} 篇歷史文章比較")
        
        comparison_text = ""
        for i, hist_article in enumerate(comparison_articles, 1):
            comparison_text += f"\n--- 歷史文章 {i} ---\n{hist_article}\n"
        
        prompt = f"""你是一個專業的文本編輯器，請對以下內容進行去重複處理，移除重複內容後輸出完整的處理後段落。

**任務說明：**
- 「歷史文章」是已經處理過的文章，用來檢查重複
- 「當前文章」是需要去重複處理的新文章
- 請仔細比較並移除當前文章中與歷史文章重複的內容

**主要任務：**
1. 逐句比較當前文章與歷史文章的內容
2. 需要保留【其他】和【新聞】的標籤，並移除當前文章中與歷史文章重複的句子或段落
3. 保留當前文章中獨特的、新的資訊
4. 確保移除重複內容後，剩餘內容仍然邏輯完整、語句通順
5. 如果當前文章與歷史文章重複度過高（超過80%），導致移除後幾乎沒有剩餘內容，則判定為完全重複

**處理原則：**
- 精確識別重複的句子或段落，不要因為相似就刪除
- 保留事實資訊的完整性和準確性
- 維持文章的邏輯結構和因果關係
- 如果移除重複內容後文章變得支離破碎，請適當調整語句連接
- 只有在移除重複內容後，剩餘內容少於原文的20%時，才判定為"【重複內容】"

**輸出要求：**
- 只輸出去重後的【新聞】或【其他】內容段落（不包含說明、不包含比對分析）
- 必須保留原始標籤（例如：開頭為【新聞】或【其他】），整篇文章只能有一個標籤
- **嚴禁輸出「經過比對」這類說明句子**
- 僅當文章與歷史內容重複度超過80%，且剩餘有效內容過少時，才輸出「【重複內容】」
- 如果內容邏輯支離破碎，無法重構，才輸出「【無法處理】」


=== 歷史文章 ===
{comparison_text}

=== 當前文章 ===
{content}"""

        try:
            result = call_model_api(prompt)
            self.stats['stage3_calls'] += 1
            
            final_content = result.strip()
            
            if "【重複內容】" in final_content or "【重复内容】" in final_content:
                print(f"    ⚠️ [階段3] 檢測到完全重複")
                article['stage3_content'] = "【重複內容】"
                article['is_duplicate'] = True
                self.stats['duplicate_articles'] += 1
            elif "【無法處理】" in final_content or "【无法处理】" in final_content:
                print(f"    ❌ [階段3] 內容結構複雜無法處理")
                article['stage3_content'] = "【無法處理】"
                article['is_duplicate'] = False
            elif not final_content or len(final_content.strip()) < 10:
                print(f"    ⚠️ [階段3] 移除重複後內容過少")
                article['stage3_content'] = "【重複內容】"
                article['is_duplicate'] = True
                self.stats['duplicate_articles'] += 1
            else:
                article['stage3_content'] = final_content
                article['is_duplicate'] = False
                print(f"    ✅ [階段3] 去重複完成，處理後長度：{len(final_content)}")
                
                original_length = len(content)
                final_length = len(final_content)
                reduction_rate = (original_length - final_length) / original_length * 100
                print(f"    📊 內容精簡率：{reduction_rate:.1f}%")
            
            return article
            
        except Exception as e:
            print(f"    ❌ [階段3] 處理失敗：{e}")
            article['stage3_content'] = f"階段3處理失敗：{str(e)}"
            article['is_duplicate'] = False
            return article
    
    def add_to_history(self, article: Dict):
        """將處理完成的文章添加到歷史記錄"""
        content = article.get('stage3_content', '')
        
        if (content and 
            not content.startswith(("【重複內容】", "【無法處理】", "【無法修復】", "階段2處理失敗", "階段3處理失敗")) and
            not article.get('is_duplicate', False) and
            len(content.strip()) >= 10):
            
            self.processed_news_history.append(content)
            print(f"    📝 已加入歷史記錄，目前共 {len(self.processed_news_history)} 篇")
    
    def save_article_to_database(self, article: Dict, block_ts: str, global_counter: int) -> bool:
        """將文章儲存到資料庫 - UTC+8時間版本"""
        content = article.get('stage3_content', '')
        
        # 檢查是否應該儲存
        if (not content or 
            content.startswith(("【重複內容】", "【無法處理】", "【無法修復】", "階段2處理失敗", "階段3處理失敗")) or
            article.get('is_duplicate', False) or
            len(content.strip()) < 10):
            return False
        
        try:
            # 🚀 使用 UTC+8 台灣時間
            from datetime import datetime, timedelta
            utc_time = datetime.utcnow()
            taiwan_time = utc_time + timedelta(hours=8)
            
            timestamp_unique = taiwan_time.strftime("%Y%m%d_%H%M%S_%f") + f"_{global_counter:03d}"
            processed_at_formatted = taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
            
            # 清理最終內容
            final_content = re.sub(r'\n+', ' ', content)
            final_content = re.sub(r'\s+', ' ', final_content)
            final_content = final_content.strip()
            
            print(f"    🕐 UTC時間: {utc_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    🇹🇼 台灣時間(UTC+8): {processed_at_formatted}")
            print(f"    📝 準備儲存的時間格式: {processed_at_formatted}")
            
            success = self.mysql_manager.insert_clean_news(
                timestamp=timestamp_unique,
                final_content=final_content,
                source_timestamp=str(block_ts),
                processed_at=processed_at_formatted
            )
            
            if success:
                self.stats['saved_articles'] += 1
                print(f"✅ 已儲存：{timestamp_unique}")
                
                # 顯示內容預覽
                preview = final_content[:100] + "..." if len(final_content) > 100 else final_content
                print(f"   內容預覽：{preview}")
                return True
            else:
                print(f"❌ 儲存失敗")
                return False
                
        except Exception as e:
            print(f"❌ 儲存文章到資料庫失敗：{e}")
            return False

# ========== 滑動視窗分批 ==========
def sliding_window_chunks(data, window=15, step=10):
    """滑動視窗分批處理 - 確保處理所有資料"""
    chunks = []
    
    # 標準滑動視窗處理
    for i in range(0, len(data) - window + 1, step):
        chunk = data[i:i + window]
        text_block = "\n".join([d["content"] for d in chunk])
        timestamps = [d["timestamp"] for d in chunk]
        start_ts = chunk[0]["timestamp"]
        end_ts = chunk[-1]["timestamp"]
        range_ts = f"{start_ts}-{end_ts}"
        chunks.append((range_ts, text_block, chunk))
    
    # 檢查是否有剩餘資料未處理
    if chunks:
        last_chunk = chunks[-1][2]  # 取得最後一個chunk的資料
        last_processed_index = data.index(last_chunk[-1])  # 找到最後處理的資料索引
        
        # 如果還有剩餘資料，添加最後一個視窗
        if last_processed_index < len(data) - 1:
            remaining_data = data[last_processed_index + 1:]
            if remaining_data:
                start_ts = remaining_data[0]["timestamp"]
                end_ts = remaining_data[-1]["timestamp"]
                range_ts = f"{start_ts}-{end_ts}"
                text_block = "\n".join([d["content"] for d in remaining_data])
                chunks.append((range_ts, text_block, remaining_data))
                print(f"🔍 添加剩餘資料視窗：{len(remaining_data)} 筆資料")
    else:
        # 如果資料量小於視窗大小，直接處理所有資料
        if data:
            start_ts = data[0]["timestamp"]
            end_ts = data[-1]["timestamp"]
            range_ts = f"{start_ts}-{end_ts}"
            text_block = "\n".join([d["content"] for d in data])
            chunks.append((range_ts, text_block, data))
    
    print(f"📊 總共建立 {len(chunks)} 個視窗，確保處理全部 {len(data)} 筆資料")
    return chunks

# ========== 監控新資料 ==========
def monitor_new_data(mysql_manager, last_timestamp=None):
    """監控新資料 - 簡化版本，直接返回結果"""
    
    if last_timestamp:
        print(f"   📝 檢查時間戳 > {last_timestamp} 的新資料")
    else:
        print("   📝 檢查所有資料（首次運行）")
    
    new_data = mysql_manager.fetch_transcripts_after_timestamp(last_timestamp)
    
    if new_data:
        print(f"📥 找到 {len(new_data)} 筆新資料")
        # 顯示最新和最舊的時間戳
        if len(new_data) > 1:
            first_ts = new_data[0].get('timestamp', 'Unknown')
            last_ts = new_data[-1].get('timestamp', 'Unknown')
            print(f"   時間範圍：{first_ts} ~ {last_ts}")
        else:
            ts = new_data[0].get('timestamp', 'Unknown')
            print(f"   時間戳：{ts}")
    else:
        print("   暫無新資料")
    
    return new_data

# ========== 等待資料累積 ==========
def wait_for_sufficient_data(mysql_manager, last_timestamp=None):
    """等待資料累積到足夠數量"""
    print(f"\n⏰ 等待資料累積...")
    
    wait_start_time = time.time()
    
    while True:
        if time.time() - wait_start_time > MAX_WAIT_TIME:
            print(f"⏰ 等待時間超過 {MAX_WAIT_TIME} 秒，繼續處理現有資料")
            break
        
        new_data = monitor_new_data(mysql_manager, last_timestamp)
        
        if len(new_data) >= MIN_NEW_DATA_THRESHOLD:
            print(f"✅ 資料充足，開始處理 {len(new_data)} 筆新資料")
            return new_data
        elif len(new_data) > 0:
            print(f"📊 目前有 {len(new_data)} 筆新資料，等待更多資料...")
        else:
            print("⏳ 暫無新資料，繼續等待...")
        
        print(f"💤 等待 {MONITOR_INTERVAL} 秒後再次檢查...")
        time.sleep(MONITOR_INTERVAL)
    
    return monitor_new_data(mysql_manager, last_timestamp)

# ========== 三階段處理流程 ==========
def process_three_stages(processor, text_block, chunk_data):
    """執行完整的三階段處理流程"""
    print(f"\n🔄 開始三階段處理流程...")
    
    # 第一階段：滑動視窗處理
    articles = processor.stage1_windowed_processing(chunk_data)
    if not articles:
        print("❌ 第一階段處理失敗")
        return []
    
    # 逐篇處理每個文章
    final_articles = []
    for article in articles:
        # 第二階段：深度清理
        article = processor.stage2_deep_cleaning(article)
        
        # 第三階段：去重複處理
        article = processor.stage3_deduplication(article, len(final_articles) + 1)
        
        # 添加到歷史記錄
        processor.add_to_history(article)
        
        final_articles.append(article)
    
    print(f"✅ 三階段處理完成，處理了 {len(final_articles)} 篇文章")
    return final_articles

# ========== 處理新資料批次 ==========
def process_data_batch(processor, new_data, last_processed_timestamp):
    """處理一批新資料"""
    global_counter = 0
    total_saved = 0
    total_failed = 0
    latest_timestamp = last_processed_timestamp
    
    print(f"\n🔄 開始處理 {len(new_data)} 筆新資料...")
    
    if len(new_data) < WINDOW_SIZE:
        print(f"⚠️ 資料量不足視窗大小，實際處理 {len(new_data)} 筆")
    
    count = 0
    
    # 處理滑動視窗
    for block_ts, block_text, chunk_data in sliding_window_chunks(new_data, WINDOW_SIZE, SLIDE_STEP):
        print(f"\n{'='*80}")
        print(f"🔍 處理回合 {count + 1}，時間範圍：{block_ts}")
        print(f"📝 區塊文字長度：{len(block_text)} 字元")
        
        # 執行三階段處理
        final_articles = process_three_stages(processor, block_text, chunk_data)
        
        if final_articles:
            saved_count = 0
            for article in final_articles:
                # 儲存到資料庫
                if processor.save_article_to_database(article, block_ts, global_counter):
                    saved_count += 1
                    total_saved += 1
                else:
                    total_failed += 1
                
                global_counter += 1
            
            print(f"💾 本回合儲存：{saved_count} 筆")
        else:
            print("❌ 本回合處理失敗，沒有產出")
            total_failed += 1
        
        # 更新最新處理的時間戳
        if chunk_data:
            latest_timestamp = chunk_data[-1]['timestamp']
        
        count += 1
        processor.stats['total_windows'] += 1
        
        # 避免API頻率限制
        time.sleep(2)
    
    # 輸出批次統計結果
    print(f"\n📊 本批次處理完成：")
    print(f"   處理回合：{count}")
    print(f"   成功儲存：{total_saved}")
    print(f"   重複跳過：{processor.stats['duplicate_articles']}")
    print(f"   失敗數量：{total_failed}")
    print(f"   API呼叫統計：")
    print(f"     階段1：{processor.stats['stage1_calls']}")
    print(f"     階段2：{processor.stats['stage2_calls']}")
    print(f"     階段3：{processor.stats['stage3_calls']}")
    
    return latest_timestamp, total_saved

# ========== 持續監控主程序 ==========
def continuous_monitoring():
    """持續監控主程序 - 永不停止的監聽模式"""
    print("🎯 啟動持續監聽模式...")
    print(f"🔄 系統將每 {MONITOR_INTERVAL} 秒檢查一次新資料")
    print(f"📊 當累積 {MIN_NEW_DATA_THRESHOLD} 筆或以上新資料時開始處理")
    
    # 初始化MySQL管理器
    try:
        mysql_manager = MySQLManager()
    except Exception as e:
        print(f"❌ MySQL 管理器初始化失敗: {e}")
        return
    
    # 初始化三階段處理器
    processor = ThreeStageNewsProcessor(mysql_manager)
    
    # 從資料庫獲取最後處理的時間戳
    last_processed_timestamp = mysql_manager.get_last_processed_source_timestamp()
    total_processed_count = mysql_manager.count_clean_news()
    
    if last_processed_timestamp:
        print(f"📝 從資料庫恢復，上次處理時間戳：{last_processed_timestamp}")
        print(f"📊 資料庫現有新聞數量：{total_processed_count}")
    else:
        print("🆕 首次運行或資料庫為空，將處理所有資料")
    
    monitoring_round = 0
    consecutive_empty_rounds = 0  # 連續空資料輪數
    
    try:
        print(f"\n🚀 開始持續監聽...")
        print(f"💡 按 Ctrl+C 可安全退出程式")
        
        while True:  # 無限循環，持續監聽
            monitoring_round += 1
            
            print(f"\n{'='*80}")
            print(f"🔄 監控回合 {monitoring_round}")
            print(f"⏰ 當前時間：{(datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)")
            
            # 檢查新資料
            new_data = monitor_new_data(mysql_manager, last_processed_timestamp)
            
            if not new_data:
                consecutive_empty_rounds += 1
                print(f"⏳ 暫無新資料 (連續 {consecutive_empty_rounds} 輪)")
                
                # 每10輪空資料時顯示狀態
                if consecutive_empty_rounds % 10 == 0:
                    current_count = mysql_manager.count_clean_news()
                    print(f"📊 系統狀態：資料庫共 {current_count} 筆新聞，持續監聽中...")
                
            elif len(new_data) < MIN_NEW_DATA_THRESHOLD:
                consecutive_empty_rounds = 0  # 重置連續空資料計數
                print(f"📊 發現 {len(new_data)} 筆新資料，少於處理閾值 {MIN_NEW_DATA_THRESHOLD}")
                print(f"⏳ 等待更多資料累積...")
                
            else:
                consecutive_empty_rounds = 0  # 重置連續空資料計數
                print(f"🎯 發現 {len(new_data)} 筆新資料，達到處理閾值！")
                print(f"🚀 開始處理...")
                
                # 處理新資料批次
                try:
                    latest_timestamp, batch_saved_count = process_data_batch(processor, new_data, last_processed_timestamp)
                    
                    if latest_timestamp:
                        last_processed_timestamp = latest_timestamp
                        total_processed_count += batch_saved_count
                        
                        print(f"✅ 批次處理完成，已儲存 {batch_saved_count} 筆新聞")
                        print(f"📊 累計總處理數量：{total_processed_count}")
                        
                        # 更新統計
                        actual_count = mysql_manager.count_clean_news()
                        print(f"📊 資料庫實際儲存數量：{actual_count} 筆")
                        
                    else:
                        print("❌ 處理失敗，無法更新時間戳")
                        
                except Exception as e:
                    print(f"❌ 批次處理失敗：{e}")
                    print("🔄 將在下一輪重試...")
            
            # 等待下一輪檢查
            print(f"💤 等待 {MONITOR_INTERVAL} 秒後進行下一輪檢查...")
            
            # 顯示進度提示
            for i in range(MONITOR_INTERVAL):
                if i % 10 == 0 and i > 0:  # 每10秒顯示一次倒數
                    remaining = MONITOR_INTERVAL - i
                    print(f"   ⏱️  {remaining} 秒後進行下一輪檢查...")
                time.sleep(1)
            
    except KeyboardInterrupt:
        print(f"\n🛑 收到中斷信號，正在安全退出...")
        print("✅ 程式已安全退出，進度已保存在資料庫中")
        
        # 顯示最終統計
        print(f"\n📊 最終處理統計：")
        print(f"   監控回合：{monitoring_round}")
        print(f"   處理視窗：{processor.stats['total_windows']}")
        print(f"   總文章數：{processor.stats['total_articles']}")
        print(f"   成功儲存：{processor.stats['saved_articles']}")
        print(f"   重複跳過：{processor.stats['duplicate_articles']}")
        print(f"   API呼叫統計：")
        print(f"     階段1：{processor.stats['stage1_calls']}")
        print(f"     階段2：{processor.stats['stage2_calls']}")
        print(f"     階段3：{processor.stats['stage3_calls']}")
        
    except Exception as e:
        print(f"\n❌ 程式發生未預期錯誤：{e}")
        print("📝 進度已保存在資料庫中，可重新啟動程式繼續監聽")
        raise  # 重新拋出異常以便除錯

# ========== 一次性處理模式 ==========
def one_time_processing():
    """一次性處理所有資料"""
    print("🚀 開始三階段一次性文字處理...")
    print(f"🤖 LLM 供應者：{LLM_PROVIDER}")
    
    # 初始化MySQL管理器
    try:
        mysql_manager = MySQLManager()
    except Exception as e:
        print(f"❌ MySQL 管理器初始化失敗: {e}")
        return
    
    # 初始化三階段處理器
    processor = ThreeStageNewsProcessor(mysql_manager)
    
    print("📥 讀取語音轉錄資料...")
    raw_data = mysql_manager.fetch_transcripts_after_timestamp(None)
    print(f"共讀取 {len(raw_data)} 筆原始資料")

    if len(raw_data) < WINDOW_SIZE:
        print(f"⚠️ 警告：資料量 {len(raw_data)} 少於視窗大小 {WINDOW_SIZE}")

    # 使用一次性處理
    _, total_saved = process_data_batch(processor, raw_data, None)
    
    print(f"\n🎉 三階段一次性處理完成！總共儲存 {total_saved} 筆新聞")
    
    # 顯示最終統計
    print(f"\n📊 最終處理統計：")
    print(f"   處理視窗：{processor.stats['total_windows']}")
    print(f"   總文章數：{processor.stats['total_articles']}")
    print(f"   成功儲存：{processor.stats['saved_articles']}")
    print(f"   重複跳過：{processor.stats['duplicate_articles']}")
    print(f"   API呼叫統計：")
    print(f"     階段1：{processor.stats['stage1_calls']}")
    print(f"     階段2：{processor.stats['stage2_calls']}")
    print(f"     階段3：{processor.stats['stage3_calls']}")

# ========== 主程序入口 ==========
def main():
    """主程序入口 - 持續監聽模式"""
    print("🚀 三階段新聞轉錄處理系統 - 持續監聽模式")
    print(f"🤖 LLM 供應者：{LLM_PROVIDER}")
    print(f"   模型：{LLM_MODEL}")
    if LLM_BASE_URL:
        print(f"   端點：{LLM_BASE_URL}")
    
    print(f"⚙️ 監聽配置：")
    print(f"   監控間隔：{MONITOR_INTERVAL} 秒")
    print(f"   處理閾值：{MIN_NEW_DATA_THRESHOLD} 筆新資料")
    print(f"   視窗大小：{WINDOW_SIZE} 筆")
    print(f"   滑動步長：{SLIDE_STEP} 筆")
    
    try:
        continuous_monitoring()
    except KeyboardInterrupt:
        print("\n🛑 收到中斷信號，程式已安全退出")
    except Exception as e:
        print(f"\n❌ 程式發生錯誤：{e}")
        print("🔄 可重新啟動程式繼續監聽")

if __name__ == "__main__":
    main()
