"""
Egg Tracker MVP — FastAPI Backend + Google Chat Bot
=====================================================
Now supports TWO-WAY Google Chat:
  - Anthony adds eggs → team gets notified in Google Chat
  - Team members reply with a NUMBER to claim cartons (e.g. "3")
  - Reply with "paid" to mark your most recent unpaid claim as paid
  - All via the same Google Chat space — no website needed!

ARCHITECTURE:
  - Webhook (outgoing): sends notifications TO Google Chat
  - Chat App endpoint (incoming): receives messages FROM Google Chat
  - Both use the same Google Chat space
"""

# ──────────────────────────────────────────────
# 1. IMPORTS
# ──────────────────────────────────────────────
import os
import httpx
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ──────────────────────────────────────────────
# 2. ENVIRONMENT VARIABLES
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
# 3. FASTAPI APP
# ──────────────────────────────────────────────
app = FastAPI(title="Egg Tracker")

# ──────────────────────────────────────────────
# 4. PYDANTIC MODELS
# ──────────────────────────────────────────────

class BatchCreate(BaseModel):
    total_cartons: int
    notes: str = ""

class ClaimCreate(BaseModel):
    claimed_by: str
    cartons: int

# ──────────────────────────────────────────────
# 5. SUPABASE HELPER
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
    if method == "DELETE" or not response.text:
        return []
    return response.json()

# ──────────────────────────────────────────────
# 6. GOOGLE CHAT WEBHOOK (outgoing — send TO chat)
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
# 7. BATCH & CLAIM HELPERS
# ──────────────────────────────────────────────

async def get_remaining_cartons(batch_id, total_cartons):
    claims = await supabase_request(
        "GET", "claims",
        params={"batch_id": f"eq.{batch_id}", "select": "cartons"},
    )
    claimed = sum(c["cartons"] for c in claims)
    return total_cartons - claimed


async def adjust_batch_after_paid_delete(batch_id, cartons_to_remove):
    """
    When deleting a paid claim, reduce the batch's total_cartons
    so the remaining count stays correct. If total reaches 0,
    delete the batch entirely.
    """
    batch_list = await supabase_request(
        "GET", "batches",
        params={"id": f"eq.{batch_id}", "select": "id,total_cartons"},
    )
    if not batch_list:
        return  # batch already deleted, nothing to adjust

    new_total = batch_list[0]["total_cartons"] - cartons_to_remove
    if new_total > 0:
        await supabase_request(
            "PATCH", "batches",
            params={"id": f"eq.{batch_id}"},
            json_body={"total_cartons": new_total},
        )
    else:
        # No cartons left — delete the batch
        # First remove any remaining claims for this batch
        await supabase_request(
            "DELETE", "claims",
            params={"batch_id": f"eq.{batch_id}"},
        )
        await supabase_request(
            "DELETE", "batches",
            params={"id": f"eq.{batch_id}"},
        )


async def find_open_batch():
    batches = await supabase_request(
        "GET", "batches", params={"order": "created_at.desc"},
    )
    for batch in batches:
        remaining = await get_remaining_cartons(batch["id"], batch["total_cartons"])
        if remaining > 0:
            return batch, remaining
    return None, 0


async def cleanup_old_paid_claims():
    """
    Delete paid claims older than 1 day.
    Adjusts batch total_cartons so remaining count stays correct.
    """
    paid_claims = await supabase_request(
        "GET", "claims",
        params={"is_paid": "eq.true", "select": "id,batch_id,cartons,created_at"},
    )
    now = datetime.now(timezone.utc)
    for claim in paid_claims:
        claim_date = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
        if (now - claim_date).days >= 1:
            await adjust_batch_after_paid_delete(claim["batch_id"], claim["cartons"])
            await supabase_request("DELETE", "claims", params={"id": f"eq.{claim['id']}"})
            print(f"🗑️  Auto-deleted old paid claim #{claim['id']}")


async def cleanup_fully_claimed_batches():
    batches = await supabase_request("GET", "batches", params={"select": "*"})
    all_claims = await supabase_request("GET", "claims", params={"select": "batch_id,cartons,is_paid"})
    for batch in batches:
        batch_claims = [c for c in all_claims if c["batch_id"] == batch["id"]]
        claimed = sum(c["cartons"] for c in batch_claims)
        remaining = batch["total_cartons"] - claimed
        all_paid = all(c["is_paid"] for c in batch_claims) if batch_claims else False
        if remaining <= 0 and all_paid:
            await supabase_request("DELETE", "claims", params={"batch_id": f"eq.{batch['id']}"})
            await supabase_request("DELETE", "batches", params={"id": f"eq.{batch['id']}"})
            print(f"🗑️  Auto-deleted fully claimed & paid batch #{batch['id']}")


