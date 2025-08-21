from django import template

register = template.Library()

@register.filter(name="add_class")
def add_class(field, css):
    """
    Usage: {{ form.nickname|add_class:"input input--lg" }}
    It keeps existing widget attrs and appends CSS classes.
    """
    attrs = field.field.widget.attrs.copy()
    existing = attrs.get("class", "")
    attrs["class"] = (existing + " " + css).strip()
    return field.as_widget(attrs=attrs)

@register.filter(name="attr")
def attr(field, arg):
    """
    Generic attribute setter.
    Usage: {{ form.avatar_url|attr:"placeholder:https://example.com/photo.jpg" }}
    """
    if ":" not in arg:
        return field
    key, val = arg.split(":", 1)
    attrs = field.field.widget.attrs.copy()
    attrs[key] = val
    return field.as_widget(attrs=attrs)
