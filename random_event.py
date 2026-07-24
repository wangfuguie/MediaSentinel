import time
import uuid
import json
import os
import re
import subprocess
from dotenv import load_dotenv
import tempfile
import requests
import urllib.parse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

load_dotenv()
# 交由 Selenium Manager 依瀏覽器版本管理 driver，忽略 PATH 中可能過期的版本。
os.environ.setdefault("SE_SKIP_DRIVER_IN_PATH", "true")

from datetime import datetime
import mysql.connector
from mysql.connector import Error
from whisper_setup import ensure_whisper_cpp
from llm_client import call_llm

# ========== Whisper CLI 包裝器 ==========
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
    """使用設定的 LLM API 清理轉錄文字"""

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

# ========== MySQL 資料庫管理 ==========
class MySQLManager:
    def __init__(self, host=None, port=None, database='random_event', 
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
        """測試資料庫連線"""
        connection = None
        cursor = None
        try:
            print(f"[{datetime.utcnow()}] 🔗 測試 MySQL 連線...")
            connection = self.get_connection()
            if not connection:
                raise Exception("無法建立 MySQL 連線")
                
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            print(f"[{datetime.utcnow()}] ✅ MySQL 連線測試成功")
            
        except Error as e:
            print(f"[{datetime.utcnow()}] ❌ MySQL 連線測試失敗: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def insert_clean_news(self, video_id, title, original_url, duration, date, cleaned_content):
        """插入清理後的新聞內容"""
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            if not connection:
                return None
                
            cursor = connection.cursor()
            
            # 使用 INSERT ... ON DUPLICATE KEY UPDATE 避免重複插入
            insert_query = """
            INSERT INTO clean_news (video_id, title, original_url, duration, date, cleaned_content)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                cleaned_content = VALUES(cleaned_content),
                updated_at = CURRENT_TIMESTAMP
            """
            
            cursor.execute(insert_query, (video_id, title, original_url, duration, date, cleaned_content))
            connection.commit()
            
            return cursor.lastrowid if cursor.rowcount == 1 else cursor.rowcount
            
        except Error as e:
            print(f"[{datetime.utcnow()}] ❌ MySQL 插入錯誤: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

# ========== 音頻下載和轉換 ==========
def download_and_convert_audio(m3u8_url):
    """下載 M3U8 串流並轉換為 WAV 格式（靜音模式）"""
    temp_wav = None
    try:
        # 建立暫存檔案
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_wav = temp_file.name
        
        print(f"📥 開始下載音頻: {m3u8_url[:80]}...")
        
        # 針對不同類型的 URL 使用不同的下載策略
        if 'p.data.cctv.com/play' in m3u8_url:
            # 對於 API 連結，增加更多 headers 和參數
            ffmpeg_cmd = [
                'ffmpeg', '-y', 
                '-loglevel', 'error',  # 🔇 只顯示錯誤訊息，減少輸出
                '-headers', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                '-headers', 'Referer: https://tv.cctv.com/',
                '-headers', 'Accept: */*',
                '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                '-i', m3u8_url,
                '-ar', '16000',  # 16kHz 採樣率
                '-ac', '1',      # 單聲道
                '-vn',           # 不要視頻
                '-acodec', 'pcm_s16le',  # PCM 16位編碼
                '-t', '600',     # 最長10分鐘
                '-avoid_negative_ts', 'make_zero',
                '-nostdin',      # 🔇 不讀取標準輸入
                temp_wav
            ]
        else:
            # 對於直接 M3U8 連結，使用原有的參數
            ffmpeg_cmd = [
                'ffmpeg', '-y', 
                '-loglevel', 'error',  # 🔇 只顯示錯誤訊息
                '-headers', 'User-Agent: Mozilla/5.0',
                '-i', m3u8_url,
                '-ar', '16000',  # 16kHz 採樣率
                '-ac', '1',      # 單聲道
                '-vn',           # 不要視頻
                '-acodec', 'pcm_s16le',  # PCM 16位編碼
                '-t', '300',     # 最長5分鐘
                '-nostdin',      # 🔇 不讀取標準輸入
                temp_wav
            ]
        
        # 🔇 分離方式處理 subprocess，避免參數衝突
        with open(os.devnull, 'w') as devnull:
            result = subprocess.run(
                ffmpeg_cmd, 
                stdin=subprocess.DEVNULL,
                stdout=devnull,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300
            )
        
        if result.returncode != 0:
            error_msg = result.stderr[:500] if result.stderr else "未知錯誤"
            raise Exception(f"FFmpeg 錯誤: {error_msg}")
        
        if not os.path.exists(temp_wav) or os.path.getsize(temp_wav) == 0:
            raise Exception("音頻檔案無效或為空")
        
        file_size_mb = os.path.getsize(temp_wav) / 1024 / 1024
        print(f"✅ 音頻下載完成: {file_size_mb:.1f} MB")
        return temp_wav
        
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg 下載超時")
    except Exception as e:
        # 清理失敗的暫存檔案
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except:
                pass
        raise e

# ========== 影片處理主函數 ==========
def process_video(video, asr, mysql_manager):
    """處理單個影片：下載 -> 轉錄 -> 清理 -> 儲存"""
    video_id = str(uuid.uuid4())
    title = video.get('title', '')
    
    # 跳過完整版
    if "完整版" in title:
        print(f"⏭️ 跳過完整版: {title}")
        return False
    
    temp_wav = None
    try:
        print(f"\n🎬 開始處理: {title}")
        
        # 1. 下載和轉換音頻
        temp_wav = download_and_convert_audio(video['m3u8'])
        
        # 2. 轉錄音頻
        print(f"🎵 開始轉錄音頻...")
        transcribed_text = asr.transcribe(temp_wav)
        
        if not transcribed_text.strip():
            print(f"⚠️ 轉錄結果為空，跳過")
            return False
        
        print(f"📝 轉錄完成 ({len(transcribed_text)} 字元): {transcribed_text[:100]}...")
        
        # 3. 使用 LLM 清理文字
        print(f"🤖 開始 AI 文字清理...")
        cleaned_content = clean_transcript_with_llm(transcribed_text)
        
        if not cleaned_content:
            print(f"⚠️ 文字清理失敗，跳過")
            return False
        
        print(f"✨ 文字清理完成: {cleaned_content[:100]}...")
        
        # 4. 儲存到資料庫
        print(f"💾 儲存到資料庫...")
        record_id = mysql_manager.insert_clean_news(
            video_id=video_id,
            title=title,
            original_url=video.get('link', ''),
            duration=video.get('duration', ''),
            date=video.get('date', ''),
            cleaned_content=cleaned_content
        )
        
        if record_id:
            print(f"✅ 成功儲存 (ID: {record_id}): {title}")
            return True
        else:
            print(f"❌ 資料庫儲存失敗: {title}")
            return False
            
    except Exception as e:
        print(f"❌ 處理失敗: {str(e)[:200]} - {title}")
        return False
    finally:
        # 清理暫存檔案
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
                print(f"🗑️ 清理暫存檔案: {os.path.basename(temp_wav)}")
            except Exception as e:
                print(f"⚠️ 清理檔案失敗: {e}")

# ========== Selenium 網頁爬取 ==========
def setup_driver():
    """設置 Chrome WebDriver（整合網路監控功能）"""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--enable-logging')
    options.add_argument('--log-level=0')
    
    # 🔇 完全靜音設定
    options.add_argument('--mute-audio')
    options.add_argument('--disable-audio-output')
    options.add_argument('--disable-audio')
    options.add_argument('--disable-sound')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--autoplay-policy=no-user-gesture-required')
    options.add_argument('--disable-background-media')
    options.add_argument('--disable-background-audio')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-webgl')
    options.add_argument('--disable-webgl2')
    
    # 設定媒體自動播放政策為靜音
    prefs = {
        "profile.default_content_setting_values": {
            "media_stream": 2,  # 拒絕媒體串流
            "notifications": 2,  # 拒絕通知
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings": {
            "media_stream": 2
        }
    }
    options.add_experimental_option("prefs", prefs)
    
    # 🔍 啟用效能日誌以監控網路請求
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    return webdriver.Chrome(options=options)

def get_video_list(driver):
    """擷取影片清單"""
    try:
        driver.get("https://tv.cctv.com/lm/lijian/index.shtml")
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        videos = []
        
        for item in soup.select('ul > li'):
            try:
                a = item.select_one('div.image a')
                img = a.select_one('img') if a else None
                link = a['href'] if a else ''
                title = img.get('title', '').strip() if img else ''
                duration = item.select_one('span.time').text.strip() if item.select_one('span.time') else ''
                date = item.select_one('span.year').text.strip() if item.select_one('span.year') else ''
                
                if link and title:
                    videos.append({
                        'link': link, 
                        'title': title, 
                        'duration': duration, 
                        'date': date
                    })
            except Exception as e:
                continue
                
        print(f"📋 發現 {len(videos)} 個影片")
        return videos
        
    except Exception as e:
        print(f"❌ 擷取影片清單失敗: {e}")
        return []

def extract_m3u8(driver, video_url):
    """擷取 M3U8 串流連結（優先尋找 2000.m3u8 高品質串流）"""
    try:
        print(f"🔍 正在載入網頁並監控網路請求...")
        driver.get("about:blank")
        driver.get(video_url)
        time.sleep(10)  # 增加等待時間確保完全載入
        
        # 方法1：從網路日誌中擷取
        logs = driver.get_log("performance")
        m3u8_urls = []
        high_quality_urls = []
        
        for entry in logs:
            try:
                msg = json.loads(entry['message'])['message']
                if msg['method'] == 'Network.responseReceived':
                    response_url = msg['params']['response']['url']
                    if '.m3u8' in response_url:
                        m3u8_urls.append(response_url)
                        # 優先收集 2000.m3u8 高品質串流
                        if '2000.m3u8' in response_url:
                            high_quality_urls.append(response_url)
                            print(f"✅ 找到高品質 m3u8: {response_url}")
            except:
                continue
        
        # 優先返回高品質串流
        if high_quality_urls:
            return high_quality_urls[0]
        
        # 方法2：從頁面源碼中尋找（如果網路日誌沒找到）
        if not m3u8_urls:
            print("🔄 在頁面源碼中搜尋 m3u8...")
            page_source = driver.page_source
            
            # 多種 m3u8 模式，優先尋找 2000.m3u8
            patterns = [
                r'https?://[^\s"\'<>]+2000\.m3u8[^\s"\'<>]*',  # 優先：2000.m3u8
                r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',      # 一般：任何 .m3u8
                r'"(https?://[^"]+2000\.m3u8[^"]*)"',          # 引號內的 2000.m3u8
                r'"(https?://[^"]+\.m3u8[^"]*)"',              # 引號內的 .m3u8
                r"'(https?://[^']+2000\.m3u8[^']*)'",          # 單引號內的 2000.m3u8
                r"'(https?://[^']+\.m3u8[^']*)'",              # 單引號內的 .m3u8
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, page_source)
                if matches:
                    for match in matches:
                        # 如果找到 2000.m3u8，立即返回
                        if '2000.m3u8' in match:
                            print(f"✅ 在頁面源碼中找到高品質串流: {match}")
                            return match
                    # 如果沒有 2000.m3u8，收集到列表中
                    m3u8_urls.extend(matches)
        
        # 方法3：優先選擇包含特定關鍵字的串流
        if m3u8_urls:
            # 優先選擇直接的 CCTV/CNTV 連結
            for url in m3u8_urls:
                if any(keyword in url for keyword in ['dh5.cntv.cdn20.com', 'cntv', 'cctv']):
                    if not url.startswith('https://p.data.cctv.com/play'):
                        print(f"🔗 找到直接 M3U8: {url}")
                        return url
            
            # 如果沒有直接連結，嘗試解析 API 連結
            for url in m3u8_urls:
                if 'p.data.cctv.com/play' in url:
                    print(f"🔄 發現 API 連結，嘗試解析: {url[:100]}...")
                    real_m3u8 = parse_cctv_api_url(url)
                    if real_m3u8:
                        return real_m3u8
            
            # 最後選擇第一個可用的 m3u8
            print(f"🔗 使用第一個可用的 M3U8: {m3u8_urls[0]}")
            return m3u8_urls[0]
        
        print(f"⚠️ 未找到任何 M3U8 連結")
        return None
        
    except Exception as e:
        print(f"❌ 擷取 M3U8 失敗: {e}")
        return None

def parse_cctv_api_url(api_url):
    """解析 CCTV API URL 獲取真正的 M3U8 連結"""
    try:
        # 從 API URL 中提取 streamUrl 參數
        import urllib.parse
        
        # 解析 URL 參數
        parsed = urllib.parse.urlparse(api_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        # 獲取 gokey 參數並解碼
        gokey = params.get('gokey', [''])[0]
        if gokey:
            # 解碼 gokey 中的參數
            decoded_gokey = urllib.parse.unquote(gokey)
            
            # 提取 streamUrl
            if 'streamUrl=' in decoded_gokey:
                stream_url_start = decoded_gokey.find('streamUrl=') + len('streamUrl=')
                stream_url_end = decoded_gokey.find('&', stream_url_start)
                if stream_url_end == -1:
                    stream_url_end = len(decoded_gokey)
                
                stream_url = decoded_gokey[stream_url_start:stream_url_end]
                stream_url = urllib.parse.unquote(stream_url)
                
                if '.m3u8' in stream_url and 'http' in stream_url:
                    print(f"✅ 成功解析出 M3U8: {stream_url}")
                    return stream_url
        
        # 備用方法：直接訪問 API 並解析回應
        print(f"🔄 嘗試直接訪問 API...")
        response = requests.get(api_url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Referer': 'https://tv.cctv.com/'
        }, timeout=10)
        
        if response.status_code == 200:
            # 嘗試從回應中找到 M3U8 連結
            content = response.text
            import re
            m3u8_pattern = r'https?://[^"\s]+\.m3u8[^"\s]*'
            matches = re.findall(m3u8_pattern, content)
            
            for match in matches:
                if 'cntv' in match or 'cctv' in match:
                    print(f"✅ 從 API 回應解析出 M3U8: {match}")
                    return match
        
        print(f"❌ 無法解析 API URL")
        return None
        
    except Exception as e:
        print(f"❌ 解析 API URL 失敗: {e}")
        return None

# ========== 主程式 ==========
def main():
    """主程式邏輯"""
    # 初始化組件
    try:
        print("🚀 系統初始化中...")
        
        # 初始化 ASR
        asr = ASRWrapper()
        print("✅ Whisper CLI 初始化成功")
        
        # 初始化 MySQL
        mysql_manager = MySQLManager()
        print("✅ MySQL 初始化成功")
        
        # 初始化 WebDriver
        driver = setup_driver()
        print("✅ WebDriver 初始化成功")
        
    except Exception as e:
        print(f"❌ 系統初始化失敗: {e}")
        return
    
    # 主監控循環
    seen_links = set()
    cycle_count = 0
    total_processed = 0
    total_successful = 0
    
    print(f"\n🔄 開始監控模式 (每分鐘檢查一次)")
    print("🚨 按 Ctrl+C 可停止程式")
    print("=" * 80)
    
    try:
        while True:
            cycle_count += 1
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print(f"\n[{current_time}] 🔍 第 {cycle_count} 次檢查...")
            
            # 獲取影片清單
            videos = get_video_list(driver)
            if not videos:
                print("⚠️ 未找到影片，等待下次檢查...")
                time.sleep(60)
                continue
            
            # 處理新影片
            new_videos = [v for v in videos if v['link'] not in seen_links]
            if not new_videos:
                print("😴 無新影片，等待下次檢查...")
                time.sleep(60)
                continue
            
            print(f"🆕 發現 {len(new_videos)} 個新影片")
            
            for i, video in enumerate(new_videos, 1):
                try:
                    print(f"\n--- 處理第 {i}/{len(new_videos)} 個影片 ---")
                    
                    # 標記為已見過（避免重複處理）
                    seen_links.add(video['link'])
                    total_processed += 1
                    
                    # 獲取 M3U8 連結
                    m3u8_url = extract_m3u8(driver, video['link'])
                    if not m3u8_url:
                        print(f"⚠️ 無法獲取 M3U8: {video['title']}")
                        continue
                    
                    video['m3u8'] = m3u8_url
                    
                    # 處理影片
                    success = process_video(video, asr, mysql_manager)
                    if success:
                        total_successful += 1
                    
                    print(f"📊 當前統計: 處理 {total_processed}, 成功 {total_successful}")
                    
                except Exception as e:
                    print(f"❌ 處理影片失敗: {str(e)[:200]}")
                    continue
            
            print(f"\n⏰ 等待 60 秒後進行下次檢查...")
            time.sleep(60)
            
    except KeyboardInterrupt:
        print(f"\n\n🛑 收到停止信號，正在安全退出...")
        
        print(f"\n📊 最終統計:")
        print(f"   檢查週期: {cycle_count}")
        print(f"   總處理: {total_processed}")
        print(f"   總成功: {total_successful}")
        print(f"   成功率: {total_successful/max(total_processed,1)*100:.1f}%")
        
    except Exception as e:
        print(f"\n❌ 程式異常: {e}")
        
    finally:
        # 清理資源
        try:
            driver.quit()
            print("🗑️ WebDriver 已關閉")
        except:
            pass
        
        print("👋 程式已安全退出")

if __name__ == "__main__":
    print("🚀 啟動整合式轉錄和文字整理系統")
    print("📊 功能: 網頁監控 + 音頻轉錄 + AI文字清理 + 資料庫儲存")
    print("🎯 目標: random_event.clean_news")
    main()
