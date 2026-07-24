import concurrent.futures
import subprocess
import sys
import os
from dotenv import load_dotenv
from whisper_setup import ensure_whisper_cpp

load_dotenv()

# 要執行的 Python 檔案清單
scripts = [
    "clean_3periods.py",
    "clean_24hr.py",
    "get_3periods_url.py",
    "get_wav_24hr.py",
    "random_event.py",
    "sql_create.py",
    "whisper_24hr.py"
]

def run_script(script_name):
    """執行單一 Python 檔案"""
    print(f"執行中: {script_name}")
    result = subprocess.run([sys.executable, script_name], capture_output=True, text=True)
    print(f"{script_name} 完成，輸出如下：\n{result.stdout}")
    if result.stderr:
        print(f"{script_name} 發生錯誤：\n{result.stderr}")
    return script_name, result.returncode

if __name__ == "__main__":
    # 在平行啟動轉錄程式前，先完成一次 whisper.cpp 安裝與建置。
    # 之後各子程式會直接重用相同的 CLI 和模型。
    try:
        ensure_whisper_cpp()
    except Exception as error:
        print(f"無法準備 whisper.cpp: {error}", file=sys.stderr)
        raise SystemExit(1)

    # 平行執行所有腳本
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(run_script, script) for script in scripts]
        for future in concurrent.futures.as_completed(futures):
            script_name, return_code = future.result()
            print(f"{script_name} 結束，Return code: {return_code}")
