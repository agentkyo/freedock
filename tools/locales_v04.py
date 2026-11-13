"""Translations added in 0.4 (audio output switch, Counter-Strike 2, game lighting). Merged by gen_locales.py."""
import sys
from pathlib import Path

EXTRA4 = {
    # key: (en, pt, es)
    # ---- audio output switch
    "action.audio_output": ("Audio output (switch device)", "Saída de áudio (trocar dispositivo)",
                            "Salida de audio (cambiar dispositivo)"),
    "widget.audio_output": ("Audio output", "Saída de áudio", "Salida de audio"),
    "audio_out.next": ("Next device", "Próximo dispositivo", "Siguiente dispositivo"),
    "audio_out.prev": ("Previous device", "Dispositivo anterior", "Dispositivo anterior"),
    "audio_out.set": ("This device (by name)", "Este dispositivo (pelo nome)", "Este dispositivo (por nombre)"),
    "audio_out.none": ("No output", "Sem saída", "Sin salida"),
    "audio_out.muted": ("Muted", "Mudo", "Silenciado"),
    "audio_out.failed": ("Could not switch to {name}", "Não deu para trocar para {name}",
                         "No se pudo cambiar a {name}"),
    "field.device": ("Device name", "Nome do dispositivo", "Nombre del dispositivo"),
    "field.skip": ("Skip devices containing (comma separated)", "Pular dispositivos com (separados por vírgula)",
                   "Saltar dispositivos con (separados por comas)"),
    "field.comms": ("Also for calls (communication device)", "Também para chamadas (dispositivo de comunicação)",
                    "También para llamadas (dispositivo de comunicación)"),
    # ---- Counter-Strike 2
    "widget.cs2": ("Counter-Strike 2", "Counter-Strike 2", "Counter-Strike 2"),
    "cs2.bomb": ("C4 timer", "Timer da C4", "Temporizador de la C4"),
    "cs2.health": ("Health", "Vida", "Vida"),
    "cs2.armor": ("Armor", "Colete", "Chaleco"),
    "cs2.helmet": ("Armor + helmet", "Colete + capacete", "Chaleco + casco"),
    "cs2.money": ("Money", "Dinheiro", "Dinero"),
    "cs2.kills": ("Kills this round", "Kills no round", "Bajas en la ronda"),
    "cs2.round_kills": ("kills", "kills", "bajas"),
    "cs2.kda": ("K / A / D", "K / A / D", "K / A / D"),
    "cs2.score": ("Score", "Placar", "Marcador"),
    "cs2.weapon": ("Weapon + ammo", "Arma + munição", "Arma + munición"),
    "cs2.round": ("Round phase", "Fase do round", "Fase de la ronda"),
    "cs2.round_n": ("Round {n}", "Round {n}", "Ronda {n}"),
    "cs2.map": ("Map", "Mapa", "Mapa"),
    "cs2.waiting": ("Waiting for the game", "Esperando o jogo", "Esperando el juego"),
    "cs2.menu": ("Menu", "Menu", "Menú"),
    "cs2.dead": ("Dead", "Morto", "Muerto"),
    "cs2.defusing": ("Defusing {t}s", "Desarmando {t}s", "Desactivando {t}s"),
    "cs2.kit_ok": ("Kit: make it!", "Kit: dá tempo!", "Kit: ¡llegas!"),
    "cs2.defuse_ok": ("Time to defuse", "Dá tempo de desarmar", "Hay tiempo"),
    "cs2.no_time": ("No time!", "Sem tempo!", "¡Sin tiempo!"),
    "cs2.they_can": ("CTs can still defuse", "CTs ainda desarmam", "Los CT aún llegan"),
    "cs2.defused": ("Defused", "Desarmada", "Desactivada"),
    "cs2.exploded": ("Boom", "Explodiu", "Explotó"),
    "cs2.phase.freezetime": ("Buy time", "Compra", "Compra"),
    "cs2.phase.live": ("Live", "Valendo", "En juego"),
    "cs2.phase.over": ("Round over", "Fim do round", "Fin de ronda"),
    "cs2.phase.warmup": ("Warmup", "Aquecimento", "Calentamiento"),
    "cs2.phase.bomb": ("Bomb planted", "Bomba plantada", "Bomba plantada"),
    "cs2.phase.defuse": ("Defusing", "Desarmando", "Desactivando"),
    "cs2.installed": ("CS2 connected to FreeDock - restart CS2 if it is open",
                      "CS2 conectado ao FreeDock - reinicie o CS2 se estiver aberto",
                      "CS2 conectado a FreeDock - reinicia CS2 si está abierto"),
    # ---- game lighting
    "lights.theme": ("Game theme (preview / pin)", "Tema de jogo (prévia / fixar)", "Tema de juego (vista / fijar)"),
    "field.theme": ("Theme", "Tema", "Tema"),
    "ui.game_lights": ("Game lighting", "Iluminação dos jogos", "Iluminación de juegos"),
    "ui.game_lights_on": ("Themes and effects while you play (keyboard, mouse, headset, strip)",
                          "Temas e efeitos enquanto você joga (teclado, mouse, headset, fita)",
                          "Temas y efectos mientras juegas (teclado, ratón, auriculares, tira)"),
    "ui.game_strip": ("LED strip while playing", "Fita de LED durante o jogo", "Tira LED al jugar"),
    "ui.game_strip.events": ("Ambilight, taken only for game moments (bomb, kills, flashes)",
                             "Ambilight, só assume nos momentos do jogo (bomba, kills, flash)",
                             "Ambilight, solo en momentos del juego (bomba, bajas, flashes)"),
    "ui.game_strip.theme": ("Always the game's theme", "Sempre o tema do jogo", "Siempre el tema del juego"),
    "ui.game_strip.off": ("Never (ambilight only)", "Nunca (só ambilight)", "Nunca (solo ambilight)"),
    "ui.game_native": ("Let the game's own Chroma effects keep the keyboard",
                       "Deixar os efeitos Chroma do próprio jogo no teclado",
                       "Dejar los efectos Chroma del propio juego en el teclado"),
    "ui.game_idle_screen": ("When not playing, the keyboard, mouse and headset follow the screen (like the strip)",
                            "Fora dos jogos, teclado, mouse e headset acompanham a tela (como a fita)",
                            "Fuera de los juegos, teclado, ratón y auriculares siguen la pantalla (como la tira)"),
    # ---- Euro Truck / ATS: route advisor and compass gone, job pay and cargo damage in
    "ets2.cargo": ("Cargo damage", "Dano da carga", "Daño de la carga"),
    "ets2.income": ("Job pay", "Frete", "Pago"),
}

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from freedock.integrations.scenes import THEMES  # noqa: E402

EXTRA4.update({f"theme.{t.id}": (t.name, t.name, t.name) for t in THEMES.values()})
