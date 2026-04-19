from __future__ import annotations

# Rarity / rolls (from Fishin-Tiffin duck_manager)
RARITY_WEIGHTS = [
    ("Common", 65),
    ("Uncommon", 20),
    ("Rare", 10),
    ("Legendary", 4),
    ("Mythic", 1),
]

RARITY_CATCH_FLAIR = {
    "Common": "A scrappy little duck waddles your way.",
    "Uncommon": "A curious duck tilts its head and joins you.",
    "Rare": "A striking duck circles once, then lands at your side.",
    "Legendary": "The skies part as a mighty duck descends!",
    "Mythic": "Reality shimmers… a mythical duck chooses you.",
}

# Shiny cosmetic chance (%)
SHINY_PROB = 1.0

# Filenames under duck_game_backend/static/game/ (no remote URLs at runtime).
BOOT_IMAGE_FILENAMES = [
    "boot1.jpg",
    "boot2.jpg",
    "boot3.jpg",
    "boot4.png",
]

DUCK_OUTCOME_WEIGHTS = [
    ("zay_proc", 1),
    ("keish_proc", 1),
    ("boot", 3),
    ("steal", 15),
    ("new_duck", 80),
]

COOLDOWN_MEAN = 30.0
COOLDOWN_STD = 120.0
COOLDOWN_MIN = 1
COOLDOWN_MAX = 5 * 60

REVENGE_WINDOW_SECONDS = 5 * 60
REVENGE_SWING_STEAL_THRESHOLD = 20

STAT_WEIGHTS = {
    "Common": [33, 22, 17, 11, 9, 8, 0, 0, 0, 0],
    "Uncommon": [13, 15, 15, 15, 15, 15, 12, 0, 0, 0],
    "Rare": [4, 5, 8, 11, 14, 16, 20, 22, 0, 0],
    "Legendary": [1, 1, 2, 3, 5, 8, 12, 18, 24, 26],
    "Mythic": [0, 0, 0, 0, 2, 5, 8, 15, 30, 40],
}

# Keish bonus batch (3 rolls × 3–7 ducks each; no cooldown between bonus rolls; last roll does not start cooldown)
KEISH_BONUS_ROLLS = 3
KEISH_BATCH_MIN = 3
KEISH_BATCH_MAX = 7
KEISH_SUCCESS_IMAGE_FILENAMES = (
    "keish_success.png",
    "keish_success1.png",
    "keish_success2.png",
)
KEISH_BATCH_CATCH_TITLE = "Keish Snags A Flock of Birds From The Sky For You!"
KEISH_PROC_DESCRIPTION = (
    "The water stills—then the **spirit of thrill** sparks along your spine. "
)
KEISH_PROC_FOOTER = (
    "No cooldown! Hit !duck again."
)

# Zay — per-round defense success (rounds 1–3). Lower = harder fight.
ZAY_DEFENSE_PROBS = (0.75, 0.50, 0.25)

ZAY_PROC_TITLE = "ZAY IS COMING FOR YOUR FLOCK"
ZAY_PROC_DESCRIPTION = (
    "Zay is trying to snatch birds from your collection. Use `!duck` to fight him off."
)
ZAY_PROC_FOOTER = (
    "He's picking targets from ducks he can actually take — stand your ground with `!duck`."
)
ZAY_MID_DEFENSE_TITLE = "Good defense! He's coming in for another attack"
ZAY_MID_DEFENSE_DESCRIPTION = (
    "You protected some of your ducks but he's still lurking, use `!duck` again to fend him off!"
)
ZAY_MID_DEFENSE_FOOTER = "You got this, `!duck` to protect your ducks again!"
ZAY_STEAL_FINALE_TITLE = "Zay stole from your flock…"
ZAY_FULL_DEFENSE_TITLE = "You drove him off!"
KEISH_PROC_TITLE = "A strike of luck! A wild Keish appears!"
