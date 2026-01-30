# Reel-to-Guide API

A FastAPI backend that extracts step-by-step guides from Instagram/TikTok/YouTube videos using Google Gemini 2.0 Flash.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Install ffmpeg (Required for yt-dlp):
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

3. Run Server:
```bash
export GEMINI_API_KEY="your_key"
uvicorn main:app --reload
```

## Deploy to Vercel

Vercel supports Python Serverless Functions!

1. Install Vercel CLI: `npm i -g vercel`
2. Run `vercel` in this directory.
