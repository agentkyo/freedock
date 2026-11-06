# Customising FreeDock

Everything you see on the deck can be changed from the editor, and everything is stored as plain
JSON in your profile (`%APPDATA%\FreeDock\profile.json` on Windows, `~/.config/freedock/profile.json`
on Linux), so a look can be shared by copying a few lines.

## Three levels

A key's look is built from three style dictionaries. Each one overrides the one before it:

| Level | Where in the editor | Profile JSON |
|---|---|---|
| **Theme** (every key of every page) | Settings → Theme | `"theme": {...}` |
| **Page** (every key of one page) | Page panel → Looks → *Page look…* | `"pages": [{"style": {...}}]` |
| **Key / strip zone** | Select the key → *More looks* | `"keys": [{"style": {...}}]` |

Leave a value empty ("Inherit") to take it from the level above. In the colour buttons, right-click
clears a colour.

## Style keys

| Key | Values | What it does |
|---|---|---|
| `bg`, `bg2` | `#rrggbb` | Background, and the second stop of a gradient |
| `grad` | `diag` `vertical` `horizontal` `radial` | Gradient direction |
| `accent` | `#rrggbb` | Rings, bars, glow, borders |
| `accent_lock` | `true` / `false` | Keep your accent even when a widget picks one (e.g. CPU turning red) |
| `fg` | `#rrggbb` | Text and icon colour |
| `icon_color` | `#rrggbb` | Icon colour only |
| `font` | any installed font family | Labels and captions |
| `font_value` | any installed font family | The big value of widgets (default: JetBrains Mono for numbers) |
| `font_scale` | `0.5` – `1.8` | Text size |
| `bold` | `true` / `false` | Bold text |
| `icon_scale` | `0.4` – `1.5` | Icon size |
| `radius` | `0` – `2.5` | Corner roundness (`0` = square) |
| `border` | `auto` `none` `accent` | Border style |
| `sheen` | `true` / `false` | The glassy light at the top |
| `image_fit` | `cover` `contain` `stretch` | How a picture fills the key |
| `image_dim` | `0` – `0.8` | Darken the picture so the text stays readable |
| `anim` | see below | Animation |
| `anim_when` | `always` `active` `idle` | Play it always, only when the key is "on", or only when it is "off" |
| `anim_speed` | `0.25` – `3` | Animation speed |
| `active_anim` | see below | Animation used while a toggle or widget is "on" (default `glow`) |
| `change` | `none` `fade` `slide` `zoom` | Transition when the content changes (default `fade`) |

### Animations

`pulse`, `breathe`, `glow`, `shimmer`, `rainbow`, `blink`, `float`, `spin`, `heartbeat`,
`gradient`, `wave`, `scan`, `alert`, `sparkle`.

Some widgets suggest an animation (the weather icon floats, the page indicator shimmers, a running
AI spins). Your own `anim` setting, even `"none"`, wins over a suggestion. Attention animations
(`alert`, e.g. "Claude finished") always play until you acknowledge them.

Animations advance at most 20 times per second per key, which keeps USB traffic low. *Settings →
General → Animations* turns them all off.

## Pictures and animated GIFs

Pick a picture with the button next to the icon. PNG, JPG, BMP, **GIF**, **WebP** and **APNG** are
supported. Animated pictures play at their own frame timing, up to 240 frames. Keys are
112 × 112 pixels and strip zones 176 × 112, so a square GIF fits a key best.

## Example: a neon theme

```json
"theme": {
  "bg": "#0a0014", "bg2": "#1a0033", "grad": "radial",
  "accent": "#ff2bd6", "fg": "#f4e9ff",
  "font": "Orbitron", "radius": 0.5, "border": "accent",
  "active_anim": "rainbow", "change": "zoom"
}
```

## Notifications from anything

The AI tiles are one use of a general mechanism. Any program can light up the deck:

```sh
freedock notify custom done -m "Backup finished"
curl "http://127.0.0.1:47810/notify?source=build&event=error&message=Tests%20failed"
```

`event` is `working`, `progress` (with `--progress 0.4`), `done`, `question`, `error` or `idle`.
`GET /state` returns the current state as JSON. Only this computer can reach the HTTP port; set it
to `0` in *Settings → Notifications & lights* to turn it off.
