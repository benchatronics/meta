import pymysql
pymysql.install_as_MySQLdb()

from pathlib import Path
import os
#USE_I18N = True
from .i18n import LANGUAGES, LANGUAGE_CODE



# ==== Simple Captcha: senior-friendly ====
CAPTCHA_IMAGE_SIZE = (240, 90)          # wider + taller image
CAPTCHA_FONT_SIZE = 70                  # big characters
CAPTCHA_LETTER_ROTATION = (-10, 10)     # minimal tilt (easier to read)
CAPTCHA_NOISE_FUNCTIONS = ()            # remove arcs/dots noise
CAPTCHA_BACKGROUND_COLOR = '#FFFFFF'    # white background
CAPTCHA_FOREGROUND_COLOR = '#000000'    # black text (max contrast)
CAPTCHA_CHALLENGE_FUNCT = 'captcha.helpers.random_char_challenge'
#CAPTCHA_CHALLENGE_FUNCT = 'captcha.helpers.math_challenge'
# ^ switches to easy math (e.g., "7 + 3 = ?") — easier than distorted letters

CAPTCHA_LENGTH = 4                      # keep short; math is already easy
CAPTCHA_TIMEOUT = 5                     # minutes; typical default is okay


CAPTCHA_WIDGET_TEMPLATE = 'captcha/custom_widget.html'

APPEND_SLASH = True


# Base dir of project (folder that contains manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# --- .env loader (python-dotenv) ---
# pip install python-dotenv
from dotenv import load_dotenv

ENV_PATH = BASE_DIR / ".env"

# Prefer explicit path; override=True so local .env wins in dev
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)
else:
    # Fallback: load from CWD if someone runs from a different folder
    load_dotenv(override=True)

# twillio , Now read values
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_SMS    = os.getenv("TWILIO_FROM_SMS", "")
TWILIO_REGION      = os.getenv("TWILIO_REGION", "")

DEPOSIT_WEBHOOK_SECRET = os.getenv(
    "DEPOSIT_WEBHOOK_SECRET",
    ""  # empty means webhook runs in insecure (dev) mode; set in .env for secure mode
)


#telegram channel for verification
# settings.py
SUPPORT_TELEGRAM_URL = "https://t.me/benchatronics"
TELEGRAM_VERIFY_TTL_MINUTES = 1

#trial bonus , it can be reset here for only new users
TRIAL_BONUS_ENABLED = True
TRIAL_BONUS_EUR = 300    # easy to change later (e.g., 0 to disable without removing the feature)

# fast loading
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 86400
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True


SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY is not set in environment variables")


# --- Proxy headers (Cloudflare/PythonAnywhere front proxy) ---
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

DEBUG = True

ALLOWED_HOSTS = ["scamperlinks.pythonanywhere.com","explorepiedia.com","www.explorepiedia.com"]
CSRF_TRUSTED_ORIGINS = ["https://scamperlinks.pythonanywhere.com","https://explorepiedia.com","https://www.explorepiedia.com"]

# --- Apps ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    #app name
    "main",
    # extras
    "site_tags",
    "django_countries",
    "phonenumber_field",
    "captcha",
]

# Custom user
AUTH_USER_MODEL = "main.CustomUser"

# Phone number defaults
PHONENUMBER_DEFAULT_REGION = "US"   # adjust if you like (e.g., 'NG')
PHONENUMBER_DB_FORMAT = "E164"

# reCAPTCHA (dev/test defaults)
RECAPTCHA_PUBLIC_KEY = "test_public_key"
RECAPTCHA_PRIVATE_KEY = "test_private_key"
SILENCED_SYSTEM_CHECKS = ["captcha.recaptcha_test_key_error"]


# --- Middleware ---
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    #"whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # LocaleMiddleware must be right after SessionMiddleware
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "travel_site.urls"

# settings.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": "/home/scamperlinks/metasearch/.cache",
        "TIMEOUT": 300,  # 5 minutes
    }
}


# --- Templates ---
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # e.g. templates/base.html, includes/, meta_search/
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",  # needed for set_language 'next' and dropdown
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "main.context_processors.impersonation",
            ],
        },
    },
]
 # Ensure i18n context processor is present (Option B)
TEMPLATES[0]["OPTIONS"].setdefault("context_processors", [])
if "django.template.context_processors.i18n" not in TEMPLATES[0]["OPTIONS"]["context_processors"]:
    TEMPLATES[0]["OPTIONS"]["context_processors"].append(
        "django.template.context_processors.i18n"
    )


WSGI_APPLICATION = "travel_site.wsgi.application"

# --- Database ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "scamperlinks$default",
        "USER": "scamperlinks",
        "PASSWORD": os.getenv("MYSQL_PASSWORD"),
        "HOST": "scamperlinks.mysql.pythonanywhere-services.com",
        "PORT": "3306",
        "OPTIONS": {
            "charset": "utf8mb4",
            "use_unicode": True,
            "init_command": "SET NAMES 'utf8mb4', sql_mode='STRICT_TRANS_TABLES'",
        },
        "CONN_MAX_AGE": 300,
    }
}


#DATABASES = {
#    "default": {
#        "ENGINE": "django.db.backends.sqlite3",
#        "NAME": BASE_DIR / "db.sqlite3",
#       }
#}

# --- Auth / Passwords ---
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Internationalization (i18n) ---
LANGUAGE_CODE = LANGUAGE_CODE
LANGUAGES = LANGUAGES
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = 'django_language'

TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # for collectstatic (PythonAnywhere/production)
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]  # your local assets

# WhiteNoise recommended setting (serves compressed files)
#STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

#login redirect and logout
LOGIN_REDIRECT_URL = "user_dashboard"
LOGOUT_REDIRECT_URL = "signin"






# --- Defaults ---
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"