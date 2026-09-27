# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os
import subprocess
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

import google.auth
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import (
    AgentEngineSandboxCodeExecutor,
)
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.tool_context import ToolContext
from google.cloud import firestore, storage
from google.genai import types
from google.oauth2.credentials import Credentials

from .a2ui_utils import a2ui_callback

# Load environment variables from .env
load_dotenv()

# IMPORTANT: Hardcoded project ID and GCS bucket name as strings
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-01-281106dd16d5"
BUCKET_NAME = "task-scheduler-assets-qwiklabs-gcp-01-281106dd16d5"

# Memory Bank / Agent Engine ID from deployment_metadata.json
MEMORY_BANK_ID = "2919594248128954368"

# Resolve Agent Engine resource name from deployment_metadata.json
DEPLOYMENT_METADATA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "deployment_metadata.json"
)
AGENT_ENGINE_RESOURCE_NAME = f"projects/948495145432/locations/us-central1/reasoningEngines/{MEMORY_BANK_ID}"

if os.path.exists(DEPLOYMENT_METADATA_PATH):
    try:
        with open(DEPLOYMENT_METADATA_PATH, "r") as f:
            metadata = json.load(f)
            AGENT_ENGINE_RESOURCE_NAME = metadata.get(
                "remote_agent_runtime_id", AGENT_ENGINE_RESOURCE_NAME
            )
            MEMORY_BANK_ID = AGENT_ENGINE_RESOURCE_NAME.split("/")[-1]
    except Exception:
        pass

# Initialize Agent Platform sandbox code executor
code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME
)


def memory_service_builder() -> VertexAiMemoryBankService:
    """Returns a VertexAiMemoryBankService instance pointed at the deployed Memory Bank."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-central1",
        agent_engine_id=MEMORY_BANK_ID,
    )


memory_service = memory_service_builder()


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback to extract durable user facts and preferences into Vertex AI Memory Bank after each turn."""
    await callback_context.add_session_to_memory()
    return None


def get_firestore_client() -> firestore.Client:
    """Returns a Firestore client initialized with the hardcoded project ID."""
    try:
        token = (
            subprocess.check_output(["gcloud", "auth", "print-access-token"])
            .decode()
            .strip()
        )
        return firestore.Client(
            project=FIRESTORE_PROJECT_ID, credentials=Credentials(token)
        )
    except Exception:
        try:
            creds, _ = google.auth.default()
            return firestore.Client(project=FIRESTORE_PROJECT_ID, credentials=creds)
        except Exception:
            return firestore.Client(project=FIRESTORE_PROJECT_ID)


def search_tasks(
    query: str = "", category: str = "", priority: str = "", status: str = ""
) -> list[dict]:
    """Searches for tasks in the task scheduler database by keyword, category, priority, or status.

    Args:
        query: Optional search keyword in task title or description (e.g. 'proposal', 'schema', 'review').
        category: Optional category filter (e.g. 'Engineering', 'Academics', 'Work', 'Personal').
        priority: Optional priority filter (e.g. 'High', 'Medium', 'Low').
        status: Optional status filter (e.g. 'Pending', 'In Progress', 'Completed').

    Returns:
        A list of matching task dictionaries.
    """
    db = get_firestore_client()
    collection_ref = db.collection("tasks")
    docs = collection_ref.stream()

    results = []
    query_lower = query.lower().strip()
    cat_lower = category.lower().strip()
    prio_lower = priority.lower().strip()
    stat_lower = status.lower().strip()

    for doc in docs:
        data = doc.to_dict()
        title = data.get("title", "").lower()
        cat = data.get("category", "").lower()
        prio = data.get("priority", "").lower()
        stat = data.get("status", "").lower()
        desc = data.get("description", "").lower()
        tid = data.get("task_id", "").lower()

        matches_query = (
            not query_lower
            or query_lower in title
            or query_lower in desc
            or query_lower in tid
        )
        matches_cat = not cat_lower or cat_lower in cat
        matches_prio = not prio_lower or prio_lower in prio
        matches_stat = not stat_lower or stat_lower in stat

        if matches_query and matches_cat and matches_prio and matches_stat:
            results.append(data)

    return results


