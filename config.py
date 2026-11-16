import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNERS = {int(x) for x in os.environ.get("OWNER_IDS", "0").split(",") if x.strip()}
WEBAPP_URL = os.environ.get("WEBAPP_URL", "http://localhost:8080").rstrip("/")
DATA = os.environ.get("DATA_DIR", "data")
BOT_API_URL = os.environ.get("BOT_API_URL", "https://api.telegram.org").rstrip("/")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

LLM_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")

PHOTO_API_URL = os.environ.get("PHOTO_API_URL", "")
PHOTO_API_KEY = os.environ.get("PHOTO_API_KEY", "")
PHOTO_MODEL = os.environ.get("PHOTO_MODEL", "gpt-image-1")
VIDEO_API_URL = os.environ.get("VIDEO_API_URL", "")
VIDEO_API_KEY = os.environ.get("VIDEO_API_KEY", "")
SUNO_API_URL = os.environ.get("SUNO_API_URL", "")
SUNO_API_KEY = os.environ.get("SUNO_API_KEY", "")

REDIS_URL = os.environ.get("REDIS_URL", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_KEY = os.environ.get("S3_KEY", "")
S3_SECRET = os.environ.get("S3_SECRET", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "lex")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)

DAILY_BUDGET = float(os.environ.get("DAILY_BUDGET", "5"))
INIT_TTL = 3600
WORKERS = int(os.environ.get("WORKERS", "2"))
MODERATION = os.environ.get(
    "MODERATION",
    "nsfw,porn, porno, порно, эротик, наркот, расчлен, beheading, gore, underage, малолет",
)
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")

if SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.2)
    except Exception:
        pass
