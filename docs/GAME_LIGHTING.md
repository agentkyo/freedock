# Game lighting, CS2 and the audio output switch

## Audio output switch (the deck's Ctrl+Win+V)

The **Audio output** widget shows the default playback device; a tap moves to the next one. On the
built-in *Daily* page it replaced the deck-brightness zone (zone 2) and its knob: turn = previous /
next device, push = next. Deck brightness stays in the main window's slider.

* The name that tells devices apart goes big: a device you renamed in Windows' Sound settings
  ("JBL LED BRANCO") wins; a generic one ("Headphones") gives way to the adapter ("Astro A50 Game").
* Virtual devices are skipped: VB-CABLE, Voicemeeter, Steam Streaming, Oculus Virtual... Edit the
  widget's *Skip* field to change the list.
* The **Audio output** action can also pick a device by name, and optionally move the
  *communication* device (calls, Discord) too.
* Volume keys follow the new device at once, and the soundboard's "only me" sounds move with it.

## Game lighting

While a known game runs (the foreground one wins), Razer Chroma (keyboard, mouse, mousepad,
headset, Chroma Link) and the Ambibox / SyncLight strip follow it, in layers:

1. the game's **theme** - palette, animated background and highlighted keys;
2. **live game state** - CS2's health, ammo and C4 countdown, truck blinkers in sync with the
   dashboard, rev bars on racing pages;
3. **event effects** - kills, headshots, the bomb, round won / lost, flashbangs.

Themes: Counter-Strike 2, Euro Truck / American Truck Simulator, BeamNG.drive, Assetto Corsa / ACC,
Flight Simulator, Forza Horizon 5, EA SPORTS FC 26, Beat Saber, Rocksmith 2014, Taxi Life,
The Precinct, Bodycam, Formula Legends, Tetris Effect. They live in
`src/freedock/integrations/scenes.py` (`THEMES`): a theme is a process list, three colors, an
animation (`static breathe wave aurora embers split pulse scan`) and key highlights such as
`{"wasd": "#de9b35", "space": "#ef4444"}`.

**The strip while you play** (Settings → Integrations → Game lighting):

| Mode | What the strip does |
| --- | --- |
| Ambilight, taken only for game moments *(default)* | stays your ambilight; FreeDock borrows it for the C4 countdown, flashbangs, kills, the round end and shift lights, then gives it back |
| Always the game's theme | the theme on the strip the whole time |
| Never | ambilight only |

**Games with their own Chroma effects** (Tetris Effect; any game if you tick *Let the game's own
Chroma effects keep the keyboard*): FreeDock leaves the keyboard to the game and only adds the strip.

The *RGB lights* action has a **Game theme** operation to preview or pin a theme without the game.

## Counter-Strike 2

FreeDock uses Valve's Game State Integration. On start it writes
`game/csgo/cfg/gamestate_integration_freedock.cfg` into every CS2 install (next to Razer Cortex's
and Logitech's) and listens on `http://127.0.0.1:47812` with a random token. **Restart CS2 once**
after the first FreeDock start - the game reads these files at launch.

The *Counter-Strike 2* page (opens by itself when `cs2.exe` runs) shows the C4 timer, health, armor,
weapon + ammo, round kills, money, score, K/A/D, round phase and map; its strip zones switch the
audio output, the volume and the microphone.

| Moment | Keyboard | Strip |
| --- | --- | --- |
| alive | WASD in your team color, F-row = health, number row = ammo | ambilight |
| buy time | B glows gold | ambilight |
| under 30 HP | heartbeat in red, faster the lower you are | ambilight |
| bomb planted | blinks with the C4 beeps (they speed up), F-row = time left, number row green/red = can the CTs still defuse (kit-aware) | countdown draining toward the middle; blue defuse progress; hard strobe when it is too late |
| flashbang | white, fading with your blindness | white |
| kill / headshot | team-color flash / gold burst | the same |
| death, round lost | dark red fade | the same |
| round won, bomb defused | sweep in your team color | the same |
| bomb exploded | orange burst | the same |

GSI gives the bomb timer through `phase_countdowns`; when a game build leaves it out, FreeDock runs
the 40 s C4 clock from the moment the plant is reported. While you are dead, GSI reports the player
you spectate: personal effects (kills, flashes, death) only fire for your own Steam ID.
