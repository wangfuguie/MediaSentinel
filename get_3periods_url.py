import requests
import re
import json
import time
import os
from datetime import datetime
from dotenv import load_dotenv

# 交由 Selenium Manager 依瀏覽器版本管理 driver，忽略 PATH 中可能過期的版本。
os.environ.setdefault("SE_SKIP_DRIVER_IN_PATH", "true")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

load_dotenv()



class CSVHandler:
    """CSV 檔案儲存處理類別"""

    def __init__(self, file_path='videos.csv'):
        self.file_path = file_path
        self.columns = ['timestamp', 'type', 'news_url', 'm3u8_url', 'news_title', 'publish_time']
        if not os.path.isfile(self.file_path):
            pd.DataFrame(columns=self.columns).to_csv(self.file_path, index=False)

    def get_publish_time(self, news_url):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0'
            }
            response = requests.get(news_url, headers=headers)
            response.raise_for_status()

            time_pattern = r'<span class="time"[^>]*>([^<]+)</span>'
            match = re.search(time_pattern, response.text)

            if match:
                return match.group(1).strip()

            fallback_pattern = r'([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2})'
            match = re.search(fallback_pattern, response.text)
            if match:
                return match.group(1).strip()

            return datetime.now().strftime('%Y-%m-%d %H:%M')

        except Exception as e:
            print(f"❌ 擷取發布時間失敗: {e}")
            return datetime.now().strftime('%Y-%m-%d %H:%M')

    def save_video_data(self, news_url, m3u8_url, video_type="noon", news_title=""):
        try:
            timestamp = str(int(time.time()))
            publish_time = self.get_publish_time(news_url)

            new_data = {
                'timestamp': timestamp,
                'type': video_type,
                'news_url': news_url,
                'm3u8_url': m3u8_url,
                'news_title': news_title,
                'publish_time': publish_time
            }

            df = pd.DataFrame([new_data])
            df.to_csv(self.file_path, mode='a', index=False, header=False)

            print(f"✅ 成功儲存到 CSV 檔案: {self.file_path}")
            return True, None
        except Exception as e:
            print(f"❌ 儲存到 CSV 失敗: {e}")
            return False, str(e)

    def get_recent_videos(self, limit=10):
        try:
            df = pd.read_csv(self.file_path)
            return df.tail(limit).to_dict('records')
        except Exception as e:
            print(f"❌ 讀取 CSV 失敗: {e}")
            return []

    def check_video_exists(self, news_url):
        """檢查影片是否已存在於 CSV 中"""
        try:
            df = pd.read_csv(self.file_path)
            return news_url in df['news_url'].values
        except Exception as e:
            print(f"❌ 檢查影片是否存在失敗: {e}")
            return False


