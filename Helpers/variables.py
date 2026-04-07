import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Environment Detection
# =============================================================================

IS_TEST_MODE = os.getenv("TEST_MODE", "").lower() in ("true", "1", "t")
ERROR_PING_USER_ID = os.getenv("ERROR_PING_USER_ID")

# =============================================================================
# Environment-Specific Config
# =============================================================================

_ENV_CONFIG = {
    "test": {
        # Guild IDs
        "TAQ_GUILD_ID": 1369134564450107412,
        "EXEC_GUILD_ID": 1364751619018850405,
        # Channel IDs
        "WELCOME_CHANNEL_ID": 1369134566509514897,
        "ANNOUNCEMENT_CHANNEL_ID": 1411438316087148634,
        "FAQ_CHANNEL_ID": 1369134566295732334,
        "GUILD_BANK_CHANNEL_ID": 1367285315236008036,
        "BOT_LOG_CHANNEL_ID": 1473531947178528859,
        "ATTENTION_CHANNEL_ID": 1367285315236008036,
        "ECO_LEARNING_CHANNEL_ID": 1367285315236008036,
        "RANK_UP_CHANNEL_ID": 1369134566295732333,
        "PROMOTION_CHANNEL_ID": 1367285315236008036,
        "TAQ_ROLES_CHANNEL_ID": 1369134566295732335,
        "BOT_COMMAND_CHANNEL_ID": 1369134566723293194,
        "RAID_COLLECTING_CHANNEL_ID": 1370900136267616339,
        "RAID_LOG_CHANNEL_ID": 1370124586036887652,
        "MEMBER_APP_CHANNEL_ID": 1367283441850122330,
        "HAMMERHEAD_APP_CHANNEL_ID": 1367283460292476991,
        "MEETING_ANNOUNCEMENT_CHANNEL_ID": 1470222786646507676,
        "MILITARY_CHANNEL_ID": 1369134566979403789,
        "TERRITORY_TRACKER_CHANNEL_ID": 1369134566979403791,
        "GLOBAL_TERR_TRACKER_CHANNEL_ID": 1457380818845434068,
        "ERROR_CHANNEL_ID": 1473531947178528859,  # testing-logs
        "GUILD_LOG_CHANNEL_ID": 1367285315236008036,
        "KICK_LIST_CHANNEL_ID": 1477598411728359505,
        "SNIPE_LOG_CHANNEL_ID": 1485450296514707527,
        # Role IDs
        "EXECUTIVE_ROLE_ID": 1364751911999373483,
        "SPEARHEAD_ROLE_ID": 1369134565335236645,
        "APP_MANAGER_ROLE_ID": 1371274399637835837,
        "EXEC_APP_MANAGER_ROLE_ID": 1371274399637835837,  # test: same as app manager
        "MANUAL_REVIEW_ROLE_ID": 1371274399637835837,
        "MILITARY_ROLE_ID": 1369134565335236646,
        "HQ_TEAM_ROLE_ID": 1486367257641619537,
        # Emoji strings (formatted Discord emoji, NOT raw IDs)
        "SHELL_EMOJI": "<:shells:1371292212729479207>",
        "ASPECT_EMOJI": "<:aspect_warrior:1371292093074640936>",
        "NOTG_EMOJI": "<:notg:1371906671747666052>",
        "TCC_EMOJI": "<:tcc:1371906703099953242>",
        "TNA_EMOJI": "<:tna:1371906714949124247>",
        "NOL_EMOJI": "<:nol:1371906726940639272>",
        # Thread / Misc
        "RATES_THREAD_ID": 1462137243194888212,
        # Vanity roles
        "VANITY_ROLE_IDS": {
            "wars": {
                "t3": 1411440289159057561,  # Great White Shark (>=120 wars in 14d)
                "t2": 1411440397581811823,  # Orca             (>=80)
                "t1": 1411441013372751912,  # Mako Shark       (>=40)
            },
            "raids": {
                "t3": 1411440340556054630,  # Megalodon     (>=80 raids in 14d)
                "t2": 1411440801476771911,  # Mosasaurus    (>=50)
                "t1": 1411440932364222464,  # Liopleurodon  (>=30)
            },
        },
    },
    "prod": {
        # Guild IDs
        "TAQ_GUILD_ID": 729147655875199017,
        "EXEC_GUILD_ID": 784795827808763904,
        # Channel IDs
        "WELCOME_CHANNEL_ID": 748900470575071293,
        "ANNOUNCEMENT_CHANNEL_ID": 729162124223447040,
        "FAQ_CHANNEL_ID": 1386413126697877626,
        "GUILD_BANK_CHANNEL_ID": 1213515243041595442,
        "BOT_LOG_CHANNEL_ID": 1473526161131704331,
        "ATTENTION_CHANNEL_ID": None,
        "ECO_LEARNING_CHANNEL_ID": None,
        "RANK_UP_CHANNEL_ID": 1220853844364234802,
        "PROMOTION_CHANNEL_ID": 1033401698695262379,
        "TAQ_ROLES_CHANNEL_ID": 752917987853467669,
        "BOT_COMMAND_CHANNEL_ID": 744092522007101480,
        "RAID_COLLECTING_CHANNEL_ID": 1280196125340602478,
        "RAID_LOG_CHANNEL_ID": 1290713041285152788,
        "MEMBER_APP_CHANNEL_ID": 889162191150931978,
        "HAMMERHEAD_APP_CHANNEL_ID": 868490210294505493,
        "MEETING_ANNOUNCEMENT_CHANNEL_ID": 868488553062092850,
        "MILITARY_CHANNEL_ID": 729162690760671244,
        "TERRITORY_TRACKER_CHANNEL_ID": 729162480000958564,
        "GLOBAL_TERR_TRACKER_CHANNEL_ID": 1454634575442743437,
        "ERROR_CHANNEL_ID": 1473526161131704331,  # logs
        "GUILD_LOG_CHANNEL_ID": 936679740385931414,
        "KICK_LIST_CHANNEL_ID": 1477349930556198954,
        "SNIPE_LOG_CHANNEL_ID": 1432204059548455062,
        # Role IDs
        "EXECUTIVE_ROLE_ID": 1192976663185719467,
        "SPEARHEAD_ROLE_ID": 857589881689210950,
        "APP_MANAGER_ROLE_ID": 870767928704921651,
        "EXEC_APP_MANAGER_ROLE_ID": 870767928704921651,  # TODO: EXEC_APP_MANAGER_ROLE_MENTION is temporarily set to ping user 170719819715313665 for testing — revert to f"<@&{EXEC_APP_MANAGER_ROLE_ID}>" when ready
        "MANUAL_REVIEW_ROLE_ID": 1469587471326249063,
        "MILITARY_ROLE_ID": 894276062693949521,
        "HQ_TEAM_ROLE_ID": 1405514713554485309,
        # Emoji strings (formatted Discord emoji, NOT raw IDs)
        "SHELL_EMOJI": "<:shells:1126608994526560306>",
        "ASPECT_EMOJI": "<:aspect_warrior:1371292000963395655>",
        "NOTG_EMOJI": "<:notg:1316539942524031017>",
        "TCC_EMOJI": "<:tcc:1316539938917060658>",
        "TNA_EMOJI": "<:tna:1316539936438222850>",
        "NOL_EMOJI": "<:nol:1316539940418621530>",
        # Thread / Misc
        "RATES_THREAD_ID": 1279379192626282579,
        # Vanity roles
        "VANITY_ROLE_IDS": {
            "wars": {
                "t3": 1401236653472743668,  # Great White Shark (>=120 wars in 14d)
                "t2": 1401236428368642243,  # Orca             (>=80)
                "t1": 1401226770069590089,  # Mako Shark       (>=40)
            },
            "raids": {
                "t3": 1401281458164990022,  # Megalodon     (>=80 raids in 14d)
                "t2": 1401281504671305850,  # Mosasaurus    (>=50)
                "t1": 1401281543699431566,  # Liopleurodon  (>=30)
            },
        },
    },
}

