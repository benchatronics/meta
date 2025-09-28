#!/usr/bin/env python3
"""
command :

# 1) Update/scan strings for all languages
python manage.py makemessages -a

# 2) Validate .po files (catches placeholder errors like %(mb)s, %(phone)s, etc.)
find locale -type f -name 'django.po' -exec msgfmt -c --check-format -o /dev/null {} +

# 3) Compile to .mo files (only after step 2 passes)
python manage.py compilemessages

# 4)  python auto_translate_po.py --all

# 5) optional: clear stale .mo files for Spanish
find locale/es -name "*.mo" -delete

# compile ONLY Spanish or one one just add the langauge iso
python manage.py compilemessages -l es

Auto-translate empty msgstr entries in Django .po files.

- Preserves Django-style placeholders: %(name)s, %(mb)s
- Preserves Python braces: {time}, {count}
- Preserves %s, %d
- Skips already translated/non-empty entries (unless --force)
- Handles plural forms (best-effort)
- Works on one lang or all langs under locale/*/LC_MESSAGES/django.po

Usage examples:
  python auto_translate_po.py --lang fr
  python auto_translate_po.py --lang ar --po locale/ar/LC_MESSAGES/django.po
  python auto_translate_po.py --all
  python auto_translate_po.py --langs ar,de,es,fr,ha,it,ja,ko,pt,ru,yo,zh_Hans

Tip:
  Commit/backup your locale folder first!
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import polib
from deep_translator import GoogleTranslator


# === Regex patterns we must NOT translate (protect & restore) ===
# Django/python placeholders like %(name)s, %(mb)s
RE_DJANGO_FMT = re.compile(r"%\([a-zA-Z0-9_]+\)s")
# Old-style %s, %d, %f
RE_OLD_FMT = re.compile(r"%[sdif]")
# Python format braces {time}, {count}
RE_BRACES = re.compile(r"\{[a-zA-Z0-9_]+\}")
# HTML/XML tags <b>...</b>, <br>, <strong>
RE_TAG = re.compile(r"</?[\w\-]+(?:\s+[\w\-\:]+=(?:\"[^\"]*\"|'[^']*'|[^\s>]+))*\s*/?>")
# HTML entities like &nbsp; &amp;
RE_ENTITY = re.compile(r"&[a-zA-Z]+;")
# Django template filters like {{ var|date:"Y-m-d" }}
RE_DJANGO_TMPL = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)

PROTECT_PATTERNS = [
    RE_DJANGO_TMPL,
    RE_TAG,
    RE_ENTITY,
    RE_DJANGO_FMT,
    RE_OLD_FMT,
    RE_BRACES,
]


def protect_tokens(text: str):
    """
    Replace protected parts with sentinel tokens before translation,
    return (protected_text, tokens_map) so we can restore later.
    """
    tokens_map = []
    def _replacer(m):
        idx = len(tokens_map)
        token = f"[[[[TOKEN_{idx}]]]]"
        tokens_map.append((token, m.group(0)))
        return token

    protected = text
    # Apply all regexes sequentially
    for pattern in PROTECT_PATTERNS:
        protected = pattern.sub(_replacer, protected)
    return protected, tokens_map


def restore_tokens(text: str, tokens_map):
    """
    Put protected parts back after translation.
    """
    restored = text
    for token, original in tokens_map:
        restored = restored.replace(token, original)
    return restored


def translate_text(src_text: str, src_lang: str, dest_lang: str) -> str:
    if not src_text.strip():
        return src_text
    # Protect
    protected, tokens = protect_tokens(src_text)
    # Translate (Google auto-detects OK; we supply src if helpful)
    translated = GoogleTranslator(source=src_lang, target=dest_lang).translate(protected)
    # Restore
    translated = restore_tokens(translated, tokens)
    # Minor cleanup: keep leading/trailing whitespace identical
    return preserve_whitespace(src_text, translated)


def preserve_whitespace(original: str, translated: str) -> str:
    # Keep exact leading/trailing spaces/newlines from original
    lead_ws = len(original) - len(original.lstrip())
    trail_ws = len(original) - len(original.rstrip())
    return (" " * lead_ws) + translated.strip() + (" " * trail_ws)


def process_po(po_path: Path, target_lang: str, source_lang_hint: str, force: bool, dry_run: bool):
    print(f"→ Processing: {po_path}  [lang={target_lang}]")
    po = polib.pofile(str(po_path))

    changed = 0
    total = 0

    for entry in po:
        # Skip developer/empty keys like msgid ""
        if not entry.msgid.strip():
            continue

        # Skip if already has a translation and not forcing
        if not force:
            if entry.msgstr.strip():
                continue
            # For plural entries, skip if any plural forms filled
            if entry.msgstr_plural and any(v.strip() for v in entry.msgstr_plural.values()):
                continue

        # We translate msgid (singular) and msgid_plural (if present)
        total += 1

        try:
            if entry.msgid_plural:
                # Best-effort: translate both source strings
                tr_singular = translate_text(entry.msgid, source_lang_hint, target_lang)
                tr_plural = translate_text(entry.msgid_plural, source_lang_hint, target_lang)

                # Fill plural forms that exist in file header (po metadata decides nplurals count)
                # If header says 2 forms (common), indexes usually 0,1.
                # We set index 0 to singular, index 1 to plural. If more, reuse plural for the rest.
                n_forms = max([int(k) for k in entry.msgstr_plural.keys()] + [1]) + 1 if entry.msgstr_plural else 2
                new_plural = {}
                for i in range(n_forms):
                    if i == 0:
                        new_plural[i] = tr_singular
                    else:
                        new_plural[i] = tr_plural
                entry.msgstr_plural = new_plural
            else:
                # Singular only
                entry.msgstr = translate_text(entry.msgid, source_lang_hint, target_lang)

            changed += 1

        except Exception as ex:
            print(f"   ! Translation failed for: {entry.msgid[:60]!r} … {ex}")

    if changed and not dry_run:
        # Ensure the header Language is set (optional but nice)
        # Only set if empty to not fight your existing headers
        if not po.metadata.get("Language"):
            po.metadata["Language"] = target_lang

        backup_path = po_path.with_suffix(".po.bak")
        # Backup once per run (avoid overwriting an existing .bak)
        if not backup_path.exists():
            backup_path.write_text(po.__unicode__(), encoding="utf-8")

        po.save(str(po_path))
        print(f"✓ Saved: {po_path}  (updated {changed}/{total} entries)")
    else:
        print(f"• No changes needed in: {po_path} (candidates: {total}, updated: {changed})")


def find_po_paths(root: Path, langs: list[str] | None, explicit_po: Path | None):
    if explicit_po:
        return [explicit_po]

    paths = []
    if langs:
        for lang in langs:
            p = root / lang / "LC_MESSAGES" / "django.po"
            if p.exists():
                paths.append(p)
            else:
                print(f"   ! Missing: {p}")
    else:
        # Auto-discover all locale/*/LC_MESSAGES/django.po
        for p in (root.glob("*/LC_MESSAGES/django.po")):
            paths.append(p)
    return paths


def main():
    parser = argparse.ArgumentParser(description="Auto-translate Django .po files.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--lang", help="Single target language code (e.g., fr)")
    g.add_argument("--langs", help="Comma-separated language codes (e.g., ar,de,es,fr,ha,it,ja,ko,pt,ru,yo,zh_Hans)")
    g.add_argument("--all", action="store_true", help="Process all locale/*/LC_MESSAGES/django.po")

    parser.add_argument("--po", help="Explicit path to a single .po (overrides locale discovery)")
    parser.add_argument("--locale-root", default="locale", help="Root folder containing <lang>/LC_MESSAGES/django.po (default: locale)")
    parser.add_argument("--src", default="en", help="Source language hint (default: en)")
    parser.add_argument("--force", action="store_true", help="Also replace non-empty msgstr values")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes; just report")

    args = parser.parse_args()

    # Determine which languages
    langs = None
    if args.lang:
        langs = [args.lang]
    elif args.langs:
        langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    elif args.all:
        langs = None  # discovery mode

    # Resolve .po input(s)
    locale_root = Path(args.locale_root)
    explicit_po = Path(args.po) if args.po else None
    po_paths = find_po_paths(locale_root, langs, explicit_po)

    if not po_paths:
        print("No .po files found. Check --locale-root / --lang / --langs / --po.")
        sys.exit(1)

    # Process each
    for po_path in po_paths:
        if explicit_po and (not args.lang and not args.langs and not args.all):
            # If the user specified --po without language flags, try to infer the language from path
            # e.g., locale/fr/LC_MESSAGES/django.po -> 'fr'
            try:
                target = po_path.relative_to(locale_root).parts[0]
            except Exception:
                target = "auto"  # fallback
        else:
            # Pull the language folder name as target, unless a single --lang used
            if args.lang:
                target = args.lang
            else:
                try:
                    target = po_path.relative_to(locale_root).parts[0]
                except Exception:
                    target = "auto"

        process_po(po_path, target, args.src, args.force, args.dry_run)


if __name__ == "__main__":
    main()
