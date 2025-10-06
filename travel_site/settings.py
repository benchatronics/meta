# settings.py
from pathlib import Path
import os
import urllib.parse as _urlparse

# --- DB driver shim (PythonAnywhere + MySQL) ---
import pymysql
pymysql.install_as_MySQLdb()

# === Base paths ===
BASE_DIR = Path(__file__).resolve().parent.parent

# === .env loader (load early!) ===
# pip install python-dotenv
from dotenv import load_dotenv
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)
else:
    load_dotenv(override=True)

# === i18n config (avoid shadowing) ===
from .i18n import LANGUAGES as I18N_LANGUAGES, LANGUAGE_CODE as I18N_LANGUAGE_CODE

# === Core flags ===
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Allowed hosts
ALLOWED_HOSTS = [h.strip() for h in os.getenv(
    "ALLOWED_HOSTS",
    "www.travellcia.com,travellcia.com,scamperlinks.pythonanywhere.com"
).split(",") if h.strip()]

# Proxy headers (Cloudflare/PythonAnywhere)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# === Site / origins ===
PUBLIC_SITE_ORIGIN = os.getenv("PUBLIC_SITE_ORIGIN", "https://www.travellcia.com").rstrip("/")
CHAT_API_ORIGIN    = os.getenv("CHAT_API_ORIGIN", PUBLIC_SITE_ORIGIN).rstrip("/")
SITE_NAME          = os.getenv("SITE_NAME", "Travellcia")

# CSRF trusted origins (must include scheme)
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv(
    "CSRF_TRUSTED_ORIGINS",
    "https://www.travellcia.com,https://travellcia.com,https://scamperlinks.pythonanywhere.com"
).split(",") if o.strip()]

# Optional: auto-add PUBLIC_SITE_ORIGIN host to ALLOWED_HOSTS/CSRF
try:
    _parsed = _urlparse.urlparse(PUBLIC_SITE_ORIGIN)
    _host = (_parsed.netloc or "").split("@")[-1]
    if _host and _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)
    _csrf_origin = f"{_parsed.scheme}://{_host}"
    if _csrf_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_csrf_origin)
except Exception:
    pass

# Helpful for local dev if you flip DEBUG=true (moved here so CSRF_TRUSTED_ORIGINS exists)
if DEBUG:
    for h in ("127.0.0.1", "localhost"):
        if h not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(h)
    for o in ("http://127.0.0.1:8000", "http://localhost:8000"):
        if o not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(o)

# === Captcha (readable) ===
CAPTCHA_IMAGE_SIZE = (240, 90)
CAPTCHA_FONT_SIZE = 70
CAPTCHA_LETTER_ROTATION = (-10, 10)
CAPTCHA_NOISE_FUNCTIONS = ()
CAPTCHA_BACKGROUND_COLOR = "#FFFFFF"
CAPTCHA_FOREGROUND_COLOR = "#000000"
CAPTCHA_CHALLENGE_FUNCT = "captcha.helpers.random_char_challenge"
CAPTCHA_LENGTH = 4
CAPTCHA_TIMEOUT = 5  # minutes
CAPTCHA_WIDGET_TEMPLATE = "captcha/custom_widget.html"

APPEND_SLASH = True

# === Secrets ===
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY is not set in environment variables")

# === Security (conditional on DEBUG) ===
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "86400"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# Extra hardening (safe in both modes)
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CONTENT_TYPE_NOSNIFF = True

# === Apps ===
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # project apps
    "main",
    "support_app.apps.SupportAppConfig",

    # extras
    "site_tags",
    "django_countries",
    "phonenumber_field",
    "captcha",
]

# Custom user
AUTH_USER_MODEL = "main.CustomUser"

# Phone numbers
PHONENUMBER_DEFAULT_REGION = os.getenv("PHONENUMBER_DEFAULT_REGION", "US")
PHONENUMBER_DB_FORMAT = "E164"

