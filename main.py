"""
Egg Tracker MVP — FastAPI Backend
==================================
CHANGES FROM V1:
  - "Added by" is always Anthony (hardcoded)
  - Claims auto-select the latest open batch (no batch ID needed)
  - Carton count is validated — can't claim more than what's left
  - Dashboard shows remaining cartons per batch
"""

# ──────────────────────────────────────────────
# 1. IMPORTS
# ──────────────────────────────────────────────
import os
import httpx
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ──────────────────────────────────────────────
# 2. READ ENVIRONMENT VARIABLES
# ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GCHAT_WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️  WARNING: SUPABASE_URL and SUPABASE_KEY are not set!")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# ──────────────────────────────────────────────
# 3. CREATE THE FASTAPI APP
# ──────────────────────────────────────────────
app = FastAPI(title="Egg Tracker")

# ──────────────────────────────────────────────
# 4. PYDANTIC MODELS
# ──────────────────────────────────────────────

class BatchCreate(BaseModel):
    total_cartons: int     # how many cartons in this batch
    notes: str = ""        # optional note

class ClaimCreate(BaseModel):
    claimed_by: str        # who is taking eggs
    cartons: int           # how many cartons they want
    # batch_id is auto-picked — always the latest open batch

# ──────────────────────────────────────────────
# 5. HELPER: Talk to Supabase REST API
# ──────────────────────────────────────────────

async def supabase_request(method, table, params=None, json_body=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method, url,
            headers=SUPABASE_HEADERS,
            params=params or {},
            json=json_body,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code,
                            detail=response.text)
    # DELETE returns empty body — handle gracefully
    if method == "DELETE" or not response.text:
        return []
    return response.json()

# ──────────────────────────────────────────────
# 6. HELPER: Send Google Chat notification
# ──────────────────────────────────────────────

async def notify_google_chat(message):
    if not GCHAT_WEBHOOK:
        print("ℹ️  GCHAT_WEBHOOK not set — skipping notification.")
        return
    async with httpx.AsyncClient() as client:
        try:
            await client.post(GCHAT_WEBHOOK, json={"text": message})
        except Exception as e:
            print(f"⚠️  Google Chat notification failed: {e}")

# ──────────────────────────────────────────────
# 7. HELPER: Get remaining cartons for a batch
# ──────────────────────────────────────────────

async def get_remaining_cartons(batch_id, total_cartons):
    """remaining = total - sum of all claimed cartons"""
    claims = await supabase_request(
        "GET", "claims",
        params={"batch_id": f"eq.{batch_id}", "select": "cartons"},
    )
    claimed = sum(c["cartons"] for c in claims)
    return total_cartons - claimed

# ──────────────────────────────────────────────
# 8. HELPER: Find latest batch with cartons left
# ──────────────────────────────────────────────

async def find_open_batch():
    """Returns (batch_dict, remaining) or (None, 0)."""
    batches = await supabase_request(
        "GET", "batches",
        params={"order": "created_at.desc"},
    )
    for batch in batches:
        remaining = await get_remaining_cartons(batch["id"], batch["total_cartons"])
        if remaining > 0:
            return batch, remaining
    return None, 0

# ══════════════════════════════════════════════
# 9. API ENDPOINTS
# ══════════════════════════════════════════════

@app.get("/api/batches")
async def list_batches():
    return await supabase_request("GET", "batches", params={"order": "created_at.desc"})


