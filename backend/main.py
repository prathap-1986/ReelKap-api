from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel
import google.generativeai as genai
import subprocess
import os
import uuid
import time
import json
import logging
from datetime import datetime

# --- CONFIGURATION ---
# In production, use environment variables!
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Configure Gemini
if not GEMINI_API_KEY:
    # Warning instead of crash for local dev if key missing
    print("WARNING: GEMINI_API_KEY not set")
else:
    genai.configure(api_key=GEMINI_API_KEY)

# Configure Logging (JSON format for Analytics/Marketing)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reelkap")

app = FastAPI(title="Reel-to-Guide API")

# --- DATA MODELS ---
class VideoRequest(BaseModel):
    url: str

class Step(BaseModel):
    step_number: int
    instruction: str
    visual_cue: str | None = None

class GuideResponse(BaseModel):
    title: str
    description: str
    tools_needed: list[str]
    steps: list[Step]
    tips: list[str]

class HookAnalysis(BaseModel):
    type: str
    script: str
    why_it_worked: str
    improvement: str

class StructurePhase(BaseModel):
    time: str
    phase: str
    action: str

class RemixTemplate(BaseModel):
    niche: str
    script: str
    visual_cues: list[str]

class ViralAnalysisResponse(BaseModel):
    viral_score: int
    hook_analysis: HookAnalysis
    structure_breakdown: list[StructurePhase]
    remix_template: RemixTemplate

# --- HELPER FUNCTIONS ---
def cleanup_file(path: str):
    """Deletes a temporary file."""
    if os.path.exists(path):
        os.remove(path)
        print(f"Deleted temp file: {path}")

def get_platform(url: str) -> str:
    if "instagram" in url: return "instagram"
    if "tiktok" in url: return "tiktok"
    if "youtube" in url or "youtu.be" in url: return "youtube"
    return "other"

def log_event(data: dict):
    """Logs structured JSON event for downstream analytics (PostHog/Mixpanel)"""
    # In a real setup, this would send data to Mixpanel/PostHog API directly.
    # For now, we print JSON which cloud providers (Render/AWS) capture.
    data["timestamp"] = datetime.utcnow().isoformat()
    logger.info(json.dumps(data))

def download_video(url: str, output_path: str):
    """Downloads video using yt-dlp."""
    cmd = [
        "yt-dlp",
        url,
        "-o", output_path,
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--force-overwrites",
        "--quiet",
        "--no-warnings"
    ]
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download video. Instagram might be blocking or link is invalid. Error: {e}")

# --- API ENDPOINTS ---