# ══════════════════════════════════════════════
# 8. GOOGLE CHAT BOT ENDPOINT (incoming — receive FROM chat)
# ══════════════════════════════════════════════
#
# This is the magic endpoint. Google Chat sends a POST here
# every time someone sends a message in the space (or DMs the bot).
#
# HOW IT WORKS:
#   User types "3"    → bot claims 3 cartons under their name
#   User types "paid" → bot marks their latest unpaid claim as paid
#   User types "help" → bot shows available commands
#   User types "status" → bot shows current batch + claims summary

@app.post("/chat")
async def google_chat_handler(request: Request):
    """
    Receives interaction events from Google Chat.
    Google sends JSON with the user's message and identity.
    We parse it and respond with an action.
    """
    event = await request.json()

    # Google Chat sends different event types
    event_type = event.get("type", "")

    # When bot is first added to a space, say hello
    if event_type == "ADDED_TO_SPACE":
        return {
            "text": (
                "🥚 *Egg Tracker Bot is here!*\n\n"
                "I'll notify you when Anthony adds eggs.\n"
                "Reply with:\n"
                "• A *number* (e.g. `3`) — claim that many cartons\n"
                "• `paid` — mark your latest claim as paid\n"
                "• `status` — see current batch & claims\n"
                "• `help` — show these commands"
            )
        }

    # Handle actual messages
    if event_type == "MESSAGE":
        # Extract the sender's display name and the message text
        sender_name = event.get("message", {}).get("sender", {}).get("displayName", "Unknown")
        raw_text = event.get("message", {}).get("text", "").strip()

        # If bot was @mentioned, remove the mention prefix
        # Google Chat includes "@BotName " before the actual text
        # The argumentText field gives us just the user's text without the mention
        argument_text = event.get("message", {}).get("argumentText", "").strip()
        text = argument_text if argument_text else raw_text
        text_lower = text.lower()

        # ── COMMAND: help ──
        if text_lower in ("help", "?"):
            return {
                "text": (
                    "🥚 *Egg Tracker Commands:*\n\n"
                    f"• Type a *number* (e.g. `3`) → claim 3 cartons (as {sender_name})\n"
                    "• `paid` → mark your latest unpaid claim as paid\n"
                    "• `status` → see current batch info & all claims\n"
                    "• `help` → show this message"
                )
            }

        # ── COMMAND: status ──
        if text_lower == "status":
            return await chat_status_command()

        # ── COMMAND: paid ──
        if text_lower == "paid":
            return await chat_paid_command(sender_name)

        # ── COMMAND: a number → claim cartons ──
        try:
            num_cartons = int(text)
            if num_cartons < 1:
                return {"text": "❌ Please enter a number greater than 0."}
            return await chat_claim_command(sender_name, num_cartons)
        except ValueError:
            pass

        # ── Unknown command ──
        return {
            "text": (
                f"🤔 I didn't understand \"{text}\".\n"
                "Try a *number* to claim cartons, `paid`, `status`, or `help`."
            )
        }

    # For any other event type (REMOVED_FROM_SPACE, etc.), just acknowledge
    return {}