_cfg = _ENV_CONFIG["test" if IS_TEST_MODE else "prod"]

# =============================================================================
# Guild IDs
# =============================================================================

TAQ_GUILD_ID = _cfg["TAQ_GUILD_ID"]
EXEC_GUILD_ID = _cfg["EXEC_GUILD_ID"]
DEV_GUILD_ID = 1364751619018850405  # always the same — used for error logs

# ---- Server Buckets (DEV included only in test mode) ----
PERSONAL_TEST_GUILD_ID = 1352901131977625631  # personal test server

if IS_TEST_MODE:
    TAQ_GUILD_IDS = list(set([TAQ_GUILD_ID, DEV_GUILD_ID, PERSONAL_TEST_GUILD_ID]))
    EXEC_GUILD_IDS = list(set([EXEC_GUILD_ID, DEV_GUILD_ID, PERSONAL_TEST_GUILD_ID]))
    ALL_GUILD_IDS = list(set([TAQ_GUILD_ID, EXEC_GUILD_ID, DEV_GUILD_ID, PERSONAL_TEST_GUILD_ID]))
else:
    TAQ_GUILD_IDS = [TAQ_GUILD_ID]
    EXEC_GUILD_IDS = [EXEC_GUILD_ID]
    ALL_GUILD_IDS = list(set([TAQ_GUILD_ID, EXEC_GUILD_ID]))

