# travel_site/i18n.py
# One-stop robust language config (no African languages).
# Ensures every code is registered with LANG_INFO so Django never raises KeyError.

from django.conf.locale import LANG_INFO

LANGUAGE_CODE = "en"

# --- 1) Candidate list (non-African). ---
_CANDIDATES = [
    # Europe
    ("en", "English"), ("fr", "Français"), ("es", "Español"), ("de", "Deutsch"),
    ("it", "Italiano"), ("pt", "Português"), ("nl", "Nederlands"), ("da", "Dansk"),
    ("sv", "Svenska"), ("nb", "Norsk Bokmål"), ("nn", "Norsk Nynorsk"),
    ("fi", "Suomi"), ("et", "Eesti"), ("lv", "Latviešu"), ("lt", "Lietuvių"),
    ("pl", "Polski"), ("cs", "Čeština"), ("sk", "Slovenčina"), ("sl", "Slovenščina"),
    ("hu", "Magyar"), ("ro", "Română"), ("bg", "Български"), ("el", "Ελληνικά"),
    ("mk", "Македонски"), ("sq", "Shqip"), ("bs", "Bosanski"), ("hr", "Hrvatski"),
    ("sr", "Српски"), ("sr-latn", "Srpski (latinica)"),
    ("uk", "Українська"), ("be", "Беларуская"), ("ru", "Русский"),
    ("ga", "Gaeilge"), ("gd", "Gàidhlig"), ("cy", "Cymraeg"), ("br", "Brezhoneg"),
    ("eu", "Euskara"), ("gl", "Galego"), ("is", "Íslenska"),
    ("mt", "Malti"), ("lb", "Lëtzebuergesch"), ("rm", "Rumantsch"),
    ("ca", "Català"), ("eo", "Esperanto"),

    # Middle East / Central Asia / Caucasus
    ("tr", "Türkçe"), ("hy", "Հայերեն"), ("ka", "ქართული"), ("az", "Azərbaycanca"),
    ("he", "עברית"), ("ar", "العربية"), ("fa", "فارسی"), ("ur", "اردو"),
    ("kk", "Қазақ тілі"), ("ky", "Кыргызча"), ("uz", "Oʻzbekcha"),
    ("tg", "Тоҷикӣ"), ("tk", "Türkmençe"), ("tt", "Татарча"),

    # South Asia
    ("hi", "हिन्दी"), ("bn", "বাংলা"), ("pa", "ਪੰਜਾਬੀ"), ("gu", "ગુજરાતી"),
    ("mr", "मराठी"), ("or", "ଓଡ଼ିଆ"), ("ta", "தமிழ்"), ("te", "తెలుగు"),
    ("kn", "ಕನ್ನಡ"), ("ml", "മലയാളം"), ("si", "සිංහල"), ("ne", "नेपाली"),
    ("sd", "سنڌي"), ("ks", "कॉशुर / کٲشُر"),

    # SE / East Asia
    ("th", "ไทย"), ("lo", "ລາວ"), ("km", "ខ្មែរ"), ("my", "မြန်မာ"),
    ("vi", "Tiếng Việt"), ("ms", "Bahasa Melayu"), ("id", "Bahasa Indonesia"),
    ("jv", "Basa Jawa"), ("su", "Basa Sunda"), ("fil", "Filipino"),
    ("ja", "日本語"), ("ko", "한국어"),
    ("zh-hans", "简体中文"), ("zh-hant", "繁體中文"),
    ("bo", "བོད་སྐད་"), ("dz", "རྫོང་ཁ"), ("mn", "Монгол"),

    # Americas & Oceania
    ("qu", "Runasimi (Quechua)"), ("gn", "Avañe'ẽ (Guaraní)"), ("ay", "Aymar aru (Aymara)"),
    ("ht", "Kreyòl Ayisyen"), ("haw","ʻŌlelo Hawaiʻi"), ("sm", "Gagana Samoa"),
    ("mi", "Te Reo Māori"), ("ty", "Reo Tahiti"), ("fj", "Vosa Vakaviti"),

    # Constructed / Classical
    ("la", "Latina"), ("ia", "Interlingua"),
]

# --- 2) Normalize/patch ---
_RTL = {"ar", "he", "fa", "ur", "ps", "sd", "ks"}

_REPLACEMENTS = {
    "fr-fr": "fr",
    "fr_fr": "fr",
    "tl": "fil",
    "sr_latn": "sr-latn",
    "sr-latn": "sr-latn",
    "zh_hans": "zh-hans",
    "zh_Hans": "zh-hans",
    "zh_hant": "zh-hant",
    "zh_Hant": "zh-hant",
}

def _ensure_langinfo(code: str, label: str):
    if code in LANG_INFO:
        return
    LANG_INFO[code] = {
        "bidi": code in _RTL,
        "code": code,
        "name": label,
        "name_local": label,
    }

_NORMALIZED = []
for code, label in _CANDIDATES:
    code = _REPLACEMENTS.get(code, code)
    _ensure_langinfo(code, label)
    _NORMALIZED.append((code, label))

# --- 3) Final public settings Django will read ---
LANGUAGES = _NORMALIZED