class CCTVMultiMonitor:
    """CCTV 多URL新聞監控類別"""
    
    def __init__(self, interval_minutes=5):
        self.interval_minutes = interval_minutes
        self.seen_ids = {
            'morning': set(),
            'noon': set(),
            'night': set()
        }
        self.csv_handler = CSVHandler()
        
        # 定義URL與類型的映射
        self.url_mapping = {
            'https://tv.cctv.com/lm/gfjszb/index.shtml': 'morning',
            'https://tv.cctv.com/lm/zwgfjs/index.shtml': 'noon',
            'https://tv.cctv.com/lm/jsbd/index.shtml': 'night'
        }
        
        # 為每個URL創建獨立的driver
        self.drivers = {}
        
    def setup_driver(self, url_key):
        """設定 Chrome 瀏覽器驅動"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        
        # 禁用音效和自動播放
        chrome_options.add_argument('--mute-audio')
        chrome_options.add_argument('--disable-audio')
        chrome_options.add_argument('--disable-sound')
        chrome_options.add_argument('--autoplay-policy=no-user-gesture-required')
        chrome_options.add_argument('--disable-background-media')
        chrome_options.add_argument('--disable-background-audio')
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--disable-webgl")
        chrome_options.add_argument("--disable-webgl2")
        # 設定偏好設定來禁用媒體自動播放
        prefs = {
            "profile.default_content_setting_values.media_stream": 2,
            "profile.default_content_settings.popups": 0,
            "profile.managed_default_content_settings.media_stream": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_settings.media_stream": 2
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        driver = webdriver.Chrome(options=chrome_options)
        self.drivers[url_key] = driver
        return driver

    def fetch_latest_videos(self, url, video_type):
        """獲取指定URL的最新影片列表"""
        try:
            if url not in self.drivers:
                self.setup_driver(url)
            
            driver = self.drivers[url]
            driver.get(url)

            # 模擬滑動載入更多
            for _ in range(5):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)

            elements = driver.find_elements(By.CSS_SELECTOR, "ul.rililist a[href]")
            videos = []
            for el in elements:
                href = el.get_attribute('href')
                title = el.text.strip()
                if href and "VIDE" in href:
                    videos.append({
                        "url": href,
                        "title": title,
                        "id": href.split("/")[-1].replace(".shtml", ""),
                        "type": video_type
                    })
            return videos
        except Exception as e:
            print(f"❌ 擷取 {video_type} 影片錯誤：{e}")
            return []

    def get_new_videos_for_url(self, url, video_type):
        """獲取指定URL的新影片（未處理過的）"""
        try:
            videos = self.fetch_latest_videos(url, video_type)
            new_videos = []
            
            for v in videos:
                # 檢查是否已在記憶體中處理過
                if v["id"] not in self.seen_ids[video_type]:
                    # 檢查是否已存在於 CSV 檔案中
                    if not self.csv_handler.check_video_exists(v["url"]):
                        new_videos.append(v)
                    # 無論是否新增，都加入已見過的列表
                    self.seen_ids[video_type].add(v["id"])
                
            return new_videos
        except Exception as e:
            print(f"❌ 擷取 {video_type} 新影片錯誤：{e}")
            return []

    def monitor_single_url(self, url, video_type):
        """監控單一URL的新影片"""
        while True:
            try:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 檢查 {video_type} 新影片...")
                
                new_videos = self.get_new_videos_for_url(url, video_type)
                
                if new_videos:
                    print(f"✅ 發現 {len(new_videos)} 則 {video_type} 新影片：")
                    for v in new_videos:
                        print(f"▶ [{video_type.upper()}] {v['title']}")
                        print(f"🔗 {v['url']}")
                    
                    # 處理新影片
                    self.process_new_videos(new_videos)
                    print("-" * 50)
                else:
                    print(f"📭 無 {video_type} 新影片")
                
                time.sleep(self.interval_minutes * 60)
                
            except Exception as e:
                print(f"❌ 監控 {video_type} 過程發生錯誤：{e}")
                time.sleep(30)  # 錯誤時短暫等待後重試

    def process_new_videos(self, videos):
        """處理新影片的函數"""
        print(f"📋 收到 {len(videos)} 個新影片，開始處理...")
        
        extractor = M3U8Extractor()
        try:
            for video in videos:
                print(f"\n處理: [{video['type'].upper()}] {video['title']}")
                print(f"影片 URL: {video['url']}")
                
                # 擷取 M3U8 URL
                m3u8_url = extractor.extract_m3u8_url(video['url'])
                
                if m3u8_url:
                    print(f"✅ 成功獲取 M3U8: {m3u8_url}")
                    
                    # 儲存到 CSV
                    success, result = self.csv_handler.save_video_data(
                        news_url=video['url'],
                        m3u8_url=m3u8_url,
                        video_type=video['type'],
                        news_title=video['title']
                    )
                    
                    if success:
                        print("💾 資料已成功儲存到 CSV 檔案")
                    else:
                        print(f"❌ 儲存失敗: {result}")
                        
                else:
                    print("❌ 無法獲取 M3U8 URL，跳過儲存")
                    
                print("-" * 40)
                
        finally:
            extractor.close_driver()

    def run_monitoring(self):
        """
        啟動多線程監控所有URL
        """
        print(f"🚀 啟動 CCTV 多URL新聞監控... 每 {self.interval_minutes} 分鐘檢查一次")
        print(f"💾 資料將儲存到 CSV 檔案: {self.csv_handler.file_path}")
        print(f"📺 監控的URL:")
        for url, video_type in self.url_mapping.items():
            print(f"   - {video_type.upper()}: {url}")
        print("-" * 80)
        
        # 使用線程池來並行監控多個URL
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for url, video_type in self.url_mapping.items():
                future = executor.submit(self.monitor_single_url, url, video_type)
                futures.append(future)
            
            try:
                # 等待所有線程完成
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                print("\n🛑 監控中止")
            except Exception as e:
                print(f"\n❌ 監控過程發生錯誤：{e}")
            finally:
                self.close_all_drivers()

    def close_all_drivers(self):
        """關閉所有瀏覽器驅動"""
        for url, driver in self.drivers.items():
            try:
                driver.quit()
                print(f"✅ 已關閉 {self.url_mapping.get(url, 'unknown')} 的瀏覽器驅動")
            except Exception as e:
                print(f"❌ 關閉驅動失敗: {e}")
        self.drivers.clear()

    def show_recent_videos(self, limit=10):
        """顯示最近的影片記錄"""
        print(f"\n📋 最近 {limit} 則影片記錄:")
        print("-" * 80)
        
        recent_videos = self.csv_handler.get_recent_videos(limit)
        
        if recent_videos:
            for i, video in enumerate(recent_videos, 1):
                print(f"{i}. [{video['type'].upper()}] {video['news_title']}")
                print(f"   發布時間: {video['publish_time']}")
                print(f"   新聞URL: {video['news_url']}")
                print(f"   M3U8 URL: {video['m3u8_url']}")
                print("-" * 40)
        else:
            print("📭 無影片記錄")


class M3U8Extractor:
    """M3U8 URL 擷取器"""
    
    def __init__(self):
        self.driver = None
        
    def setup_driver(self):
        """設定 Chrome 瀏覽器驅動（用於網路監控）"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--enable-logging')
        chrome_options.add_argument('--log-level=0')
        
        # 禁用音效和自動播放
        chrome_options.add_argument('--mute-audio')
        chrome_options.add_argument('--disable-audio')
        chrome_options.add_argument('--disable-sound')
        chrome_options.add_argument('--autoplay-policy=no-user-gesture-required')
        chrome_options.add_argument('--disable-background-media')
        chrome_options.add_argument('--disable-background-audio')
        
        # 設定偏好設定來禁用媒體自動播放
        prefs = {
            "profile.default_content_setting_values.media_stream": 2,
            "profile.default_content_settings.popups": 0,
            "profile.managed_default_content_settings.media_stream": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_settings.media_stream": 2
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        
        self.driver = webdriver.Chrome(options=chrome_options)
        return self.driver

    def extract_from_network(self, url):
        """從網路請求中擷取 m3u8 URL"""
        if not self.driver:
            self.setup_driver()
            
        try:
            print(f"🔍 正在載入網頁並監控網路請求...")
            self.driver.get(url)
            time.sleep(10)
            
            # 獲取網路日誌
            logs = self.driver.get_log('performance')
            m3u8_urls = []
            
            for entry in logs:
                message = json.loads(entry['message'])
                if message['message']['method'] == 'Network.responseReceived':
                    response_url = message['message']['params']['response']['url']
                    if '.m3u8' in response_url and '2000.m3u8' in response_url:
                        m3u8_urls.append(response_url)
                        print(f"✅ 找到 m3u8 URL: {response_url}")
            
            if not m3u8_urls:
                # 嘗試在頁面源碼中尋找
                page_source = self.driver.page_source
                m3u8_pattern = r'https?://[^\s"\'<>]+2000\.m3u8[^\s"\'<>]*'
                matches = re.findall(m3u8_pattern, page_source)
                if matches:
                    m3u8_urls.extend(matches)
                    print(f"✅ 在頁面源碼中找到: {matches[0]}")
            
            return m3u8_urls[0] if m3u8_urls else None
            
        except Exception as e:
            print(f"❌ 網路監控擷取失敗: {e}")
            return None

    def extract_from_html(self, url):
        """從 HTML 內容中擷取 m3u8 URL（備用方法）"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            patterns = [
                r'https?://[^\s"\'<>]+2000\.m3u8[^\s"\'<>]*',
                r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
                r'"(https?://[^"]+\.m3u8[^"]*)"',
                r"'(https?://[^']+\.m3u8[^']*)'",
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, response.text)
                if matches:
                    for match in matches:
                        if '2000.m3u8' in match:
                            return match
                    return matches[0]
            
            return None
            
        except Exception as e:
            print(f"❌ HTML 擷取失敗: {e}")
            return None

    def extract_m3u8_url(self, video_url):
        """擷取 m3u8 URL（主要方法）"""
        print(f"📺 正在擷取 m3u8 URL: {video_url}")
        
        # 方法1：網路監控
        m3u8_url = self.extract_from_network(video_url)
        
        # 方法2：HTML 解析（備用）
        if not m3u8_url:
            print("🔄 嘗試備用方法...")
            m3u8_url = self.extract_from_html(video_url)
        
        if m3u8_url:
            print(f"✅ 成功擷取 m3u8 URL: {m3u8_url}")
        else:
            print("❌ 無法擷取 m3u8 URL")
            
        return m3u8_url

    def close_driver(self):
        """關閉瀏覽器驅動"""
        if self.driver:
            self.driver.quit()
            self.driver = None

# 示例用法
if __name__ == "__main__":
    print("🎯 CCTV 多URL新聞監控模組 (CSV 儲存版本)")
    print("=" * 80)
    
    # 創建多URL監控實例
    monitor = CCTVMultiMonitor(interval_minutes=1)  # 1分鐘測試間隔
    
    # 可以先查看最近的記錄
    monitor.show_recent_videos(5)
    
    # 啟動監控
    monitor.run_monitoring()
