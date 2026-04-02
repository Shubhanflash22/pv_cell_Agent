# Background Environment System

A modular, non-destructive enhancement layer that adds a dynamic environmental background to SolarInvest. The system reflects time of day and solar conditions (Apple Weather / Tesla Energy / Linear style).

## Files

| File | Purpose |
|------|---------|
| `backgroundEnvironment.js` | Engine: determines environment state, creates `#sky-environment`, injects clouds/stars/sun/meteors |
| `backgroundEnvironment.css` | Styles: gradients, animations, glassmorphism reinforcement |

## Environment States

| State | Hours | Visual |
|-------|-------|--------|
| **morning** | 6–11 | Light blue gradient, soft sun, drifting clouds |
| **afternoon** | 11–17 | Same as morning |
| **sunset** | 17–19 | Orange gradient, sun near horizon, pink-tinted clouds |
| **night** | 19–6 | Dark gradient, star field, twinkling, occasional meteor |

## Integration

The chatbot already loads these files. They are appended after `solarinvest.css` and `solarinvest.js` in `chatbot.py`:

```python
# In chatbot.py (lines ~68–75)
_bg_css = _STATIC_DIR / "backgroundEnvironment.css"
if _bg_css.exists():
    _CSS = _CSS + "\n\n" + _bg_css.read_text(encoding="utf-8")
_bg_js = _STATIC_DIR / "backgroundEnvironment.js"
if _bg_js.exists():
    _JS_HEAD = _JS_HEAD + f"\n<script>\n{_bg_js.read_text(encoding='utf-8')}\n</script>"
```

## Manual Integration (Other Entry Points)

If you use a different launcher (e.g. custom HTML):

1. Add the container (optional — the JS creates it automatically):
   ```html
   <div id="sky-environment"></div>
   ```

2. Load CSS before other app styles:
   ```html
   <link rel="stylesheet" href="static/backgroundEnvironment.css">
   ```

3. Load JS after DOM ready (or at end of body):
   ```html
   <script src="static/backgroundEnvironment.js"></script>
   ```

## Constraints

- **z-index: -1** — Background runs behind all UI
- **pointer-events: none** — No interaction
- **position: fixed** — Full viewport coverage
- UI content remains at **z-index: 10+** (unchanged)

## Performance

- Clouds: 7 max
- Stars: 90
- Meteors: 1 at a time, ~10–20s apart
- Animations use CSS transforms only