def get_task_details(task_id: str) -> dict:
    """Fetches complete details for a specific task.

    Args:
        task_id: Unique task identifier (e.g. 'task-101', 'task-102').

    Returns:
        A dictionary containing task details or an error message if not found.
    """
    db = get_firestore_client()
    doc_ref = db.collection("tasks").document(task_id.lower().strip())
    doc = doc_ref.get()

    if doc.exists:
        return doc.to_dict()
    return {"error": f"Task '{task_id}' was not found in the database."}


def add_or_update_task(
    task_id: str,
    title: str,
    category: str,
    priority: str,
    due_date: str,
    status: str,
    description: str,
) -> str:
    """Adds a new task or updates an existing task in the Firestore database.

    Args:
        task_id: Unique ID for the task (e.g. 'task-105').
        title: Clear summary title for the task.
        category: Task category ('Engineering', 'Academics', 'Work', 'Personal').
        priority: Task priority ('High', 'Medium', 'Low').
        due_date: Due date string in YYYY-MM-DD format.
        status: Task status ('Pending', 'In Progress', 'Completed').
        description: Detailed notes or actionable steps.

    Returns:
        A success message string confirming the record save.
    """
    db = get_firestore_client()
    task_data = {
        "task_id": task_id.lower().strip(),
        "title": title,
        "category": category,
        "priority": priority,
        "due_date": due_date,
        "status": status,
        "description": description,
    }
    db.collection("tasks").document(task_data["task_id"]).set(task_data)
    return f"Successfully saved task '{title}' (ID: {task_data['task_id']}) to Firestore."


def calculate_workload_and_deadlines(category: str = "") -> dict:
    """Calculates workload metrics, days remaining for each active task, and flags overdue deadlines.

    Args:
        category: Optional category filter to compute workload for a specific domain (e.g. 'Engineering', 'Academics', 'Work').

    Returns:
        A summary dictionary containing task deadline analysis, total active task counts, overdue tasks, and upcoming deadlines.
    """
    db = get_firestore_client()
    docs = db.collection("tasks").stream()

    today = datetime.date.today()
    active_tasks = []
    overdue_tasks = []
    upcoming_tasks = []

    cat_lower = category.lower().strip()

    for doc in docs:
        task = doc.to_dict()
        if task.get("status") == "Completed":
            continue

        if cat_lower and cat_lower not in task.get("category", "").lower():
            continue

        due_date_str = task.get("due_date", "")
        days_remaining = None
        is_overdue = False

        if due_date_str:
            try:
                due_dt = datetime.datetime.strptime(due_date_str, "%Y-%m-%d").date()
                days_remaining = (due_dt - today).days
                if days_remaining < 0:
                    is_overdue = True
            except ValueError:
                pass

        task_info = {
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "priority": task.get("priority"),
            "due_date": due_date_str,
            "days_remaining": days_remaining,
            "is_overdue": is_overdue,
        }

        active_tasks.append(task_info)
        if is_overdue:
            overdue_tasks.append(task_info)
        elif days_remaining is not None and days_remaining <= 3:
            upcoming_tasks.append(task_info)

    return {
        "analysis_date": str(today),
        "total_active_tasks": len(active_tasks),
        "overdue_count": len(overdue_tasks),
        "upcoming_within_3_days": len(upcoming_tasks),
        "overdue_tasks": overdue_tasks,
        "upcoming_tasks": upcoming_tasks,
        "all_active_tasks": active_tasks,
    }


def get_upcoming_public_holidays(country_code: str = "US") -> list[dict]:
    """Fetches real upcoming public holidays for a given country code (e.g. 'US', 'GB', 'CA') using Nager.Date Public Holidays API.

    Args:
        country_code: Two-letter ISO country code (e.g. 'US', 'GB', 'CA', 'DE'). Defaults to 'US'.

    Returns:
        A list of upcoming public holiday dictionaries containing date, localName, and name.
    """
    code = country_code.upper().strip() or "US"
    url = f"https://date.nager.at/api/v3/NextPublicHolidays/{code}"

    api_key = os.getenv("PUBLIC_HOLIDAYS_API_KEY")
    headers = {"User-Agent": "TaskScheduler/1.0"}
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

            holidays = []
            for item in data[:10]:
                holidays.append(
                    {
                        "date": item.get("date"),
                        "name": item.get("name"),
                        "local_name": item.get("localName"),
                        "country_code": item.get("countryCode"),
                    }
                )
            return holidays
    except Exception as e:
        return [{"error": f"Failed to fetch public holidays for '{code}': {str(e)}"}]


