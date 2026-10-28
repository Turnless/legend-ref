import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_python_sources_parse():
    for path in (ROOT/"bot"/"main.py", ROOT/"api"/"main.py"):
        ast.parse(path.read_text(encoding="utf-8"))

def test_schema_enforces_unique_invitee_and_no_self_referral():
    sql = (ROOT/"supabase"/"schema.sql").read_text(encoding="utf-8").lower()
    assert "invitee_telegram_id bigint not null unique" in sql
    assert "invitee_telegram_id <> referrer_telegram_id" in sql
    assert "enable row level security" in sql

def test_api_does_not_return_telegram_ids():
    source = (ROOT/"api"/"main.py").read_text(encoding="utf-8")
    assert '"telegram_id":' not in source
    assert '"eligible_referrals":n' in source
