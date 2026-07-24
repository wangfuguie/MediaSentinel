# Media Intelligence and Sentiment Analysis System

This project collects news and video sources, downloads audio, transcribes speech with Whisper, cleans text with a configurable LLM, and stores the results in MySQL.

## Steps

### 1. Configure environment variables

macOS/Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and provide your MySQL and LLM settings.

### 2. Create the databases

```bash
python sql_create.py
```

### 3. Prepare Whisper

```bash
python whisper_setup.py
```

The first setup may take a few minutes to download and build Whisper.

### 4. Start the system

```bash
python main.py
```

`main.py` starts data collection, audio downloading, transcription, text cleanup, and database initialization in parallel. Some processes run continuously. Press `Ctrl+C` to stop the system.

---
# 與情分析系統

這是一套新聞與影音輿情自動化處理系統，可抓取新聞來源、下載音訊、使用 Whisper 進行語音轉文字、透過可設定的 LLM 清理內容，最後將結果寫入 MySQL。

## 系統流程

```mermaid
flowchart LR
    A[新聞／影音來源] --> B[抓取 URL]
    B --> C[下載並轉換 WAV]
    C --> D[Whisper 語音轉錄]
    D --> E[LLM 文字清理]
    E --> F[MySQL]
```

## 操作步驟

### 1. 設定環境變數

macOS／Linux：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

複製完成後，編輯 `.env` 並填入 MySQL 與 LLM 設定。

### 2. 建立資料庫

```bash
python sql_create.py
```

### 3. 準備 Whisper

```bash
python whisper_setup.py
```

首次執行需要數分鐘完成下載與建置。

### 4. 啟動系統

```bash
python main.py
```

`main.py` 會平行啟動資料抓取、音訊下載、轉錄、文字清理與資料庫初始化程序。部分程序是持續監控服務，不會自行結束；要停止系統請按 `Ctrl+C`。