async def chat_claim_command(sender_name: str, num_cartons: int) -> dict:
    """Handle a carton claim from Google Chat."""
    batch, remaining = await find_open_batch()

    if not batch:
        return {"text": "❌ No open batches right now. Ask Anthony to add more eggs!"}

    if num_cartons > remaining:
        return {
            "text": (
                f"❌ Only *{remaining}* carton(s) left in batch #{batch['id']}. "
                f"You asked for {num_cartons}."
            )
        }

    # Create the claim
    row = {
        "batch_id": batch["id"],
        "claimed_by": sender_name,
        "cartons": num_cartons,
        "is_paid": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await supabase_request("POST", "claims", json_body=row)

    new_remaining = remaining - num_cartons
    return {
        "text": (
            f"✅ *{sender_name}* claimed *{num_cartons}* carton(s) "
            f"from batch #{batch['id']}.\n"
            f"📦 {new_remaining} carton(s) remaining.\n"
            f"Reply `paid` when you've paid Anthony."
        )
    }


async def chat_paid_command(sender_name: str) -> dict:
    """Mark the sender's most recent unpaid claim as paid."""
    # Find unpaid claims by this person, most recent first
    claims = await supabase_request(
        "GET", "claims",
        params={
            "claimed_by": f"eq.{sender_name}",
            "is_paid": "eq.false",
            "order": "created_at.desc",
            "limit": "1",
        },
    )

    if not claims:
        return {"text": f"✅ {sender_name}, you have no unpaid claims. You're all caught up!"}

    claim = claims[0]
    claim_id = claim["id"]

    # Check age — if older than 1 day, delete instead of just marking
    claim_date = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - claim_date

    if age.days >= 1:
        await adjust_batch_after_paid_delete(claim["batch_id"], claim["cartons"])
        await supabase_request("DELETE", "claims", params={"id": f"eq.{claim_id}"})
        return {
            "text": (
                f"✅ *{sender_name}* paid for claim #{claim_id} "
                f"({claim['cartons']} cartons). Record cleaned up! 🧹"
            )
        }
    else:
        await supabase_request(
            "PATCH", "claims",
            params={"id": f"eq.{claim_id}"},
            json_body={"is_paid": True},
        )
        return {
            "text": (
                f"✅ *{sender_name}* marked claim #{claim_id} as paid "
                f"({claim['cartons']} cartons). Thanks! 💰"
            )
        }


async def chat_status_command() -> dict:
    """Return a summary of current batches and claims."""
    batches = await supabase_request("GET", "batches", params={"order": "created_at.desc"})
    claims = await supabase_request("GET", "claims", params={"order": "created_at.desc"})

    if not batches:
        return {"text": "📋 No batches yet. Waiting for Anthony to add eggs!"}

    lines = ["📋 *Egg Tracker Status:*\n"]

    for batch in batches:
        batch_claims = [c for c in claims if c["batch_id"] == batch["id"]]
        claimed = sum(c["cartons"] for c in batch_claims)
        remaining = batch["total_cartons"] - claimed
        status = f"🟢 {remaining} left" if remaining > 0 else "🔴 All claimed"

        lines.append(
            f"*Batch #{batch['id']}* — {batch['total_cartons']} cartons "
            f"({status})"
        )

        if batch_claims:
            for c in batch_claims:
                paid_icon = "✅" if c["is_paid"] else "⏳"
                lines.append(
                    f"  {paid_icon} {c['claimed_by']}: {c['cartons']} cartons"
                )
        else:
            lines.append("  No claims yet")

        lines.append("")  # blank line between batches

    return {"text": "\n".join(lines)}


# ══════════════════════════════════════════════
# 9. WEB API ENDPOINTS (for the HTML dashboard)
# ══════════════════════════════════════════════

@app.get("/api/batches")
async def list_batches():
    return await supabase_request("GET", "batches", params={"order": "created_at.desc"})


@app.post("/api/batches")
async def create_batch(body: BatchCreate):
    row = {
        "added_by": "Anthony",
        "total_cartons": body.total_cartons,
        "notes": body.notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await supabase_request("POST", "batches", json_body=row)

    # Notify Google Chat with instructions on how to claim
    msg = (
        f"🥚 *New egg batch from Anthony!*\n"
        f"📦 *{body.total_cartons} cartons* available\n"
        f"📝 {body.notes or '(no notes)'}\n\n"
        f"*To claim:* Reply with a number (e.g. `3` for 3 cartons)\n"
        f"*When paid:* Reply `paid`"
    )
    await notify_google_chat(msg)
    return result


@app.get("/api/claims")
async def list_claims():
    return await supabase_request("GET", "claims", params={"order": "created_at.desc"})


@app.get("/api/open-batch")
async def get_open_batch():
    batch, remaining = await find_open_batch()
    if not batch:
        return {"batch": None, "remaining": 0}
    return {"batch": batch, "remaining": remaining}


@app.post("/api/claims")
async def create_claim(body: ClaimCreate):
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
    claim_list = await supabase_request(
        "GET", "claims", params={"id": f"eq.{claim_id}", "select": "*"},
    )
    if not claim_list:
        raise HTTPException(404, detail="Claim not found")
    claim = claim_list[0]
    claim_date = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - claim_date
    if age.days >= 1:
        # Adjust batch total before deleting claim
        await adjust_batch_after_paid_delete(claim["batch_id"], claim["cartons"])
        await supabase_request("DELETE", "claims", params={"id": f"eq.{claim_id}"})
        return {"deleted": True, "message": f"Claim #{claim_id} paid and auto-removed."}
    else:
        result = await supabase_request(
            "PATCH", "claims",
            params={"id": f"eq.{claim_id}"},
            json_body={"is_paid": True},
        )
        return result


@app.get("/api/dashboard")
async def dashboard():
    await cleanup_old_paid_claims()
    await cleanup_fully_claimed_batches()

    batches = await supabase_request("GET", "batches", params={"order": "created_at.desc"})
    claims = await supabase_request("GET", "claims", params={"order": "created_at.desc"})

    for batch in batches:
        claimed = sum(c["cartons"] for c in claims if c["batch_id"] == batch["id"])
        batch["remaining"] = batch["total_cartons"] - claimed

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
