import requests
import os
import subprocess
import re
import time
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urljoin

load_dotenv()

# ——— 基本配置 ———
M3U8_URL = "https://ldocctvwbcdcnchw.v.cdn20.com/ldocctvwbcd/cdrmldcctv7_1_720P/playlist.m3u8?wsApp=HLS"
DOWNLOAD_DIR = "downloads"  # 暫存 TS 用，轉完即刪除
WAV_DIR = "wav_24hr"        # 儲存 WAV 的資料夾

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(WAV_DIR, exist_ok=True)

def check_ffmpeg():
    """檢查 ffmpeg 是否可用"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"[{datetime.utcnow()}] ✅ ffmpeg 可用")
            return True
        else:
            print(f"[{datetime.utcnow()}] ❌ ffmpeg 運行錯誤")
            return False
    except FileNotFoundError:
        print(f"[{datetime.utcnow()}] ❌ 找不到 ffmpeg，請確認已安裝並設定環境變數")
        return False
    except Exception as e:
        print(f"[{datetime.utcnow()}] ❌ ffmpeg 檢查異常: {e}")
        return False

def fetch_playlist(url: str) -> str:
    """下載 m3u8 清單內容"""
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    print(f"[{datetime.utcnow()}] 📥 成功取得 playlist")
    return resp.text

def parse_segments(playlist_text: str):
    """解析 segment URI 與起始序號"""
    seq_match = re.search(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', playlist_text)
    start_seq = int(seq_match.group(1)) if seq_match else 0

    lines = playlist_text.splitlines()
    segs = [lines[i + 1].strip() for i, line in enumerate(lines) if line.startswith('#EXTINF')]
    print(f"[{datetime.utcnow()}] 🔍 找到 {len(segs)} 個段落，起始序號 {start_seq}")
    return start_seq, segs

def download_ts(uri: str, local_path: str) -> bool:
    """下載單一 TS 段"""
    full_url = uri if uri.startswith('http') else urljoin(M3U8_URL, uri)
    try:
        r = requests.get(full_url, timeout=10)
        r.raise_for_status()

        # 確保資料夾存在
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # 儲存 TS 檔
        with open(local_path, 'wb') as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"[{datetime.utcnow()}] ❌ TS 下載失敗: {e}")
        return False


def ts_to_wav(ts_path: str, wav_path: str) -> bool:
    """使用 ffmpeg 轉檔 .ts ➜ .wav"""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", ts_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            wav_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return os.path.exists(wav_path)
    except Exception as e:
        print(f"[{datetime.utcnow()}] ❌ ffmpeg 轉檔錯誤: {e}")
        return False

def continuous_run(interval: int = 4):
    """主處理流程"""
    print(f"[{datetime.utcnow()}] ▶️ 開始轉檔服務，間隔 {interval} 秒")

    if not check_ffmpeg():
        return

    handled = set()

    while True:
        try:
            playlist_text = fetch_playlist(M3U8_URL)
            base_seq, seg_uris = parse_segments(playlist_text)

            for i, uri in enumerate(seg_uris):
                seq_no = base_seq + i
                if seq_no in handled:
                    continue
                handled.add(seq_no)

                ts_path = os.path.join(DOWNLOAD_DIR, f"{seq_no}.ts")
                wav_path = os.path.join(WAV_DIR, f"{seq_no}.wav")

                if download_ts(uri, ts_path):
                    if ts_to_wav(ts_path, wav_path):
                        print(f"[{datetime.utcnow()}] ✅ 儲存 WAV: {wav_path}")
                    else:
                        print(f"[{datetime.utcnow()}] ❌ 轉檔失敗: {seq_no}")

                    # 轉檔完成後，刪除 TS
                    try:
                        os.remove(ts_path)
                    except Exception as e:
                        print(f"[{datetime.utcnow()}] ⚠️ 刪除 TS 檔失敗: {e}")
                else:
                    print(f"[{datetime.utcnow()}] ❌ 無法下載段落: {seq_no}")

            time.sleep(interval)

        except KeyboardInterrupt:
            print("🛑 使用者中斷，結束程式")
            break
        except Exception as e:
            print(f"[{datetime.utcnow()}] ❌ 執行錯誤: {e}")
            time.sleep(interval)

if __name__ == "__main__":
    continuous_run()
