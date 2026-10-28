import os
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
origins = [x.strip() for x in os.getenv("CORS_ORIGINS","https://getthelegend.vercel.app").split(",") if x.strip()]
app = FastAPI(title="LEGEND Referral API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"ok": True, "service": "legend-referral-api"}

@app.get("/api/leaderboard")
def leaderboard(limit: int = Query(100, ge=1, le=200)):
    try:
        rows = db.table("referrals").select("referrer_telegram_id").eq("status","eligible").execute().data
        counts = {}
        for row in rows:
            uid = int(row["referrer_telegram_id"])
            counts[uid] = counts.get(uid,0)+1
        if not counts:
            return {"updated_at": datetime.now(timezone.utc).isoformat(), "entries":[]}
        people = db.table("participants").select("telegram_id,username,display_name").in_("telegram_id",list(counts)).execute().data
        names = {int(p["telegram_id"]): (p.get("username") or p.get("display_name") or "LEGEND member") for p in people}
        ranked = sorted(counts.items(), key=lambda item:(-item[1],names.get(item[0],"").casefold()))[:limit]
        return {"updated_at":datetime.now(timezone.utc).isoformat(),"entries":[
            {"rank":i,"name":names.get(uid,"LEGEND member"),"eligible_referrals":n}
            for i,(uid,n) in enumerate(ranked,1)]}
    except Exception as exc:
        raise HTTPException(503,"Leaderboard temporarily unavailable") from exc
