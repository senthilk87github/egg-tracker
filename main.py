"""
Egg Tracker MVP — FastAPI Backend
==================================
A simple app for a small team to:
  1. Log new egg batches
  2. Claim eggs (record cartons taken)
  3. Mark claims as paid
  4. View a dashboard of all activity
  5. Notify the team via Google Chat webhook

HOW IT WORKS:
  - Supabase (free cloud Postgres) stores all data
  - FastAPI serves the API + static HTML frontend
  - Google Chat webhook sends team notifications
"""

# ──────────────────────────────────────────────
# 1. IMPORTS
# ──────────────────────────────────────────────
import os
import httpx                          # HTTP client for Supabase + Google Chat calls
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel        # Request body validation

# ──────────────────────────────────────────────
# 2. READ ENVIRONMENT VARIABLES
# ──────────────────────────────────────────────
# These MUST be set in Replit → Secrets (or .env locally).
# See SETUP.md for step-by-step instructions.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")       # e.g. https://abc123.supabase.co
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")       # anon/public key
GCHAT_WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "")     # Google Chat webhook URL

# Quick check so you get a clear error on startup, not a mystery crash later
if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️  WARNING: SUPABASE_URL and SUPABASE_KEY are not set!")
    print("   Go to Replit → Secrets and add them. See SETUP.md for help.")

# Common headers every Supabase REST call needs
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",   # tells Supabase to return the created/updated row
}

# ──────────────────────────────────────────────
# 3. CREATE THE FASTAPI APP
# ──────────────────────────────────────────────
app = FastAPI(
    title="Egg Tracker",
    description="Track egg batches, claims, and payments for a small team.",
)

# ──────────────────────────────────────────────
# 4. PYDANTIC MODELS (request body shapes)
# ──────────────────────────────────────────────
# These define what JSON the frontend must send.

class BatchCreate(BaseModel):
    """Body for POST /api/batches — add a new egg batch."""
    added_by: str          # who brought the eggs, e.g. "Maria"
    total_cartons: int     # how many cartons in this batch
    notes: str = ""        # optional note, e.g. "brown eggs from farm"

class ClaimCreate(BaseModel):
    """Body for POST /api/claims — claim cartons from a batch."""
    batch_id: int          # which batch to claim from
    claimed_by: str        # who is taking eggs, e.g. "James"
    cartons: int           # how many cartons they want

# ──────────────────────────────────────────────
# 5. HELPER: Talk to Supabase REST API
# ──────────────────────────────────────────────
# Supabase exposes a PostgREST API. We use it directly with httpx
# so there's no extra SDK to install.

async def supabase_request(method: str, table: str, params: dict = None,
                           json_body: dict = None):
    """
    Generic helper to call Supabase REST endpoints.
    
    Args:
        method:    "GET", "POST", or "PATCH"
        table:     Supabase table name, e.g. "batches"
        params:    URL query params (for filtering / ordering)
        json_body: JSON payload (for POST / PATCH)
    Returns:
        Parsed JSON response (list or dict)
    """
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method, url,
            headers=SUPABASE_HEADERS,
            params=params or {},
            json=json_body,
        )
    if response.status_code >= 400:
        # Surface Supabase error details so debugging is easy
        raise HTTPException(status_code=response.status_code,
                            detail=response.text)
    return response.json()


# ──────────────────────────────────────────────
# 6. HELPER: Send Google Chat notification
# ──────────────────────────────────────────────
async def notify_google_chat(message: str):
    """
    Posts a simple text message to a Google Chat space via webhook.
    If the webhook URL isn't configured, silently skip (no crash).
    """
    if not GCHAT_WEBHOOK:
        print("ℹ️  GCHAT_WEBHOOK not set — skipping notification.")
        return
    async with httpx.AsyncClient() as client:
        try:
            await client.post(GCHAT_WEBHOOK, json={"text": message})
        except Exception as e:
            # Don't crash the main request if Chat is unreachable
            print(f"⚠️  Google Chat notification failed: {e}")


# ══════════════════════════════════════════════
# 7. API ENDPOINTS
# ══════════════════════════════════════════════

# ── 7a. GET /api/batches — list all batches ──
@app.get("/api/batches")
async def list_batches():
    """Return every egg batch, newest first."""
    return await supabase_request(
        "GET", "batches",
        params={"order": "created_at.desc"},
    )


# ── 7b. POST /api/batches — add a new batch ──
@app.post("/api/batches")
async def create_batch(body: BatchCreate):
    """
    Insert a new egg batch into Supabase and notify the team.
    """
    row = {
        "added_by": body.added_by,
        "total_cartons": body.total_cartons,
        "notes": body.notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await supabase_request("POST", "batches", json_body=row)

    # Send a Google Chat notification so the team knows
    msg = (
        f"🥚 New egg batch added by {body.added_by}!\n"
        f"   Cartons: {body.total_cartons}\n"
        f"   Notes: {body.notes or '(none)'}"
    )
    await notify_google_chat(msg)

    return result


# ── 7c. GET /api/claims — list all claims ──
@app.get("/api/claims")
async def list_claims():
    """Return every claim, newest first."""
    return await supabase_request(
        "GET", "claims",
        params={"order": "created_at.desc"},
    )


# ── 7d. POST /api/claims — claim cartons ──
@app.post("/api/claims")
async def create_claim(body: ClaimCreate):
    """
    Record that someone is taking cartons from a batch.
    Starts as unpaid (is_paid = false).
    """
    row = {
        "batch_id": body.batch_id,
        "claimed_by": body.claimed_by,
        "cartons": body.cartons,
        "is_paid": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await supabase_request("POST", "claims", json_body=row)

    msg = (
        f"📦 {body.claimed_by} claimed {body.cartons} carton(s) "
        f"from batch #{body.batch_id}"
    )
    await notify_google_chat(msg)

    return result


# ── 7e. PATCH /api/claims/{id}/pay — mark a claim paid ──
@app.patch("/api/claims/{claim_id}/pay")
async def mark_paid(claim_id: int):
    """
    Flip is_paid to true for a specific claim.
    Uses Supabase's query-string filter:  ?id=eq.{claim_id}
    """
    result = await supabase_request(
        "PATCH", "claims",
        params={"id": f"eq.{claim_id}"},
        json_body={"is_paid": True},
    )
    if not result:
        raise HTTPException(404, detail="Claim not found")
    return result


# ── 7f. GET /api/dashboard — combined view ──
@app.get("/api/dashboard")
async def dashboard():
    """
    Returns batches + claims in one response so the frontend
    can render everything with a single fetch().
    """
    batches = await supabase_request(
        "GET", "batches", params={"order": "created_at.desc"}
    )
    claims = await supabase_request(
        "GET", "claims", params={"order": "created_at.desc"}
    )
    return {"batches": batches, "claims": claims}


# ──────────────────────────────────────────────
# 8. SERVE THE FRONTEND
# ──────────────────────────────────────────────
# Mount the "static" folder so CSS/JS/images are served automatically.
# The root URL ("/") returns index.html.

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    """Serve the main HTML page."""
    return FileResponse("static/index.html")
