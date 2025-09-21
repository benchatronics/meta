# travel_site/templatetags/lang_extras.py
from django import template
from django.utils.translation import get_language_info

register = template.Library()

# Simple mapping from language code -> representative flag emoji.
# (You can extend this anytime. Unknowns fall back to 🇬🇧.)
FLAG_MAP = {
    # Core
    "en": "🇬🇧", "en-us": "🇺🇸",
    "fr": "🇫🇷", "es": "🇪🇸", "de": "🇩🇪", "it": "🇮🇹", "pt": "🇵🇹",
    "nl": "🇳🇱", "pl": "🇵🇱", "fi": "🇫🇮", "sv": "🇸🇪", "nb": "🇳🇴", "nn": "🇳🇴",
    "da": "🇩🇰", "is": "🇮🇸", "ga": "🇮🇪", "gd": "🏴", "cy": "🏴",
    "bg": "🇧🇬", "ro": "🇷🇴", "hu": "🇭🇺", "cs": "🇨🇿", "sk": "🇸🇰",
    "sl": "🇸🇮", "hr": "🇭🇷", "bs": "🇧🇦", "sr": "🇷🇸", "sr-latn": "🇷🇸",
    "mk": "🇲🇰", "sq": "🇦🇱", "el": "🇬🇷", "uk": "🇺🇦", "be": "🇧🇾", "ru": "🇷🇺",
    "eu": "🇪🇺", "gl": "🇪🇸", "ca": "🇪🇸", "eo": "🌍", "br": "🇫🇷",
    "lb": "🇱🇺", "rm": "🇨🇭", "mt": "🇲🇹",

    # ME / Central Asia
    "tr": "🇹🇷", "hy": "🇦🇲", "ka": "🇬🇪", "az": "🇦🇿",
    "he": "🇮🇱", "ar": "🇸🇦", "fa": "🇮🇷", "ur": "🇵🇰",
    "kk": "🇰🇿", "ky": "🇰🇬", "uz": "🇺🇿", "tg": "🇹🇯",
    "tk": "🇹🇲", "tt": "🇷🇺",

    # South Asia
    "hi": "🇮🇳", "bn": "🇧🇩", "pa": "🇮🇳", "gu": "🇮🇳",
    "mr": "🇮🇳", "or": "🇮🇳", "ta": "🇮🇳", "te": "🇮🇳",
    "kn": "🇮🇳", "ml": "🇮🇳", "si": "🇱🇰", "ne": "🇳🇵",
    "sd": "🇵🇰", "ks": "🇮🇳",

    # SE / East Asia
    "th": "🇹🇭", "lo": "🇱🇦", "km": "🇰🇭", "my": "🇲🇲",
    "vi": "🇻🇳", "ms": "🇲🇾", "id": "🇮🇩", "jv": "🇮🇩", "su": "🇮🇩",
    "fil": "🇵🇭", "ja": "🇯🇵", "ko": "🇰🇷",
    "zh-hans": "🇨🇳", "zh-hant": "🇹🇼", "zh": "🇨🇳",
    "bo": "🇨🇳", "dz": "🇧🇹", "mn": "🇲🇳",

    # Americas & Oceania
    "qu": "🇵🇪", "gn": "🇵🇾", "ay": "🇧🇴", "ht": "🇭🇹",
    "haw": "🇺🇸", "sm": "🇼🇸", "mi": "🇳🇿", "ty": "🇵🇫", "fj": "🇫🇯",

    # Classical / constructed
    "la": "🏛️", "ia": "🌐",
}

@register.filter
def lang_flag(code: str) -> str:
    """Return a flag emoji for a language code."""
    if not code:
        return "🇬🇧"
    c = code.lower()
    return FLAG_MAP.get(c, FLAG_MAP.get(c.split("-")[0], "🇬🇧"))

@register.filter
def safe_lang_local_name(code: str) -> str:
    """
    Return native language name; never crash.
    Falls back to English name or the code itself.
    """
    try:
        info = get_language_info(code)
        return info.get("name_local") or info.get("name") or code
    except Exception:
        return code

@register.filter
def safe_lang_en_name(code: str) -> str:
    """English display name (fallback to code)."""
    try:
        info = get_language_info(code)
        return info.get("name") or code
    except Exception:
        return code

@register.filter
def is_rtl(code: str) -> bool:
    """True if language is right-to-left."""
    try:
        return bool(get_language_info(code).get("bidi"))
    except Exception:
        return False