@app.post("/api/batches")
async def create_batch(body: BatchCreate):
    """Add a new batch — always from Anthony."""
    row = {
        "added_by": "Anthony",
        "total_cartons": body.total_cartons,
        "notes": body.notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await supabase_request("POST", "batches", json_body=row)

    msg = (
        f"🥚 New egg batch added by Anthony!\n"
        f"   Cartons: {body.total_cartons}\n"
        f"   Notes: {body.notes or '(none)'}"
    )
    await notify_google_chat(msg)
    return result


@app.get("/api/claims")
async def list_claims():
    return await supabase_request("GET", "claims", params={"order": "created_at.desc"})


@app.get("/api/open-batch")
async def get_open_batch():
    """Returns the latest batch with remaining cartons."""
    batch, remaining = await find_open_batch()
    if not batch:
        return {"batch": None, "remaining": 0}
    return {"batch": batch, "remaining": remaining}


@app.post("/api/claims")
async def create_claim(body: ClaimCreate):
    """Claim cartons — auto-picks latest open batch, validates count."""
    batch, remaining = await find_open_batch()

    if not batch:
        raise HTTPException(400, detail="No open batches with cartons available.")

    if body.cartons > remaining:
        raise HTTPException(
            400,
            detail=f"Only {remaining} carton(s) left in batch #{batch['id']}. "
                   f"You requested {body.cartons}."
        )

    row = {
        "batch_id": batch["id"],
        "claimed_by": body.claimed_by,
        "cartons": body.cartons,
        "is_paid": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await supabase_request("POST", "claims", json_body=row)

    msg = (
        f"📦 {body.claimed_by} claimed {body.cartons} carton(s) "
        f"from batch #{batch['id']} ({remaining - body.cartons} left)"
    )
    await notify_google_chat(msg)
    return result


@app.patch("/api/claims/{claim_id}/pay")
async def mark_paid(claim_id: int):
    """Mark a claim as paid. If it's older than 1 day, auto-delete it."""
    # First, get the claim to check its date
    claim_list = await supabase_request(
        "GET", "claims",
        params={"id": f"eq.{claim_id}", "select": "*"},
    )
    if not claim_list:
        raise HTTPException(404, detail="Claim not found")

    claim = claim_list[0]
    claim_date = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - claim_date

    if age.days >= 1:
        # Older than 1 day → mark paid then delete
        await supabase_request(
            "DELETE", "claims",
            params={"id": f"eq.{claim_id}"},
        )
        return {"deleted": True, "message": f"Claim #{claim_id} was paid and auto-removed (older than 1 day)."}
    else:
        # Less than 1 day old → just mark paid, keep it visible
        result = await supabase_request(
            "PATCH", "claims",
            params={"id": f"eq.{claim_id}"},
            json_body={"is_paid": True},
        )
        return result


# ──────────────────────────────────────────────
# HELPER: Clean up old paid claims (runs on every dashboard load)
# ──────────────────────────────────────────────

async def cleanup_old_paid_claims():
    """
    Auto-delete any claims that are BOTH:
      - is_paid = true
      - older than 1 day
    This keeps the dashboard clean automatically.
    """
    paid_claims = await supabase_request(
        "GET", "claims",
        params={"is_paid": "eq.true", "select": "id,created_at"},
    )
    now = datetime.now(timezone.utc)
    for claim in paid_claims:
        claim_date = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
        if (now - claim_date).days >= 1:
            await supabase_request(
                "DELETE", "claims",
                params={"id": f"eq.{claim['id']}"},
            )
            print(f"🗑️  Auto-deleted old paid claim #{claim['id']}")


async def cleanup_fully_claimed_batches():
    """
    Auto-delete any batch where ALL cartons have been claimed.
    Deletes the batch and all its claims (paid or unpaid).
    """
    batches = await supabase_request("GET", "batches", params={"select": "*"})
    all_claims = await supabase_request("GET", "claims", params={"select": "batch_id,cartons,is_paid"})

    for batch in batches:
        batch_claims = [c for c in all_claims if c["batch_id"] == batch["id"]]
        claimed = sum(c["cartons"] for c in batch_claims)
        remaining = batch["total_cartons"] - claimed

        # Only delete if 100% claimed AND all claims are paid
        all_paid = all(c["is_paid"] for c in batch_claims) if batch_claims else False

        if remaining <= 0 and all_paid:
            # Delete claims first (foreign key), then the batch
            await supabase_request(
                "DELETE", "claims",
                params={"batch_id": f"eq.{batch['id']}"},
            )
            await supabase_request(
                "DELETE", "batches",
                params={"id": f"eq.{batch['id']}"},
            )
            print(f"🗑️  Auto-deleted fully claimed & paid batch #{batch['id']}")


@app.get("/api/dashboard")
async def dashboard():
    """Batches + claims + remaining counts + open batch info."""
    # Clean up old paid claims first
    await cleanup_old_paid_claims()
    # Clean up fully claimed batches
    await cleanup_fully_claimed_batches()

    batches = await supabase_request("GET", "batches", params={"order": "created_at.desc"})
    claims = await supabase_request("GET", "claims", params={"order": "created_at.desc"})

    # Add remaining count to each batch
    for batch in batches:
        claimed = sum(c["cartons"] for c in claims if c["batch_id"] == batch["id"])
        batch["remaining"] = batch["total_cartons"] - claimed

    # Find current open batch (first with remaining > 0)
    open_batch = None
    open_remaining = 0
    for b in batches:
        if b["remaining"] > 0:
            open_batch = b
            open_remaining = b["remaining"]
            break

    return {
        "batches": batches,
        "claims": claims,
        "open_batch": open_batch,
        "open_remaining": open_remaining,
    }

# ──────────────────────────────────────────────
# 10. SERVE THE FRONTEND
# ──────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