@app.post("/analyze", response_model=GuideResponse)
async def analyze_video(request: VideoRequest, background_tasks: BackgroundTasks, req: Request):
    """
    1. Receives Instagram/TikTok URL.
    2. Downloads video locally.
    3. Uploads to Gemini.
    4. Extracts steps.
    5. Returns structured JSON guide.
    """
    start_time = time.time()
    platform = get_platform(request.url)
    
    # 1. Generate temp filename
    video_filename = f"temp_{uuid.uuid4()}.mp4"
    
    # Initial Event Log (User Attempt)
    log_event({
        "event": "analysis_started",
        "url": request.url,
        "platform": platform,
        "client_ip": req.client.host if req.client else "unknown"
    })

    try:
        # 2. Download
        download_video(request.url, video_filename)
        
        # 3. Upload to Gemini
        video_file = genai.upload_file(path=video_filename)
        
        # Wait for processing state
        while video_file.state.name == "PROCESSING":
            time.sleep(1)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise HTTPException(status_code=500, detail="Gemini failed to process the video file.")

        # 4. Analyze with Gemini 2.0 Flash Lite (Cheaper & Faster)
        # Using "lite" model to save tokens and avoid rate limits
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash-lite-preview-02-05", 
            generation_config={"response_mime_type": "application/json"}
        )

        prompt = """
        You are an expert technical writer. Watch this video tutorial carefully.
        Extract the content into a structured guide.
        
        Return a JSON object with this schema:
        {
            "title": "Title of the tutorial",
            "description": "Brief summary of what this teaches",
            "tools_needed": ["tool 1", "tool 2"],
            "steps": [
                {"step_number": 1, "instruction": "Do this first...", "visual_cue": "Screen shows X"}
            ],
            "tips": ["Extra tip mentioned"]
        }
        """

        response = model.generate_content([video_file, prompt])
        result_json = json.loads(response.text)
        
        # 5. Cleanup
        background_tasks.add_task(cleanup_file, video_filename)
        background_tasks.add_task(genai.delete_file, video_file.name)

        # Success Event Log (Marketing Gold: What content is popular?)
        log_event({
            "event": "analysis_completed",
            "url": request.url,
            "platform": platform,
            "duration_sec": round(time.time() - start_time, 2),
            "extracted_title": result_json.get("title", "Unknown"),
            "extracted_topic": result_json.get("description", "")[:50] + "...",
            "status": "success"
        })

        return result_json

    except Exception as e:
        # Failure Event Log (For debugging & churn prevention)
        log_event({
            "event": "analysis_failed",
            "url": request.url,
            "platform": platform,
            "error": str(e),
            "status": "failed"
        })
        
        # Cleanup
        if os.path.exists(video_filename):
            os.remove(video_filename)

        # Handle Rate Limits Gracefully
        if "429" in str(e):
            raise HTTPException(
                status_code=429, 
                detail="Server is busy (Rate Limit Exceeded). Please try again in 30 seconds."
            )
        
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/viral-analyze", response_model=ViralAnalysisResponse)
async def viral_analyze_video(request: VideoRequest, background_tasks: BackgroundTasks, req: Request):
    """
    1. Receives Instagram/TikTok URL.
    2. Downloads video locally.
    3. Uploads to Gemini.
    4. Extracts viral structure (Hook, Retention, CTA).
    5. Returns structured JSON breakdown.
    """
    start_time = time.time()
    platform = get_platform(request.url)
    video_filename = f"temp_viral_{uuid.uuid4()}.mp4"
    
    log_event({
        "event": "viral_analysis_started",
        "url": request.url,
        "platform": platform,
        "client_ip": req.client.host if req.client else "unknown"
    })

    try:
        download_video(request.url, video_filename)
        video_file = genai.upload_file(path=video_filename)
        
        while video_file.state.name == "PROCESSING":
            time.sleep(1)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise HTTPException(status_code=500, detail="Gemini failed to process the video file.")

        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash", 
            generation_config={"response_mime_type": "application/json"}
        )

        VIRAL_PROMPT = """
        You are a Viral Content Analyst. Analyze this short video and deconstruct why it went viral.
        Provide the output strictly in the following JSON format:

        {
          "viral_score": <number 0-100 based on hook strength and retention>,
          "hook_analysis": {
            "type": "<e.g. Pattern Interrupt, Visual Curiosity, Negative Hook>",
            "script": "<The exact words spoken in the first 3 seconds>",
            "why_it_worked": "<Psychological reason>",
            "improvement": "<How to make it 10% better>"
          },
          "structure_breakdown": [
            { "time": "<timestamp>", "phase": "<Hook/Value/CTA>", "action": "<What happens visually/audially>" }
          ],
          "remix_template": {
            "niche": "<Suggest a different niche, e.g. Real Estate -> Coding>",
            "script": "<A rewritten script using the same viral structure for the new niche>",
            "visual_cues": [
              "<Timestamp 1 description>",
              "<Timestamp 2 description>"
            ]
          }
        }
        """

        response = model.generate_content([video_file, VIRAL_PROMPT])
        result_json = json.loads(response.text)
        
        background_tasks.add_task(cleanup_file, video_filename)
        background_tasks.add_task(genai.delete_file, video_file.name)

        log_event({
            "event": "viral_analysis_completed",
            "url": request.url,
            "platform": platform,
            "duration_sec": round(time.time() - start_time, 2),
            "score": result_json.get("viral_score", 0),
            "status": "success"
        })

        return result_json

    except Exception as e:
        log_event({
            "event": "viral_analysis_failed",
            "url": request.url,
            "error": str(e),
            "status": "failed"
        })
        if os.path.exists(video_filename):
            os.remove(video_filename)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online", "service": "Reel-to-Guide API"}