# ---- Home Guild IDs (canonical list of trusted guilds for admin commands) ----
HOME_GUILD_IDS = ALL_GUILD_IDS

# ---- Public Command Allowlist (everything NOT listed here is admin-only) ----
PUBLIC_COMMANDS = {
    'online', 'profile', 'progress', 'raids',
    'worlds', 'map', 'treasury', 'lootpool',
    'snipe',
}


def is_home_guild(guild_id: int) -> bool:
    """Check if a guild ID belongs to a home (trusted) guild."""
    return guild_id in HOME_GUILD_IDS

# =============================================================================
# Channel IDs
# =============================================================================

WELCOME_CHANNEL_ID = _cfg["WELCOME_CHANNEL_ID"]
ANNOUNCEMENT_CHANNEL_ID = _cfg["ANNOUNCEMENT_CHANNEL_ID"]
FAQ_CHANNEL_ID = _cfg["FAQ_CHANNEL_ID"]
GUILD_BANK_CHANNEL_ID = _cfg["GUILD_BANK_CHANNEL_ID"]
BOT_LOG_CHANNEL_ID = _cfg["BOT_LOG_CHANNEL_ID"]
ATTENTION_CHANNEL_ID = _cfg["ATTENTION_CHANNEL_ID"]
ECO_LEARNING_CHANNEL_ID = _cfg["ECO_LEARNING_CHANNEL_ID"]
RANK_UP_CHANNEL_ID = _cfg["RANK_UP_CHANNEL_ID"]
PROMOTION_CHANNEL_ID = _cfg["PROMOTION_CHANNEL_ID"]
TAQ_ROLES_CHANNEL_ID = _cfg["TAQ_ROLES_CHANNEL_ID"]
BOT_COMMAND_CHANNEL_ID = _cfg["BOT_COMMAND_CHANNEL_ID"]
RAID_COLLECTING_CHANNEL_ID = _cfg["RAID_COLLECTING_CHANNEL_ID"]
RAID_LOG_CHANNEL_ID = _cfg["RAID_LOG_CHANNEL_ID"]
MEMBER_APP_CHANNEL_ID = _cfg["MEMBER_APP_CHANNEL_ID"]
HAMMERHEAD_APP_CHANNEL_ID = _cfg["HAMMERHEAD_APP_CHANNEL_ID"]
MEETING_ANNOUNCEMENT_CHANNEL_ID = _cfg["MEETING_ANNOUNCEMENT_CHANNEL_ID"]
MILITARY_CHANNEL_ID = _cfg["MILITARY_CHANNEL_ID"]
TERRITORY_TRACKER_CHANNEL_ID = _cfg["TERRITORY_TRACKER_CHANNEL_ID"]
GLOBAL_TERR_TRACKER_CHANNEL_ID = _cfg["GLOBAL_TERR_TRACKER_CHANNEL_ID"]
ERROR_CHANNEL_ID = _cfg["ERROR_CHANNEL_ID"]
GUILD_LOG_CHANNEL_ID = _cfg["GUILD_LOG_CHANNEL_ID"]
KICK_LIST_CHANNEL_ID = _cfg["KICK_LIST_CHANNEL_ID"]
SNIPE_LOG_CHANNEL_ID = _cfg["SNIPE_LOG_CHANNEL_ID"]
RATES_THREAD_ID = _cfg["RATES_THREAD_ID"]

