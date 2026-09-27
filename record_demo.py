import os
import time
import subprocess
from playwright.sync_api import sync_playwright

def record():
    os.makedirs("/tmp/demo_video", exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir="/tmp/demo_video",
            record_video_size={"width": 1280, "height": 800}
        )
        page = context.new_page()

        print("Navigating to http://localhost:8080...")
        page.goto("http://localhost:8080")
        page.wait_for_timeout(2000)

        # Prompt 1: Doing what the app does best - search high priority tasks
        print("Submitting Prompt 1: High Priority Tasks...")
        page.click(".prompt-chips .chip-button:nth-child(1)")
        page.wait_for_timeout(8000)

        # Prompt 2: Richer prompt showing off tool call, DB lookup, and image generation
        print("Submitting Prompt 2: Generate task badge and check holidays...")
        page.fill("#input", "Generate a completion badge for task-101 and show upcoming public holidays")
        page.wait_for_timeout(1000)
        page.click(".send-btn")

        # Wait for agent tool calls and image rendering
        page.wait_for_timeout(14000)

        video_path = page.video.path()
        context.close()
        browser.close()
        print(f"Raw video recorded at: {video_path}")

        # Target output location in artifacts directory
        artifact_dir = "/config/.gemini/antigravity/brain/01b3ef60-a467-437e-a7fb-0e79cafa18f4"
        os.makedirs(artifact_dir, exist_ok=True)
        out_mp4 = os.path.join(artifact_dir, "agent_demo.mp4")

        # Convert webm to mp4 via ffmpeg
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4
        ], check=True)

        print(f"Final demo video saved at: {out_mp4}")

if __name__ == "__main__":
    record()
