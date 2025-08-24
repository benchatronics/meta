from django import template
register = template.Library()

@register.filter
def eur(cents):
    try:
        cents = int(cents or 0)
    except (TypeError, ValueError):
        cents = 0
    return f"€{(cents/100):,.2f}"
