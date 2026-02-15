# 🥚 Egg Tracker — Complete Setup Guide

A cloud-first MVP for a small team to track egg batches, claims, and payments.

---

## Folder Structure (what goes in Replit)

```
egg-tracker/
├── .replit              ← Tells Replit how to run the app
├── requirements.txt     ← Python dependencies
├── main.py              ← FastAPI backend (all API routes)
├── static/
│   └── index.html       ← Frontend (HTML + CSS + JS, all-in-one)
└── SETUP.md             ← This file
```

---

## Step 1: Create Supabase Tables

1. Go to [supabase.com](https://supabase.com) and sign up (free tier).
2. Click **New Project** → pick a name and password → wait for it to spin up.
3. Go to **SQL Editor** (left sidebar) and run this SQL:

```sql
-- ══════════════════════════════════════════════════
-- TABLE 1: batches — each row is one delivery of eggs
-- ══════════════════════════════════════════════════
CREATE TABLE batches (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  added_by       TEXT NOT NULL,           -- who brought the eggs
  total_cartons  INTEGER NOT NULL,        -- how many cartons
  notes          TEXT DEFAULT '',         -- optional description
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════════
-- TABLE 2: claims — each row is someone claiming cartons
-- ══════════════════════════════════════════════════
CREATE TABLE claims (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id       BIGINT REFERENCES batches(id),   -- which batch
  claimed_by     TEXT NOT NULL,                    -- who is claiming
  cartons        INTEGER NOT NULL,                 -- how many cartons
  is_paid        BOOLEAN DEFAULT FALSE,            -- have they paid?
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════════
-- ENABLE PUBLIC ACCESS via Supabase Row Level Security
-- For an MVP with a small trusted team, we allow all
-- operations via the anon key. For production, add
-- proper auth and RLS policies.
-- ══════════════════════════════════════════════════
ALTER TABLE batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE claims  ENABLE ROW LEVEL SECURITY;

-- Allow all operations for anon users (MVP only!)
CREATE POLICY "Allow all on batches" ON batches
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all on claims" ON claims
  FOR ALL USING (true) WITH CHECK (true);
```

4. Click **Run** (or Ctrl+Enter). You should see "Success" for each statement.

### Find Your Supabase Credentials

- Go to **Settings → API** in Supabase.
- Copy **Project URL** (looks like `https://abcdefg.supabase.co`).
- Copy **anon / public** key (the long string under "Project API keys").

---

## Step 2: Set Up Google Chat Webhook

1. Open **Google Chat** in your browser.
2. Go to the **Space** (group chat room) where you want notifications.
3. Click the Space name at the top → **Apps & integrations** (or **Manage webhooks**).
4. Click **+ Add webhooks**.
5. Name it `Egg Tracker`, optionally add an avatar, click **Save**.
6. Copy the webhook URL (looks like `https://chat.googleapis.com/v1/spaces/...`).

> If you don't have Google Chat or want to skip notifications for now,
> that's fine — the app works without it and just prints a note to the console.

---

## Step 3: Set Up Replit

1. Go to [replit.com](https://replit.com) and sign up.
2. Click **+ Create Repl** → choose **Python** → name it `egg-tracker`.
3. Upload or paste the files from this project matching the folder structure above.

### Add Environment Variables (Secrets)

In Replit, click the **Secrets** tab (🔒 icon in left sidebar) and add:

| Key              | Value                                       |
|------------------|---------------------------------------------|
| `SUPABASE_URL`   | `https://your-project.supabase.co`          |
| `SUPABASE_KEY`   | `eyJhbGciOi...` (your anon key)             |
| `GCHAT_WEBHOOK`  | `https://chat.googleapis.com/v1/spaces/...` |

### Run the App

Click the green **Run** button. Replit will:
1. Install the packages from `requirements.txt`
2. Start the FastAPI server on port 8080
3. Show you a **Webview** panel with your app running

Your app URL will be something like: `https://egg-tracker.your-username.repl.co`

---

## Step 4: Use the App

### Via the Web Frontend

Open the Webview URL. You'll see:

1. **Add New Batch** — fill in your name, carton count, optional notes, click "Add Batch"
2. **Claim Eggs** — enter the batch ID (shown in the table), your name, cartons, click "Claim Cartons"
3. **Dashboard** — auto-refreshes to show all batches and claims
4. **Mark Paid** — click the green button next to any unpaid claim

### Via Browser / Postman (API Testing)

The base URL is your Replit URL (e.g. `https://egg-tracker.you.repl.co`).

#### List all batches
```
GET /api/batches
```

#### Add a batch
```
POST /api/batches
Content-Type: application/json

{
  "added_by": "Maria",
  "total_cartons": 10,
  "notes": "Brown eggs from the farm"
}
```

#### List all claims
```
GET /api/claims
```

#### Claim cartons
```
POST /api/claims
Content-Type: application/json

{
  "batch_id": 1,
  "claimed_by": "James",
  "cartons": 3
}
```

#### Mark a claim as paid
```
PATCH /api/claims/1/pay
```
(Replace `1` with the actual claim ID)

#### Full dashboard (batches + claims)
```
GET /api/dashboard
```

#### Interactive API docs (auto-generated by FastAPI)
```
GET /docs
```
FastAPI gives you a **Swagger UI** at `/docs` where you can test every
endpoint interactively — click "Try it out", fill in the JSON, and execute.

---

## Step 5: Test Notifications

1. Make sure `GCHAT_WEBHOOK` is set in Replit Secrets.
2. Add a new batch via the frontend or Postman.
3. Check your Google Chat space — you should see a message like:

   > 🥚 New egg batch added by Maria!
   >    Cartons: 10
   >    Notes: Brown eggs from the farm

4. Claim cartons → you'll see:

   > 📦 James claimed 3 carton(s) from batch #1

If notifications aren't working:
- Check the Replit console for error messages.
- Verify the webhook URL is correct (no extra spaces).
- Make sure the Google Chat space still has the webhook active.

---

## Tips for Iterating

### Quick Wins to Add Next

- **Remaining cartons**: Calculate `total_cartons - SUM(claimed cartons)` and
  show how many are left in each batch.
- **Who owes what**: Add a price-per-carton field and show total amounts owed.
- **Date filters**: Filter the dashboard to show only this week's activity.
- **Delete/undo**: Add a DELETE endpoint for mistakes.

### Security (Before Going Public)

The current setup uses Supabase's anon key with wide-open RLS policies.
This is fine for a trusted small team but NOT for a public app. To lock it down:

1. Add Supabase Auth (email/password or Google login).
2. Replace the RLS policies with user-specific rules.
3. Use the Supabase service_role key only on the server, never on the client.

### Performance

- The app makes two Supabase calls for the dashboard (batches + claims).
  For a small team this is instant. If you grow to thousands of records,
  add pagination with `limit` and `offset` query params.

### Deployment Beyond Replit

If Replit's free tier feels limited:
- **Railway.app** or **Render.com** — free-tier Python hosting, same setup.
- **Vercel** — great for the frontend; pair with a separate API host.
- Supabase stays the same regardless of where you host the backend.

---

## Troubleshooting

| Problem                         | Fix                                                    |
|---------------------------------|--------------------------------------------------------|
| "SUPABASE_URL not set" warning  | Add it to Replit Secrets (🔒 tab)                      |
| 401 error from Supabase         | Check that SUPABASE_KEY is the **anon** key, not empty |
| 404 on `/api/batches`           | Make sure `main.py` is in the root, not a subfolder    |
| Frontend shows but tables empty | Click Refresh — or check browser console for errors    |
| Google Chat not receiving msgs  | Verify webhook URL; check Replit console for errors     |
| "relation batches does not exist" | Run the SQL from Step 1 in Supabase SQL Editor        |