# reCAPTCHA (dev/test defaults)
RECAPTCHA_PUBLIC_KEY = os.getenv("RECAPTCHA_PUBLIC_KEY", "test_public_key")
RECAPTCHA_PRIVATE_KEY = os.getenv("RECAPTCHA_PRIVATE_KEY", "test_private_key")
SILENCED_SYSTEM_CHECKS = ["captcha.recaptcha_test_key_error"]

# === Middleware ===
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    # "whitenoise.middleware.WhiteNoiseMiddleware",  # enable if serving static via app
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",  # must be right after SessionMiddleware
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "travel_site.urls"
WSGI_APPLICATION = "travel_site.wsgi.application"

# === Cache (single, consistent config; used by notify dedupe) ===
# For production you may switch to Redis/Memcached via env. Default: LocMem.
if os.getenv("CACHE_BACKEND", "").lower() == "filebased":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": os.getenv("CACHE_FILE_LOCATION", str(BASE_DIR / ".cache")),
            "TIMEOUT": int(os.getenv("CACHE_TIMEOUT", "300")),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "support-cache",
            "TIMEOUT": int(os.getenv("CACHE_TIMEOUT", "300")),
        }
    }

# === Templates ===
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "main.context_processors.impersonation",
                "django.template.context_processors.i18n",  # ensure i18n is present
            ],
        },
    },
]

# === Database (PythonAnywhere MySQL) ===
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("MYSQL_DB_NAME", "scamperlinks$default"),
        "USER": os.getenv("MYSQL_DB_USER", "scamperlinks"),
        "PASSWORD": os.getenv("MYSQL_PASSWORD", ""),
        "HOST": os.getenv("MYSQL_HOST", "scamperlinks.mysql.pythonanywhere-services.com"),
        "PORT": os.getenv("MYSQL_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "use_unicode": True,
            "init_command": "SET NAMES 'utf8mb4', sql_mode='STRICT_TRANS_TABLES'",
        },
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "300")),
    }
}
# (Optional sqlite fallback)
# DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# === Auth / Passwords ===
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# === Internationalization ===
LANGUAGE_CODE = I18N_LANGUAGE_CODE
LANGUAGES = I18N_LANGUAGES
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = "django_language"

TIME_ZONE = os.getenv("APP_TIMEZONE", "Europe/London")
USE_I18N = True
USE_TZ = True

# === Static / Media ===
# Safer default with leading slash
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"  # if using WhiteNoise

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# === Login redirects ===
LOGIN_REDIRECT_URL = "user_dashboard"
LOGOUT_REDIRECT_URL = "signin"

# === Email (single, env-driven config) ===
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "false").lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "support@example.com")
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", f"[{SITE_NAME}] ")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# === Support notifications ===
SUPPORT_AGENT_EMAILS = [
    # parsed from env in notifications.py if set
]
SUPPORT_NOTIFY_DEDUP_TTL = int(os.getenv("SUPPORT_NOTIFY_DEDUP_TTL", "300"))

# === Twilio (optional) ===
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_SMS    = os.getenv("TWILIO_FROM_SMS", "")
TWILIO_REGION      = os.getenv("TWILIO_REGION", "")

# === Deposits webhook (optional) ===
DEPOSIT_WEBHOOK_SECRET = os.getenv("DEPOSIT_WEBHOOK_SECRET", "")

# === Telegram verification (optional) ===
SUPPORT_TELEGRAM_URL = os.getenv("SUPPORT_TELEGRAM_URL", "https://t.me/bcts")
TELEGRAM_VERIFY_TTL_MINUTES = int(os.getenv("TELEGRAM_VERIFY_TTL_MINUTES", "1"))

# === Logging (optional but handy for email debugging) ===
if os.getenv("ENABLE_EMAIL_LOGGING", "false").lower() == "true":
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "loggers": {
            "django.core.mail": {"handlers": ["console"], "level": "INFO"},
            "support_app.notifications": {"handlers": ["console"], "level": "INFO"},
        },
    }

# === Defaults ===
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
