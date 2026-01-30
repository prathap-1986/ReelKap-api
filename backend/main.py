from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import google.generativeai as genai
import subprocess
import os
import uuid
import time
import json

# --- CONFIGURATION ---
# In production, use environment variables!
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Configure Gemini
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set")
genai.configure(api_key=GEMINI_API_KEY)

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

# --- HELPER FUNCTIONS ---
def cleanup_file(path: str):
    """Deletes a temporary file."""
    if os.path.exists(path):
        os.remove(path)
        print(f"Deleted temp file: {path}")

def download_video(url: str, output_path: str):
    """Downloads video using yt-dlp."""
    # NOTE: In production (AWS/Cloud), Instagram blocks data center IPs.
    # You will need to add proxy arguments here: --proxy "http://user:pass@host:port"
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
    
    # Optional: If you have cookies.txt for auth
    # cmd.extend(["--cookies", "cookies.txt"])

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download video. Instagram might be blocking or link is invalid. Error: {e}")

# --- API ENDPOINTS ---

@app.post("/analyze", response_model=GuideResponse)
async def analyze_video(request: VideoRequest, background_tasks: BackgroundTasks):
    """
    1. Receives Instagram/TikTok URL.
    2. Downloads video locally.
    3. Uploads to Gemini.
    4. Extracts steps.
    5. Returns structured JSON guide.
    """
    
    # 1. Generate temp filename
    video_filename = f"temp_{uuid.uuid4()}.mp4"
    print(f"Processing URL: {request.url}")

    try:
        # 2. Download
        download_video(request.url, video_filename)
        
        # 3. Upload to Gemini
        print("Uploading to Gemini...")
        video_file = genai.upload_file(path=video_filename)
        
        # Wait for processing state
        while video_file.state.name == "PROCESSING":
            time.sleep(1)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise HTTPException(status_code=500, detail="Gemini failed to process the video file.")

        # 4. Analyze with Gemini 2.0 Flash
        # We use JSON mode for structured output suited for an app
        print("Analyzing content...")
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
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
        
        # Parse result
        result_json = json.loads(response.text)
        
        # 5. Cleanup (Delete local video and remote Gemini file to save space/cost)
        background_tasks.add_task(cleanup_file, video_filename)
        background_tasks.add_task(genai.delete_file, video_file.name)

        return result_json

    except Exception as e:
        # Cleanup even if we fail
        if os.path.exists(video_filename):
            os.remove(video_filename)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online", "service": "Reel-to-Guide API"}