def geocode_address(address: str) -> dict:
    """Converts a street address or city name into geographic coordinates (latitude and longitude) using Google Geocoding API.

    Args:
        address: The address or place name to geocode (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A dictionary containing formatted address, latitude, and longitude.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not set."}

    # Geocoding API v4 REST Endpoint
    url_v4 = f"https://geocode.googleapis.com/v4/geocode/address/{urllib.parse.quote(address)}?{urllib.parse.urlencode({'key': api_key})}"

    try:
        req = urllib.request.Request(url_v4)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("results"):
                res = data["results"][0]
                loc = res.get("location", {})
                return {
                    "address": res.get("formattedAddress"),
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                }
    except Exception:
        pass

    # Fallback to v3 endpoint
    params = urllib.parse.urlencode({"address": address, "key": api_key})
    url_v3 = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"

    try:
        req = urllib.request.Request(url_v3)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "OK" and data.get("results"):
                first_result = data["results"][0]
                loc = first_result["geometry"]["location"]
                return {
                    "address": first_result.get("formatted_address"),
                    "latitude": loc.get("lat"),
                    "longitude": loc.get("lng"),
                }
            return {"error": f"Geocoding failed with status '{data.get('status')}'"}
    except Exception as e:
        return {"error": f"Failed to execute geocoding request: {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "coffee_shop",
    radius_meters: float = 1000.0,
) -> list[dict]:
    """Finds nearby places of a given type around a geographic location using Google Places API (New).

    Args:
        latitude: Latitude coordinate of the central location.
        longitude: Longitude coordinate of the central location.
        place_type: Place type category (e.g., 'coffee_shop', 'library', 'restaurant', 'park', 'gym').
        radius_meters: Radius search distance in meters. Defaults to 1000.0 (1km).

    Returns:
        A list of key place dictionaries containing name, address, and location.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return [{"error": "GOOGLE_MAPS_API_KEY environment variable is not set."}]

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    payload = {
        "includedTypes": [place_type.lower().strip()],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": float(radius_meters),
            }
        },
    }

    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data_bytes, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode("utf-8"))

            places = []
            for place in res.get("places", []):
                display_name = place.get("displayName", {}).get("text", "Unknown")
                addr = place.get("formattedAddress", "")
                loc = place.get("location", {})
                places.append(
                    {
                        "name": display_name,
                        "address": addr,
                        "location": {
                            "latitude": loc.get("latitude"),
                            "longitude": loc.get("longitude"),
                        },
                    }
                )
            return places
    except Exception as e:
        return [{"error": f"Failed to search nearby places: {str(e)}"}]


def generate_task_badge_image(
    prompt_description: str, tool_context: ToolContext
) -> dict:
    """Generates a visual task accomplishment badge or milestone graphic using gemini-3.1-flash-lite-image model in global location.

    Saves the generated image as an artifact in Playground's Artifacts panel and uploads in-memory bytes to the public Cloud Storage bucket.

    Args:
        prompt_description: Description of the task accomplishment badge or milestone graphic to generate (e.g. 'Gold accomplishment badge for Project Proposal').
        tool_context: ADK ToolContext injected automatically by framework.

    Returns:
        A dictionary containing the generated image public GCS URL and artifact details.
    """
    genai_client = genai.Client(
        vertexai=True,
        project=FIRESTORE_PROJECT_ID,
        location="global",
    )

    image_prompt = (
        f"A vibrant, high quality task milestone badge or accomplishment icon for: {prompt_description}. "
        "Modern clean vector illustration, isolated."
    )

    try:
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=image_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        image_bytes = None
        mime_type = "image/jpeg"

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break

        if not image_bytes:
            return {"error": "Failed to extract image bytes from model response."}

        filename = f"task_badge_{uuid.uuid4().hex[:8]}.jpg"

        # 1. Save artifact for Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename, artifact=artifact_part)

        # 2. Upload in-memory image bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return {
            "status": "success",
            "filename": filename,
            "public_url": public_url,
            "message": f"Generated badge image, saved artifact '{filename}', and published to {public_url}",
        }
    except Exception as e:
        return {"error": f"Failed to generate task badge image: {str(e)}"}