# =============================================================================
# Role IDs
# =============================================================================

EXECUTIVE_ROLE_ID = _cfg["EXECUTIVE_ROLE_ID"]
SPEARHEAD_ROLE_ID = _cfg["SPEARHEAD_ROLE_ID"]
APP_MANAGER_ROLE_ID = _cfg["APP_MANAGER_ROLE_ID"]
APP_MANAGER_ROLE_MENTION = f"<@&{APP_MANAGER_ROLE_ID}>"
EXEC_APP_MANAGER_ROLE_ID = _cfg["EXEC_APP_MANAGER_ROLE_ID"]
# TODO: revert to f"<@&{EXEC_APP_MANAGER_ROLE_ID}>" after testing
EXEC_APP_MANAGER_ROLE_MENTION = "<@170719819715313665>"
MANUAL_REVIEW_ROLE_ID = _cfg["MANUAL_REVIEW_ROLE_ID"]
MILITARY_ROLE_ID = _cfg["MILITARY_ROLE_ID"]
HQ_TEAM_ROLE_ID = _cfg["HQ_TEAM_ROLE_ID"]
# Always prod — used for cross-server/DM checks that must validate against the real guild
PROD_TAQ_GUILD_ID      = _ENV_CONFIG["prod"]["TAQ_GUILD_ID"]
PROD_MILITARY_ROLE_ID  = _ENV_CONFIG["prod"]["MILITARY_ROLE_ID"]
RATES_PING_ROLE_ID = 1050233131183112255
VANITY_ROLE_IDS = _cfg["VANITY_ROLE_IDS"]

# =============================================================================
# Emoji Strings (formatted Discord emoji, NOT raw IDs)
# =============================================================================

SHELL_EMOJI = _cfg["SHELL_EMOJI"]
ASPECT_EMOJI = _cfg["ASPECT_EMOJI"]
NOTG_EMOJI = _cfg["NOTG_EMOJI"]
TCC_EMOJI = _cfg["TCC_EMOJI"]
TNA_EMOJI = _cfg["TNA_EMOJI"]
NOL_EMOJI = _cfg["NOL_EMOJI"]

# =============================================================================
# Category / Channel Names (same across environments)
# =============================================================================

APP_CATEGORY_NAME = "Guild Applications"
INVITED_CATEGORY_NAME = "Invited"
CLOSED_CATEGORY_NAME = "Closed Applications"
APP_ARCHIVE_CHANNEL_NAME = "applications-archive"

# =============================================================================
# Misc Constants
# =============================================================================

WEBSITE_URL = "http://localhost:3000" if IS_TEST_MODE else "https://the-aquarium.com"
TICKET_TOOL_BOT_ID = 557628352828014614
LEGACY_WEBHOOK_URL = os.getenv("LEGACY_WEBHOOK_URL", "")
LEGACY_MESSAGE_ID = 1135537781574205520
LOG_CHANNEL_ID = BOT_LOG_CHANNEL_ID

# =============================================================================
# Game Data
# =============================================================================

rank_map = {'recruit': '', 'recruiter': '*', 'captain': '**', 'strategist': '***', 'chief': '****', 'owner': '*****'}
class_map = {'archer': '<:bow:966079566189842482>', 'hunter': '<:bow2:966079565791363162>',
             'assassin': '<:dagger:966079565770416138>', 'ninja': '<:dagger2:966079565770424400>',
             'shaman': '<:relik:966079565833326602>', 'skyseer': '<:relik2:966079565757820978>',
             'warrior': '<:spear:966079565782986852>', 'knight': '<:spear2:966079565703282799>',
             'mage': '<:wand:966079565564887062>', 'darkwizard': '<:wand2:966079565795573780>'}


