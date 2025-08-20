# travel_site/settings.py
from pathlib import Path
import os
from .i18n import LANGUAGES, LANGUAGE_CODE 

# Optional but convenient:
try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()  # loads <project-root>/.env automatically
except Exception:
    pass



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



# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Core ---
SECRET_KEY = "django-insecure-2x^94vgug#2-*q6&-(bfy^s)!an^je)r(m=(*ouk#)g@62-ul0"
DEBUG = True
ALLOWED_HOSTS = [
    "10.104.22.1",
    "scamperlinks.pythonanywhere.com",
    "localhost",
    "127.0.0.1",
    "localhost:8000",
]

# --- Apps ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # your app
    "main",
    # extras
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
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "travel_site.wsgi.application"

# --- Database ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

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

TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # for collectstatic (PythonAnywhere/production)
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]  # your local assets

# WhiteNoise recommended setting (serves compressed files)
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# --- Defaults ---
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