def generate_task_preview_video(
    prompt_description: str, tool_context: ToolContext
) -> dict:
    """Generates a short promotional or progress video for a task item using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the generated video as an artifact in Playground's Artifacts panel and uploads in-memory bytes to the public Cloud Storage bucket.

    Args:
        prompt_description: Description of the task video scene or animation to generate (e.g. 'Short animation of completing a task item').
        tool_context: ADK ToolContext injected automatically by framework.

    Returns:
        A dictionary containing the generated video public GCS URL and artifact details.
    """
    genai_client = genai.Client(
        vertexai=True,
        project=FIRESTORE_PROJECT_ID,
        location="global",
    )

    video_prompt = (
        f"A short smooth 3D animation for task management: {prompt_description}."
    )

    try:
        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=video_prompt,
        )

        video_bytes = None
        mime_type = "video/mp4"

        if hasattr(interaction, "output_video") and interaction.output_video:
            out_v = interaction.output_video
            if hasattr(out_v, "data") and out_v.data:
                v_data = out_v.data
                if isinstance(v_data, str):
                    video_bytes = base64.b64decode(v_data)
                else:
                    video_bytes = v_data

        if not video_bytes and hasattr(interaction, "outputs") and interaction.outputs:
            for out in interaction.outputs:
                if hasattr(out, "type") and out.type == "video":
                    v_data = getattr(out, "data", None)
                    if v_data:
                        video_bytes = (
                            base64.b64decode(v_data)
                            if isinstance(v_data, str)
                            else v_data
                        )
                        break

        if not video_bytes:
            return {
                "error": "Failed to extract video bytes from gemini-omni-flash-preview response."
            }

        filename = f"task_video_{uuid.uuid4().hex[:8]}.mp4"

        # 1. Save artifact with tool_context.save_artifact for Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename, artifact=artifact_part)

        # 2. Upload in-memory video bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return {
            "status": "success",
            "filename": filename,
            "public_url": public_url,
            "message": f"Generated video, saved artifact '{filename}', and published to {public_url}",
        }
    except Exception as e:
        return {"error": f"Failed to generate task video: {str(e)}"}


def get_current_time(query: str = "sf") -> str:
    """Simulates getting current time.

    Args:
        query: Location query.

    Returns:
        Current formatted timestamp string.
    """
    tz = ZoneInfo("America/Los_Angeles")
    now = datetime.datetime.now(tz)
    return f"Current time is {now.strftime('%Y-%m-%d %H:%M:%S %Z')}"


# Build A2UI System Instruction Prompt (v0.8 Schema & Basic Catalog)
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are the Task Scheduler Agent, an intelligent personal and team task management assistant. "
        "You remember user preferences, facts, and past instructions across conversations and use them to personalize your responses. "
        "When asked about an address or converting a location to coordinates, call `geocode_address`. "
        "When asked to find nearby places, venues, coffee shops, libraries, or parks, call `geocode_address` and then `find_nearby_places`. "
        "When asked to create or generate a task completion badge, milestone icon, or visual reward, call `generate_task_badge_image`. "
        "When asked to generate or create a video or animation for a task item, call `generate_task_preview_video`. "
        "You have Python code execution enabled via AgentEngineSandboxCodeExecutor to safely run Python in a sandbox when performing mathematical or data analysis tasks. "
        "Always use your tools (`search_tasks`, `get_task_details`, `add_or_update_task`, "
        "`calculate_workload_and_deadlines`, `get_upcoming_public_holidays`, `geocode_address`, "
        "`find_nearby_places`, `generate_task_badge_image`, `generate_task_preview_video`) to fetch real data."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        vertexai=True,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=code_executor,
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        search_tasks,
        get_task_details,
        add_or_update_task,
        calculate_workload_and_deadlines,
        get_upcoming_public_holidays,
        geocode_address,
        find_nearby_places,
        generate_task_badge_image,
        generate_task_preview_video,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