discord_rank_roles = ['Starfish', '☆Reef', 'Manatee', '★Coastal Waters', 'Piranha', 'Barracuda', '★★ Azure Ocean',
                      'Angler', '★☆☆ Blue Sea',
                      'Hammerhead', '★★☆Deep Sea', 'Sailfish', '★★★Dark Sea', 'Dolphin', 'Narwhal',
                      '★★★★Abyss Waters', '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '✫✪✫ Hydra - Leader']

discord_ranks = {
    'Starfish': {
        'in_game_rank': 'RECRUIT',
        'stars': '',
        'color': '#e8a41c',
        'image': 'starfish',
        'roles': ['Starfish', '☆Reef']
    },
    'Manatee': {
        'in_game_rank': 'RECRUITER',
        'stars': '*',
        'color': '#ffe226',
        'image': 'manatee',
        'roles': ['Manatee', '★Coastal Waters']
    },
    'Piranha': {
        'in_game_rank': 'CAPTAIN',
        'stars': '**',
        'color': '#c8ff00',
        'image': 'piranha',
        'roles': ['Piranha', '★★ Azure Ocean']
    },
    'Barracuda': {
        'in_game_rank': 'CAPTAIN',
        'stars': '**',
        'color': '#79e64a',
        'image': 'barracuda',
        'roles': ['Barracuda', '★★ Azure Ocean']
    },
    'Angler': {
        'in_game_rank': 'STRATEGIST',
        'stars': '***',
        'color': '#00e2db',
        'image': 'angler',
        'roles': ['Angler', '★☆☆ Blue Sea']
    },
    'Hammerhead': {
        'in_game_rank': 'STRATEGIST',
        'stars': '***',
        'color': '#04b0eb',
        'image': 'hammerhead',
        'roles': ['Hammerhead', '★★☆Deep Sea', '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
    },
    'Sailfish': {
        'in_game_rank': 'STRATEGIST',
        'stars': '***',
        'color': '#396aff',
        'image': 'sailfish',
        'roles': ['Sailfish', '★★★Dark Sea', '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
    },
    'Dolphin': {
        'in_game_rank': 'CHIEF',
        'stars': '****',
        'color': '#9d68ff',
        'image': 'dolphin',
        'roles': ['Dolphin', '★★★Dark Sea', '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
    },
    'Narwhal': {
        'in_game_rank': 'CHIEF',
        'stars': '****',
        'color': '#eb2279',
        'image': 'narwhal',
        'roles': ['Narwhal', '★★★★Abyss Waters', '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
    },
    'Hydra': {
        'in_game_rank': 'OWNER',
        'stars': '*****',
        'color': '#ac034c',
        'image': 'hydra',
        'roles': ['✫✪✫ Hydra - Leader', '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
    }
}

minecraft_colors = {"BLACK": (25, 25, 25),
              "GRAY": (76, 76, 76),
              "SILVER": (153, 153, 153),
              "WHITE": (255, 255, 255),
              "PINK": (242, 127, 165),
              "MAGENTA": (178, 76, 216),
              "PURPLE": (127, 63, 178),
              "BLUE": (51, 76, 178),
              "CYAN": (76, 127, 153),
              "LIGHT_BLUE": (102, 153, 216),
              "GREEN": (102, 127, 51),
              "LIME": (127, 204, 25),
              "YELLOW": (229, 229, 51),
              "ORANGE": (216, 127, 51),
              "BROWN": (102, 76, 51),
              "RED": (153, 51, 51)}

minecraft_banner_colors = {"BLACK": (20, 21, 25),
                           "GRAY": (76, 76, 76),
                           "SILVER": (142, 142, 134),
                           "WHITE": (233, 233, 233),
                           "PINK": (237, 141, 170),
                           "MAGENTA": (189, 68, 179),
                           "PURPLE": (120, 42, 172),
                           "BLUE": (53, 56, 157),
                           "CYAN": (76, 127, 153),
                           "LIGHT_BLUE": (58, 175, 217),
                           "GREEN": (84, 109, 27),
                           "LIME": (112, 185, 25),
                           "YELLOW": (248, 199, 39),
                           "ORANGE": (239, 118, 20),
                           "BROWN": (114, 71, 40),
                           "RED": (161, 38, 34)}

colours = {"0": '#000000',
           "1": '#0000AA',
           "2": '#00AA00',
           "3": '#00AAAA',
           "4": '#AA0000',
           "5": '#AA00AA',
           "6": '#FFAA00',
           "7": '#AAAAAA',
           "8": '#555555',
           "9": '#5555FF',
           "a": '#55FF55',
           "b": '#55FFFF',
           "c": '#FF5555',
           "d": '#FF55FF',
           "e": '#FFFF55',
           "f": '#FFFFFF'}

shadows = {"0": '#000000',
           "1": '#00002A',
           "2": '#002A00',
           "3": '#002A2A',
           "4": '#2A0000',
           "5": '#2A002A',
           "6": '#2A2A00',
           "7": '#2A2A2A',
           "8": '#151515',
           "9": '#15153F',
           "a": '#153F15',
           "b": '#153F3F',
           "c": '#3F1515',
           "d": '#3F153F',
           "e": '#3F3F15',
           "f": '#3F3F3F'}

wynn_ranks = {
    "champion": {"color": "#ffa214", "display": "CHAMPION"},
    "heroplus": {"color": "#bc3c7c", "display": "HERO+"},
    "hero": {"color": "#8b3f8c", "display": "HERO"},
    "vipplus": {"color": "#5a7dbf", "display": "VIP+"},
    "vip": {"color": "#44aa33", "display": "VIP"},
    "media": {"color": "#bf3399", "display": "MEDIA"},
    "admin": {"color": "#d11111", "display": "ADMIN"},
    "administrator": {"color": "#d11111", "display": "ADMIN"},
    "dev": {"color": "#d11111", "display": "DEVELOPER"},
    "web": {"color": "#d11111", "display": "WEB"},
    "owner": {"color": "#aa0000", "display": "OWNER"},
    "moderator": {"color": "#ff6a00", "display": "MODERATOR"},
    "artist": {"color": "#00aaaa", "display": "ARTIST"},
    "builder": {"color": "#00aaaa", "display": "BUILDER"},
    "cmd": {"color": "#00aaaa", "display": "CMD"},
    "gm": {"color": "#00aaaa", "display": "GM"},
    "hybrid": {"color": "#00aaaa", "display": "HYBRID"},
    "item": {"color": "#00aaaa", "display": "ITEM"},
    "music": {"color": "#00aaaa", "display": "MUSIC"},
    "qa": {"color": "#00aaaa", "display": "QA"}
}

mythics = {
    "Corkian Insulator": "insulator.png",
    "Corkian Simulator": "simulator.png",
    "Boreal": "diamond_boots.png",
    "Crusade Sabatons": "diamond_boots.png",
    "Dawnbreak": "diamond_boots.png",
    "Galleon": "diamond_boots.png",
    "Moontower": "diamond_boots.png",
    "Resurgence": "diamond_boots.png",
    "Revenant": "diamond_boots.png",
    "Slayer": "diamond_boots.png",
    "Stardew": "diamond_boots.png",
    "Warchief": "diamond_boots.png",
    "Discoverer": "diamond_chestplate.png",
    "Az": "bow.thunder3.png",
    "Divzer": "bow.thunder3.png",
    "Epoch": "bow.basicgold.png",
    "Freedom": "bow.multi3.png",
    "Grandmother": "bow.earth3.png",
    "Ignis": "bow.fire3.png",
    "Labyrinth": "bow.earth3.png",
    "Spring": "bow.water3.png",
    "Stratiformis": "bow.air3.png",
    "Absolution": "relik.fire3.png",
    "Aftershock": "relik.earth3.png",
    "Fantasia": "relik.multi3.png",
    "Hadal": "relik.water3.png",
    "Immolation": "relik.fire3.png",
    "Olympic": "relik.air3.png",
    "Resonance": "relik.basicgold.png",
    "Sunstar": "relik.thunder3.png",
    "Toxoplasmosis": "relik.earth3.png",
    "Fatal": "wand.thunder3.png",
    "Gaia": "wand.earth3.png",
    "Lament": "wand.water3.png",
    "Monster": "wand.fire3.png",
    "Pure": "wand.multi1.png",
    "Quetzalcoatl": "wand.air3.png",
    "Singularity": "wand.multi3.png",
    "Trance": "wand.fire3.png",
    "Warp": "wand.air3.png",
    "Archangel": "spear.air3.png",
    "Cataclysm": "dagger.thunder3.png",
    "Grimtrap": "dagger.earth3.png",
    "Hanafubuki": "dagger.air3.png",
    "Inferno": "dagger.fire3.png",
    "Nirvana": "dagger.water3.png",
    "Nullification": "dagger.basicgold.png",
    "Oblivion": "dagger.multi3.png",
    "Weathered": "dagger.air3.png",
    "Alkatraz": "spear.earth1.png",
    "Apocalypse": "spear.fire3.png",
    "Bloodbath": "spear.earth3.png",
    "Collapse": "spear.multi3.png",
    "Convergence": "spear.multi3.png",
    "Guardian": "spear.fire3.png",
    "Hero": "spear.air3.png",
    "Idol": "spear.water3.png",
    "Thrundacrack": "spear.thunder3.png"
}

claims = {
    "Corkus": {
        "hq": "Corkus City",
        "connections": [
            "Retrofitted Manufactory",
            "Corkus Castle",
            "Corkus City Crossroads",
            "Corkus Forest",
            "Picnic Pond"
        ],
    },
    "Sky Islands": {
        "hq": "Central Islands",
        "connections": [
            "Ahmsord",
            "Temple Island",
            "Ahmsord Outskirts",
            "Wybel Island",
            "Sky Island Ascent"
        ],
    },
    "Ragni": {
        "hq": "Nomads' Refuge",
        "connections": [
            "Farmers Settlement",
            "Ancient Waterworks",
            "Webbed Fracture",
            "Arachnid Woods",
            "Entrance to Nivla Woods"
        ],
    },
    "Canyon of the Lost": {
        "hq": "Bandit's Toll",
        "connections": [
            "Illuminant Path",
            "Canyon Walkway",
            "Wizard tower",
            "Workshop Glade"
        ],
    },
    "Detlas": {
        "hq": "Mine Base Plains",
        "connections": [
            "Mining Base Camp",
            "Abandoned Mines Entrance",
            "Essren's Hut",
            "Plains Lake",
            "Silent Road",
            "Abandoned Mines"
        ],
    },
    "Desert": {
        "hq": "Almuj",
        "connections": [
            "Gloopy Cave",
            "Ruined Villa",
            "Almuj Slums",
            "Entrance to Almuj",
            "Dusty Pit"
        ],
    },
    "Silent Expanse": {
        "hq": "Toxic Drip",
        "connections": [
            "Paths of Sludge",
            "Toxic Caves",
            "Gateway  to Nothing"
        ],
    },
    "Swamp": {
        "hq": "Bloody Trail",
        "connections": [
            "Lizardman Lake",
            "Overtaken Outpost",
            "Mangled Lake",
            "Forgotten Path",
            "Lizardman Camp",
            "Entrance to Olux"
        ],
    },
    "Kander": {
        "hq": " Cinfras Outskirts",
        "connections": [
            "Cinfras",
            "Dark Forest Village",
            "Taylor Cemetery",
            "Fungal Grove",
            "Fallen Village"
        ],
    },
    "Ocean": {
        "hq": "Nodguj Nation",
        "connections": [
            "Dujgon Nation",
            "Icy Island",
            "Santa's Hideout",
            "Mage Island",
            "Durum Barley Islet",
            "Skien's Island"
        ],
    },
    "Ragni-Detlas": {
        "hq": "Corrupted Warfront",
        "connections": [
            "Corrupted Orchard",
            "Detlas Suburbs",
            "Plains Lake",
            "Roots of Corruption",
            "Corrupted Road",
            "Akias Ruins"
        ],
    }
}

# ---------------------------------------------------------------------------
# Application format templates
# ---------------------------------------------------------------------------

APPLICATION_FORMAT_MESSAGE = (
    "\U0001f4dd **Application format:**\n"
    "IGN: \n\n"
    "Timezone (in relation to gmt):\n\n"
    "Link to stats page (wynncraft.com/stats):\n\n"
    "Age (optional):\n\n"
    "Estimated playtime per day:\n\n"
    "Do you have any previous guild experience (name of the guild, rank, reason for leaving)?\n\n"
    "Are you interested in warring? If so, do you already have experience?\n\n"
    "What do you know about TAq?\n\n"
    "What would you like to gain from joining TAq?\n\n"
    "What would you contribute to TAq?\n\n"
    "Anything else you would like to tell us?  (optional)\n\n"
    "How did you learn about TAq/reference for application: "
)
