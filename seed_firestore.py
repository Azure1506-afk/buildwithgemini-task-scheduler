"""Seed script for populating tasks in Firestore."""

import subprocess
import google.auth
from google.cloud import firestore
from google.oauth2.credentials import Credentials

# IMPORTANT: Hardcoded project ID as a string to ensure Firestore resolves correctly on Agent Platform
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-01-281106dd16d5"

SAMPLE_TASKS = [
    {
        "task_id": "task-101",
        "title": "Submit Final Project Proposal",
        "category": "Academics",
        "priority": "High",
        "status": "In Progress",
        "due_date": "2026-09-30",
        "description": "Draft 3-page project proposal covering architecture, API endpoints, and evaluation metrics.",
    },
    {
        "task_id": "task-102",
        "title": "Review Team Pull Requests",
        "category": "Engineering",
        "priority": "Medium",
        "status": "Pending",
        "due_date": "2026-09-28",
        "description": "Perform code review on feature/auth-middleware and feature/firestore-integration.",
    },
    {
        "task_id": "task-103",
        "title": "Schedule Q4 Team Planning Session",
        "category": "Work",
        "priority": "High",
        "status": "Pending",
        "due_date": "2026-10-02",
        "description": "Coordinate calendar availability across engineering and product design teams.",
    },
    {
        "task_id": "task-104",
        "title": "Update Database Schema & Migration",
        "category": "Engineering",
        "priority": "High",
        "status": "In Progress",
        "due_date": "2026-10-05",
        "description": "Apply index updates for tasks collection and verify query performance.",
    },
]


def get_db_client():
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
        return firestore.Client(project=FIRESTORE_PROJECT_ID)


def seed_data():
    db = get_db_client()
    collection_ref = db.collection("tasks")

    print(f"Seeding tasks to Firestore project: {FIRESTORE_PROJECT_ID}...")
    for task in SAMPLE_TASKS:
        doc_ref = collection_ref.document(task["task_id"])
        doc_ref.set(task)
        print(f"  ✓ Seeded task: {task['title']} (ID: {task['task_id']})")

    print("Firestore seeding completed successfully!")


if __name__ == "__main__":
    seed_data()
