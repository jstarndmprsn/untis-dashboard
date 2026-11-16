# Design themes

A theme changes colours, fonts, backgrounds and decoration of the display – for Christmas, the
school's colours, a summer look … – without touching the program. It's a folder with a CSS file.
raspy ships without themes; this folder is where they go.

## Make one in 5 minutes

```bash
cp -r themes/_template themes/christmas      # folder name = theme id: a–z, 0–9, "-"
```

1. Edit `themes/christmas/theme.json` (name, description, optional season).
2. Edit `themes/christmas/theme.css` – uncomment and change the values you want, delete the rest.
3. Look at it without changing any setting:
   `http://127.0.0.1:5000/heute?theme=christmas` (also `/` and `/thermal`; `?theme=standard` = without).
   On a laptop: `venv/bin/python app/server.py --demo --theme christmas`, then open `http://localhost:5000/heute`.
4. Turn it on: `./install.sh --configure` → **Theme**.

Open pages reload by themselves within a minute when you save a file of the active theme.

**Your own themes, kept across updates:** put them in `~/.config/raspy/themes/<id>/` instead.
That folder is never touched by `git pull` or the software watchdog. A theme there with the same id
as one in `themes/` wins. Themes in `themes/` are part of the project – commit them to share them
(with the watchdog they arrive like any other change, in a released version).

## theme.json

```json
{
  "name": "Weihnachten",
  "description": "Snow and red accents",
  "season": "12-01..12-26",
  "pages": ["heute", "woche", "thermal"]
}
```

| Field | Meaning |
|---|---|
| `name` | shown in the settings menu (default: the folder name) |
| `description` | one line in the menu (optional) |
| `season` | `MM-DD..MM-DD`, also across New Year (`12-20..01-06`). Only used with **Automatic**: then this theme is active during its season and the built-in design otherwise. With several themes in season, the first by folder name wins. |
| `pages` | which pages get the theme (default: all three). E.g. `["heute"]` leaves the week view and system page alone. |

`theme.json` is optional; without it the theme is called by its folder name and has no season.
Mistakes in it are shown in `./install.sh --configure` → Theme and in `journalctl -u raspy-web`.

## Settings

`THEME` in `config.env` (set it with the menu):

| Value | Meaning |
|---|---|
| empty | built-in design |
| `auto` | a theme while its `season` runs (e.g. Christmas in December), otherwise built-in |
| `<id>` | always this theme |

A theme that doesn't exist (deleted, typo) simply means the built-in design.

## What you can change

Everything that has a colour, font or rounding on the pages is a CSS variable. A theme overrides
them; `:root { … }` applies to all pages, `body.page-heute { … }`, `body.page-woche { … }` and
`body.page-thermal { … }` to one page. The built-in values are in `app/static/heute.css`, `woche.css`
and `thermal.css` (top of each file) and, commented out, in `_template/theme.css`.

| Variable | Pages | What |
|---|---|---|
| `--bg` | all | page background |
| `--bg-glow` | today, system | layers on top of `--bg`: the built-in colour glow – `none`, gradients or `url("bg.jpg") center / cover` |
| `--text`, `--muted`, `--faint` | all | text, secondary text, faint text |
| `--surface`, `--surface-line` | all | cards / cells / panels and their border |
| `--cancelled`, `--changed`, `--event` | today, week | cancelled lesson, substitution, event – keep them recognisable (red, green, blue-ish) |
| `--homework` | today, week | "homework due" symbol |
| `--font`, `--radius` | all | font family, corner rounding |
| `--surface-now`, `--divider`, `--warn` | today | running lesson, line between parallel courses, "Stand HH:MM" dot |
| `--header-bg`, `--today`, `--note`, `--dim`, `--divider`, `--error`, `--stale-bg`, `--stale-text` | week | day headers, today's day, extra text, dim text, course divider, error message, "not reachable" notice |
| `--series`, `--grid`, `--good`, `--warn`, `--crit` | system | chart lines and grid, status colours |

You can also write any other CSS (it's loaded after the page's own), but variables survive updates
of the pages much better than selectors do.

Subject colours come from Untis and calendar colours from `CALENDAR_COLORS` – themes don't change them.

## Decoration

Every page has two empty, full-screen layers while a theme is active – clicks pass through both:

- `.theme-back` – behind the content (in front of the background): corner pictures, a frame, a big watermark.
- `.theme-front` – in front of the content: garlands at the top edge, falling snow.

Give them a `background`, `::before`/`::after`, or fill them from `theme.js`. Don't cover the
timetable: decoration belongs at the edges, and anything in front should be small or see-through.
The body also gets the class `theme-<id>` if you want to style something only for one theme.

## Pictures and fonts

Put them into the theme folder and use relative paths in `theme.css` – `url("snow.png")`,
`@font-face { src: url("font.woff2") }`. Served are `.css .js .png .jpg .jpeg .gif .webp .svg .avif
.woff .woff2 .ttf .otf`, nothing else. Don't load anything from the internet (Google Fonts, CDNs):
the Pi may be offline, and it would tell those servers when your class looks at the screen.
Keep pictures small (a background ≤ 1920×1080, a few hundred KB).

## theme.js (optional)

Runs after the page's own script on every page the theme applies to. Most themes don't need it.
If you animate something: CSS animations of `transform`/`opacity`, a few dozen elements at most,
no work every frame in JavaScript – the Pi shows the page all day and should stay cool and quiet.
`document.body.classList.contains("page-heute")` tells you which page you're on.

## Checklist

- Readable from the back of the room? (contrast of `--text` and `--muted` on `--bg`/`--surface`)
- Cancelled / substitution / event still obvious at a glance?
- Today view with and without calendar (`venv/bin/python app/server.py --demo --no-calendar --theme <id>`),
  week view, system page – or limit the theme with `pages`.
- Many lessons in one day (compact mode): try `/heute?tag=2026-10-13T09:50` in demo mode.
