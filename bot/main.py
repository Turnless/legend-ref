import asyncio, logging, os, secrets, string
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("legend-referrals")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROUP_ID = os.environ["TELEGRAM_GROUP_ID"]
GROUP_URL = os.getenv("TELEGRAM_GROUP_URL", "https://t.me/the_legend_mm")
HOURS = max(1, int(os.getenv("QUALIFICATION_HOURS", "24")))
db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
bot, dp = Bot(TOKEN), Dispatcher()
ALPHABET = string.ascii_uppercase + string.digits
challenges = {}

def now():
    return datetime.now(timezone.utc)

def code():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))

def valid_member(member):
    status = getattr(member, "status", "")
    return status in {"creator", "administrator", "member"} or (
        status == "restricted" and bool(getattr(member, "is_member", False))
    )

def participant(uid):
    rows = db.table("participants").select("*").eq("telegram_id", uid).limit(1).execute().data
    return rows[0] if rows else None

def register(user, incoming):
    p = participant(user.id)
    if p:
        return p
    referrer = None
    if incoming and len(incoming) == 8:
        rows = db.table("participants").select("telegram_id").eq("referral_code", incoming.upper()).limit(1).execute().data
        if rows and int(rows[0]["telegram_id"]) != user.id:
            referrer = int(rows[0]["telegram_id"])
    for _ in range(5):
        try:
            return db.table("participants").insert({
                "telegram_id": user.id, "username": user.username,
                "display_name": " ".join(filter(None, [user.first_name, user.last_name]))[:100] or "LEGEND member",
                "referral_code": code(), "referred_by": referrer,
                "human_verified": False, "membership_status": "unknown"
            }).execute().data[0]
        except Exception:
            continue
    raise RuntimeError("Participant registration failed")

def member_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Join LEGEND Telegram", url=GROUP_URL)],
        [InlineKeyboardButton(text="Verify membership", callback_data="verify")]
    ])

@dp.message(CommandStart())
async def start(m: Message):
    payload = (m.text or "").split(maxsplit=1)
    incoming = payload[1].removeprefix("ref_") if len(payload) > 1 else None
    try:
        p = register(m.from_user, incoming)
        a, b = secrets.randbelow(8)+2, secrets.randbelow(8)+2
        challenges[m.from_user.id] = (a+b, now()+timedelta(minutes=10))
        await m.answer(f"Welcome to LEGEND referrals.\nHuman check: what is {a} + {b}? Reply with the number.")
    except Exception:
        log.exception("start failed")
        await m.answer("Registration is temporarily unavailable. Please try again.")

@dp.message(Command("link"))
async def link(m: Message):
    p = participant(m.from_user.id)
    if not p or not p["human_verified"]:
        await m.answer("Use /start and complete the human check first."); return
    me = await bot.get_me()
    await m.answer(f"Your referral link:\nhttps://t.me/{me.username}?start=ref_{p['referral_code']}")

@dp.message(Command("status"))
async def status(m: Message):
    p = participant(m.from_user.id)
    if not p:
        await m.answer("Use /start to register."); return
    rows = db.table("referrals").select("id", count="exact").eq("referrer_telegram_id", m.from_user.id).eq("status", "eligible").execute()
    await m.answer(f"Membership: {p['membership_status']}\nHuman check: {'complete' if p['human_verified'] else 'pending'}\nEligible referrals: {rows.count or 0}")

@dp.message(F.text)
async def answer_challenge(m: Message):
    c = challenges.get(m.from_user.id)
    if not c: return
    if now() > c[1]:
        challenges.pop(m.from_user.id, None)
        await m.answer("Human check expired. Send /start to retry."); return
    if (m.text or "").strip() != str(c[0]):
        await m.answer("Incorrect answer. Try again or send /start."); return
    challenges.pop(m.from_user.id, None)
    db.table("participants").update({"human_verified": True, "human_verified_at": now().isoformat()}).eq("telegram_id", m.from_user.id).execute()
    await m.answer("Human check complete. Join the group, then verify membership.", reply_markup=member_keyboard())

@dp.callback_query(F.data == "verify")
async def verify(q: CallbackQuery):
    try:
        p = participant(q.from_user.id)
        if not p or not p["human_verified"]:
            await q.answer("Complete /start and the human check first.", show_alert=True); return
        member = await bot.get_chat_member(GROUP_ID, q.from_user.id)
        if not valid_member(member):
            await q.answer("Join the official group first.", show_alert=True); return
        db.table("participants").update({
            "membership_status": "member", "joined_at": now().isoformat(),
            "qualified_at": (now()+timedelta(hours=HOURS)).isoformat()
        }).eq("telegram_id", q.from_user.id).execute()
        await q.message.answer(f"Membership confirmed. Stay for {HOURS} hours to qualify. Leaving resets the timer.")
        await q.answer("Membership verified")
    except Exception:
        log.exception("membership verification failed")
        await q.answer("Could not verify. Try again shortly.", show_alert=True)

@dp.chat_member()
async def membership_event(e):
    if str(e.chat.id) != str(GROUP_ID): return
    uid = e.new_chat_member.user.id
    old, new = valid_member(e.old_chat_member), valid_member(e.new_chat_member)
    try:
        if not participant(uid): return
        if new and not old:
            db.table("participants").update({"membership_status":"member","joined_at":now().isoformat(),"qualified_at":(now()+timedelta(hours=HOURS)).isoformat()}).eq("telegram_id",uid).execute()
            db.table("referrals").update({"status":"pending","qualified_at":None}).eq("invitee_telegram_id",uid).execute()
        elif old and not new:
            db.table("participants").update({"membership_status":"left","joined_at":None,"qualified_at":None}).eq("telegram_id",uid).execute()
            db.table("referrals").update({"status":"revoked","qualified_at":None}).eq("invitee_telegram_id",uid).execute()
    except Exception:
        log.exception("membership event failed")

async def sweeper():
    while True:
        try:
            rows = db.table("participants").select("telegram_id,referred_by,qualified_at").eq("human_verified",True).eq("membership_status","member").not_.is_("qualified_at","null").lte("qualified_at",now().isoformat()).execute().data
            for p in rows:
                try:
                    if not valid_member(await bot.get_chat_member(GROUP_ID,int(p["telegram_id"]))):
                        db.table("participants").update({"membership_status":"left","joined_at":None,"qualified_at":None}).eq("telegram_id",p["telegram_id"]).execute()
                        db.table("referrals").update({"status":"revoked","qualified_at":None}).eq("invitee_telegram_id",p["telegram_id"]).execute()
                    elif p["referred_by"]:
                        db.table("referrals").upsert({"invitee_telegram_id":p["telegram_id"],"referrer_telegram_id":p["referred_by"],"status":"eligible","qualified_at":now().isoformat()},on_conflict="invitee_telegram_id").execute()
                except Exception: log.exception("qualification check failed")
        except Exception: log.exception("sweeper failed")
        await asyncio.sleep(300)

async def main():
    asyncio.create_task(sweeper())
    await dp.start_polling(bot, allowed_updates=["message","callback_query","chat_member"])
if __name__ == "__main__":
    asyncio.run(main())
