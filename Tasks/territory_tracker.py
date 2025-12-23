from collections import Counter
import datetime
import discord
import json
import asyncio
import random
from typing import Dict, List, Set

import aiohttp
from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.variables import (
    spearhead_role_id,
    territory_tracker_channel,
    military_channel,
    claims,
)

# ---------- Territory Abbreviation Mapping (for snapshot compression) ----------

TERRITORY_TO_ABBREV = {
    "Abandoned Farm": "ABF",
    "Abandoned Lumberyard": "ABL",
    "Abandoned Mines": "ABM",
    "Abandoned Mines Entrance": "AME",
    "Abandoned Pass": "ABP",
    "Accursed Dunes": "ACD",
    "Aerial Descent": "ARD",
    "Ahmsord": "AHM",
    "Ahmsord Outskirts": "AHO",
    "Akias Ruins": "AKR",
    "Aldorei Cliffside Waterfalls": "ACW",
    "Aldorei River": "ALR",
    "Aldorei Springs": "ALS",
    "Aldorei Valley": "ALV",
    "Aldorei Valley Outskirts": "AVO",
    "Alekin": "ALK",
    "Almuj": "ALM",
    "Almuj Slums": "AMS",
    "Ancient Excavation": "AEX",
    "Ancient Nemract": "ANM",
    "Ancient Waterworks": "AWW",
    "Angel Refuge": "ARF",
    "Apprentice Huts": "APH",
    "Arachnid Woods": "ARW",
    "Astraulus' Tower": "AST",
    "Ava's Workshop": "AVW",
    "Avos Temple": "AVT",
    "Avos Territory": "AVY",
    "Azure Frontier": "AZF",
    "Balloon Airbase": "BAB",
    "Bandit Cave": "BCV",
    "Bandit's Toll": "BTL",
    "Bantisu Air Temple": "BAT",
    "Bantisu Approach": "BTA",
    "Barren Sands": "BRS",
    "Bear Zoo": "BRZ",
    "Big Mushroom Cave": "BMC",
    "Bizarre Passage": "BZP",
    "Black Road": "BLR",
    "Blackstring Den": "BSD",
    "Bloody Beach": "BBH",
    "Bloody Trail": "BTR",
    "Blooming Boulders": "BLB",
    "Bob's Tomb": "BOB",
    "Bremminglar": "BRM",
    "Brigand Outpost": "BOP",
    "Broken Road": "BKR",
    "Bucie Waterfall": "BCW",
    "Burning Airship": "BAS",
    "Burning Farm": "BFM",
    "Canyon Dropoff": "CDO",
    "Canyon High Path": "CHP",
    "Canyon Walkway": "CWY",
    "Caritat Mansion": "CRM",
    "Cascading Basins": "CSB",
    "Cascading Oasis": "CSO",
    "Castle Dullahan": "CDH",
    "Cathedral Harbour": "CTH",
    "Celestial Impact": "CLI",
    "Centerworld Fortress": "CWF",
    "Central Islands": "CTI",
    "Chasm Chokepoint": "CCH",
    "Chasm Overlook": "CHO",
    "Cherry Blossom Grove": "CBG",
    "Cinfras": "CIN",
    "Cinfras Outskirts": "CIO",
    "Cinfras's Small Farm": "CSF",
    "Cliffhearth Orc Camp": "COC",
    "Cliffside Passage North": "CPN",
    "Cliffside Passage South": "CPS",
    "Coastal Trail": "CTL",
    "Collapsed Bridge": "CLB",
    "Collapsed Emerald Mine": "CEM",
    "Colourful Mountaintop": "CMT",
    "Corkus Castle": "CKC",
    "Corkus City": "CC",
    "Corkus City Crossroads": "CCC",
    "Corkus City Mine": "CCM",
    "Corkus Forest": "CKF",
    "Corkus Outskirts": "CKO",
    "Corkus Sea Cove": "CSC",
    "Corrupted Orchard": "CRO",
    "Corrupted River": "CRR",
    "Corrupted Road": "CRD",
    "Corrupted Tower": "CRT",
    "Corrupted Warfront": "CWR",
    "Cosmic Fissures": "CMF",
    "Crater Descent": "CRD2",
    "Cyclospordial Hazard": "CYH",
    "Dark Forest Village": "DFV",
    "Decayed Basin": "DCB",
    "Delnar Manor": "DLM",
    "Derelict Mansion": "DRM",
    "Desolate Valley": "DSV",
    "Detlas": "DET",
    "Detlas Suburbs": "DTS",
    "Displaced Housing": "DPH",
    "Disturbed Crypt": "DBC",
    "Dodegar's Forge": "DGF",
    "Dogun Ritual Site": "DRS",
    "Dragonbone Graveyard": "DBG",
    "Dragonling Nests": "DLN",
    "Dreary Docks": "DRD",
    "Dujgon Nation": "DJN",
    "Durum Barley Islet": "DBI",
    "Durum Isles Barn": "DIB",
    "Durum Malt Islet": "DMI",
    "Durum Oat Islet": "DOI",
    "Dusty Pit": "DPT",
    "Eagle Tribe": "EGT",
    "Efilim": "EFL",
    "Efilim Crossroads": "EFC",
    "Elefolk Stomping Grounds": "ESG",
    "Elephelk Trail": "ELT",
    "Elkurn": "ELK",
    "Eltom": "ELM",
    "Emerald Trail": "EMT",
    "Enchanted River": "ECR",
    "Entamis Village": "ENV",
    "Entrance to Almuj": "ETA",
    "Entrance to Bucie": "ETB",
    "Entrance to Cinfras": "ETC",
    "Entrance to Gavel": "ETG",
    "Entrance to Kander": "ETK",
    "Entrance to Molten Heights": "EMH",
    "Entrance to Nivla Woods": "ENW",
    "Entrance to Olux": "ETO",
    "Entrance to Thesead": "ETT",
    "Essren's Hut": "ESH",
    "Evergreen Outbreak": "EGO",
    "Fading Forest": "FDF",
    "Fallen Factory": "FFY",
    "Fallen Village": "FVL",
    "Faltach Manor": "FTM",
    "Farmers Settlement": "FMS",
    "Featherfall Cliffs": "FFC",
    "Felroc Fields": "FRF",
    "Field of Life": "FOL",
    "Final Step": "FNS",
    "Fleris Cranny": "FLC",
    "Fleris Trail": "FLT",
    "Floral Peaks": "FLP",
    "Florist's Hut": "FLH",
    "Forest of Eyes": "FOE",
    "Forgotten Burrows": "FGB",
    "Forgotten Path": "FGP",
    "Forgotten Town": "FGT",
    "Founder's Statue": "FDS",
    "Fountain of Youth": "FOY",
    "Freezing Heights": "FZH",
    "Frigid Crossroads": "FCR",
    "Frosty Spikes": "FSP",
    "Frozen Fort": "FFT",
    "Frozen Homestead": "FHS",
    "Fungal Grove": "FNG",
    "Gateway to Nothing": "GTN",
    "Gelibord": "GLB",
    "Gelibord Watermill": "GWM",
    "Gert Camp": "GTC",
    "Gloopy Cave": "GLC",
    "Goblin Plains East": "GPE",
    "Goblin Plains West": "GPW",
    "Great Bridge": "GBR",
    "Grey Ruins": "GRR",
    "Guardian of the Forest": "GOF",
    "Guild Hall": "GHL",
    "Gylia Fisherman Camp": "GFC",
    "Gylia Lakehouse": "GLH",
    "Gylia Research Cabin": "GRC",
    "Gylia Watchtower": "GWT",
    "Half Moon Island": "HMI",
    "Harnort Compound": "HNC",
    "Harpy's Haunt North": "HHN",
    "Harpy's Haunt South": "HHS",
    "Heart of Decay": "HOD",
    "Heavenly Ingress": "HVI",
    "Herb Cave": "HBC",
    "Hobgoblin's Hoard": "HGH",
    "Housing Crisis": "HCR",
    "Iboju Village": "IBV",
    "Icy Descent": "ICD",
    "Icy Island": "ICY",
    "Icy Vigil": "ICV",
    "Illuminant Path": "ILP",
    "Industrial Clearing": "INC",
    "Infested Sinkhole": "ISH",
    "Inhospitable Mountain": "IHM",
    "Invaded Barracks": "INB",
    "Iron Road": "IRR",
    "Jagged Foothills": "JGF",
    "Jitak's Farm": "JTF",
    "Jofash Docks": "JFD",
    "Jofash Tunnel": "JFT",
    "Jungle Entrance": "JNE",
    "Kander Mines": "KDM",
    "Kandon Farm": "KNF",
    "Kandon Ridge": "KNR",
    "Kandon-Beda": "KNB",
    "Karoc Quarry": "KRQ",
    "Katoa Ranch": "KTR",
    "Kitrios Armory": "KTA",
    "Kitrios Barracks": "KTB",
    "Krolton's Cave": "KRC",
    "Lava Lakes": "LVL",
    "Lava Springs": "LVS",
    "Legendary Island": "LGI",
    "Lexdale": "LXD",
    "Lexdale Penitentiary": "LXP",
    "Lifeless Forest": "LLF",
    "Light Peninsula": "LPN",
    "Lighthouse Lookout": "LHL",
    "Lion Lair": "LNL",
    "Little Wood": "LTW",
    "Lizardman Camp": "LZC",
    "Lizardman Lake": "LZL",
    "Llevigar": "LLV",
    "Llevigar Farm": "LVF",
    "Llevigar Gate": "LVG",
    "Llevigar Stables": "LVB",
    "Loamsprout Orc Camp": "LOC",
    "Lost Atoll": "LAT",
    "Luminous Plateau": "LMP",
    "Lusuco": "LSC",
    "Lutho": "LTH",
    "Luxuriant Pond": "LXP2",
    "Mage Island": "MGI",
    "Maiden Tower": "MDT",
    "Maltic": "MLT",
    "Maltic Coast": "MLC",
    "Mangled Lake": "MGL",
    "Mantis Nest": "MNT",
    "Maro Peaks": "MRP",
    "Mesquis Tower": "MST",
    "Meteor Crater": "MTC",
    "Meteor Trail": "MTR",
    "Mine Base Plains": "MBP",
    "Mining Base Camp": "MBC",
    "Minotaur Barbecue": "MNB",
    "Molten Passage": "MPS",
    "Molten Reach": "MLR",
    "Monte's Village": "MTV",
    "Mount Wynn Inn": "MWI",
    "Mudspring Orc Camp": "MOC",
    "Mummy's Tomb": "MMT",
    "Mushroom Hill": "MSH",
    "Mycelial Expanse": "MYE",
    "Myconid Descent": "MYD",
    "Naga Lake": "NGL",
    "Nemract": "NMR",
    "Nemract Cathedral": "NMC",
    "Nesaak": "NSK",
    "Nesaak Transition": "NST",
    "Nested Cliffside": "NCS",
    "Nexus of Light": "NOL",
    "Nivla Woods": "NVW",
    "Nivla Woods Exit": "NWE",
    "Nodguj Nation": "NDN",
    "Nomads' Refuge": "NRF",
    "Ogre Den": "OGD",
    "Old Coal Mine": "OCM",
    "Old Crossroads": "OCR",
    "Olux": "OLX",
    "Olux Lumberyard": "OLY",
    "Orc Battlegrounds": "OBG",
    "Orc Lake": "ORL",
    "Orc Road": "ORR",
    "Otherworldly Monolith": "OWM",
    "Outer Aldorei Town": "OAT",
    "Overrun Docks": "OVD",
    "Overtaken Outpost": "OTO",
    "Owl Tribe": "OWT",
    "Panda Kingdom": "PDK",
    "Panda Path": "PDP",
    "Paper Trail": "PPT",
    "Parasitic Slime Mine": "PSM",
    "Path to Ahmsord": "PTA",
    "Path to Cinfras": "PTC",
    "Path to Light": "PTL",
    "Path to Light's Secret": "PLS",
    "Path to Ozoth's Spire": "POS",
    "Path to Talor": "PTT",
    "Path to Thanos": "PTH",
    "Path to the Dojo": "PTD",
    "Path to the Forgery": "PTF",
    "Path to the Grootslangs": "PTG",
    "Path to the Penitentiary": "PTP",
    "Paths of Sludge": "POS2",
    "Perilous Grotto": "PGT",
    "Perilous Passage": "PPG",
    "Picnic Pond": "PCP",
    "Pigmen Ravines": "PMR",
    "Pine Pillar Forest": "PPF",
    "Pirate Town": "PRT",
    "Plains Lake": "PLK",
    "Primal Fen": "PMF",
    "Protector's Pathway": "PRW",
    "Pyroclastic Flow": "PCF",
    "Ragni": "RAG",
    "Ragni Countryside North": "RCN",
    "Ragni Countryside South": "RCS",
    "Ragni Main Entrance": "RME",
    "Ragni North Entrance": "RNE",
    "Ragni South Entrance": "RSE",
    "Raiders' Airbase": "RAB",
    "Raiders' Stronghold": "RSH",
    "Ranol's Farm": "RNF",
    "Razed Inn": "RZI",
    "Regular Island": "RGI",
    "Relos": "RLS",
    "Retrofitted Manufactory": "RTM",
    "Riverbank Knoll": "RBK",
    "Road to Elkurn": "RTE",
    "Road to Light Forest": "RLF",
    "Road to Mine": "RTM2",
    "Road to Time Valley": "RTV",
    "Rocky Bend": "RKB",
    "Rocky Shore": "RKS",
    "Rodoroc": "RDR",
    "Rooster Island": "RSI",
    "Roots of Corruption": "ROC",
    "Royal Gate": "RYG",
    "Ruined Houses": "RNH",
    "Ruined Prospect": "RNP",
    "Ruined Villa": "RNV",
    "Rymek": "RYM",
    "Sablestone Orc Camp": "SOC",
    "Sanctuary Bridge": "SCB",
    "Sanguine Spider Den": "SSD",
    "Santa's Hideout": "STH",
    "Savannah Plains": "SVP",
    "Scorched Trail": "SCT",
    "Scorpion Nest": "SPN",
    "Secluded Ponds": "SCP",
    "Secluded Workshop": "SCW",
    "Selchar": "SEL",
    "Shady Shack": "SDS",
    "Shineridge Orc Camp": "SRC",
    "Silent Road": "SLR",
    "Silverbull Headquarters": "SBH",
    "Sinister Forest": "SNF",
    "Skien's Island": "SKI",
    "Sky Castle": "SKC",
    "Sky Falls": "SKF",
    "Sky Island Ascent": "SIA",
    "Snail Island": "SNI",
    "Southern Outpost": "SOP",
    "Stonecave Orc Camp": "STC",
    "Sulphuric Hollow": "SPH",
    "Sunrise Plateau": "SRP",
    "Sunset Plateau": "SNP",
    "Sunspark Orc Camp": "SSO",
    "Swamp Island": "SWI",
    "Swamp Mountain Arch": "SMA",
    "Talor Cemetery": "TLC",
    "Temple Island": "TPI",
    "Temple of Legends": "TOL",
    "Tempo Town": "TMT",
    "Ternaves": "TRN",
    "Ternaves Tunnel": "TRT",
    "Thanos": "THA",
    "Thanos Exit": "THE",
    "Thanos Underpass": "THU",
    "The Forgery": "TFG",
    "The Gate": "TGT",
    "The Hive": "THV",
    "The Shiar": "TSH",
    "Thesead": "TSD",
    "Thesead Suburbs": "TSS",
    "Thesead Underpass": "TSU",
    "Time Valley": "TMV",
    "Timeworn Arch": "TWA",
    "Tower of Ascension": "TOA",
    "Toxic Caves": "TXC",
    "Toxic Drip": "TXD",
    "Tree Island": "TRI",
    "Troll Tower": "TLT",
    "Troll's Challenge": "TRC",
    "Troms": "TRM",
    "Troms Lake": "TRL",
    "Trunkstump Goblin Camp": "TGC",
    "Turncoat Turnabout": "TCT",
    "Twain Lake": "TWL",
    "Twain Mansion": "TWM",
    "Twisted Housing": "TWH",
    "Twisted Ridge": "TWR",
    "Unicorn Trail": "UNT",
    "Upper Thanos": "UTH",
    "Viscera Pits": "VCP",
    "Void Valley": "VDV",
    "Volcanic Excavation": "VLE",
    "Volcanic Isles": "VLI",
    "Wanderer's Way": "WDW",
    "Waterfall Cave": "WFC",
    "Wayward Split": "WWS",
    "Webbed Fracture": "WBF",
    "Weird Clearing": "WRC",
    "Winding Waters": "WWW",
    "Witching Road": "WTR",
    "Wizard Tower": "WZT",
    "Wizard's Warning": "WZW",
    "Wolves' Den": "WLD",
    "Wood Sprite Hideaway": "WSH",
    "Workshop Glade": "WKG",
    "Worm Tunnel": "WMT",
    "Wybel Island": "WBI",
    "Zhight Island": "ZHI",
}

# ---------- HTTP (aiohttp single session + retries) ----------

_TERRITORY_URL = "https://api.wynncraft.com/v3/guild/list/territory"
_http_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
        _http_session = aiohttp.ClientSession(timeout=timeout, raise_for_status=True)
    return _http_session

async def _close_session():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()

async def getTerritoryData():
    try:
        sess = await _get_session()
        for attempt in range(3):
            try:
                async with sess.get(_TERRITORY_URL) as resp:
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                if attempt == 2:
                    return False
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.3))
    except Exception:
        return False

# ---------- File I/O (run in thread) ----------

def _read_territories_sync() -> dict:
    try:
        with open('territories.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def saveTerritoryData(data):
    # Save to JSON file
    with open('territories.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
        f.close()
    
    # Save to database cache
    try:
        db = DB()
        db.connect()
        
        # Set expiration to epoch time (January 1, 1970)
        epoch_time = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        
        # Use ON CONFLICT to either insert or update the cache entry
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at, fetch_count)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (cache_key) 
            DO UPDATE SET 
                data = EXCLUDED.data,
                created_at = NOW(),
                expires_at = EXCLUDED.expires_at,
                fetch_count = cache_entries.fetch_count + 1,
                last_error = NULL,
                error_count = 0
        """, ('territories', json.dumps(data), epoch_time))
        
        db.connection.commit()
        db.close()
        
    except Exception as e:
        print(f"[saveTerritoryData] Failed to save to cache: {e}")
        # Don't let database errors prevent the file save from working
        if 'db' in locals():
            try:
                db.close()
            except:
                pass

# ---------- Snapshot functions (for map history) ----------

def compress_snapshot(territory_data: dict) -> dict:
    """
    Compress territory data for storage in territory_snapshots table.
    Returns: { abbrev: { "g": guild_prefix, "n": guild_name }, ... }
    """
    snapshot = {}
    for terr_name, info in territory_data.items():
        guild = info.get('guild', {})
        guild_name = guild.get('name', '')
        # Only store claimed territories
        if guild_name:
            abbrev = TERRITORY_TO_ABBREV.get(terr_name, terr_name)
            snapshot[abbrev] = {
                "g": guild.get('prefix', ''),
                "n": guild_name
            }
    return snapshot


def save_territory_snapshot(territory_data: dict):
    """Save a compressed snapshot to territory_snapshots table."""
    try:
        db = DB()
        db.connect()

        # Create table if it doesn't exist
        db.cursor.execute("""
            CREATE TABLE IF NOT EXISTS territory_snapshots (
                id            SERIAL       PRIMARY KEY,
                snapshot_time TIMESTAMPTZ  NOT NULL,
                territories   JSONB        NOT NULL,
                created_at    TIMESTAMPTZ  DEFAULT NOW()
            )
        """)
        db.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_time
            ON territory_snapshots(snapshot_time DESC)
        """)
        db.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_time_range
            ON territory_snapshots(snapshot_time)
        """)

        snapshot = compress_snapshot(territory_data)
        now = datetime.datetime.now(datetime.timezone.utc)

        db.cursor.execute("""
            INSERT INTO territory_snapshots (snapshot_time, territories)
            VALUES (%s, %s)
        """, (now, json.dumps(snapshot)))

        db.connection.commit()
        db.close()
        print(f"[save_territory_snapshot] Saved snapshot at {now.isoformat()}")
    except Exception as e:
        print(f"[save_territory_snapshot] Failed: {e}")
        if 'db' in locals():
            try:
                db.close()
            except:
                pass


# ---------- Time helper (unchanged) ----------

def timeHeld(date_time_old, date_time_new):
    t_old = datetime.datetime.fromisoformat(date_time_old[0:len(date_time_old) - 1])
    t_new = datetime.datetime.fromisoformat(date_time_new[0:len(date_time_new) - 1])
    t_held = t_new.__sub__(t_old)

    d = t_held.days
    td = datetime.timedelta(seconds=t_held.seconds)
    t = str(td).split(":")

    return f"{d} d {t[0]} h {t[1]} m {t[2]} s"


# Helper functions for new features
def get_all_hq_territories():
    """Get a set of all HQ territory names from claims configuration."""
    hq_territories = set()
    for claim_name, cfg in claims.items():
        hq = cfg.get("hq")
        if hq:
            hq_territories.add(hq)
    return hq_territories


class TerritoryTracker(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.last_snapshot_minute = -1  # Track last snapshot to avoid duplicates
        self.territory_tracker.start()

    def cog_unload(self):
        self.territory_tracker.cancel()
        asyncio.create_task(_close_session())

    @tasks.loop(seconds=10)
    async def territory_tracker(self):
        try:
            if not self.client.is_ready():
                return

            channel = self.client.get_channel(territory_tracker_channel)
            if channel is None:
                return

            old_data = await asyncio.to_thread(_read_territories_sync)

            new_data = await getTerritoryData()
            if not new_data:
                return

            await asyncio.to_thread(saveTerritoryData, new_data)

            # Save snapshot every 10 minutes (at :00, :10, :20, :30, :40, :50)
            current_minute = datetime.datetime.now(datetime.timezone.utc).minute
            if current_minute % 10 == 0 and current_minute != self.last_snapshot_minute:
                self.last_snapshot_minute = current_minute
                await asyncio.to_thread(save_territory_snapshot, new_data)

            # tally post-update counts
            new_counts = Counter()
            for info in new_data.values():
                new_counts[info['guild']['name']] += 1

            # ---------- CLAIM-BROKEN ALERTS (CONFIG-DRIVEN) ----------
            # fires on transition: previously owned ALL tiles in claim → now missing any tile
            if old_data and claims:
                for claim_name, cfg in claims.items():
                    hq = cfg.get("hq")
                    conns: List[str] = cfg.get("connections", [])
                    if not hq:
                        continue
                    members: List[str] = [hq] + conns

                    def _owns_all(data: Dict) -> bool:
                        for t in members:
                            owner = data.get(t, {}).get("guild", {}).get("name")
                            if owner != 'The Aquarium':
                                return False
                        return True

                    old_all = _owns_all(old_data)
                    new_all = _owns_all(new_data)

                    if old_all and not new_all:
                        # what flipped away from our guild?
                        lost = [
                            t for t in members
                            if old_data.get(t, {}).get("guild", {}).get("name") == "The Aquarium"
                            and new_data.get(t, {}).get("guild", {}).get("name") != "The Aquarium"
                        ]

                        # determine which territory and who took it
                        if hq in lost:
                            lost_terr = hq
                            terr_type = "HQ"
                        elif lost:
                            lost_terr = lost[0]
                            terr_type = "connection"
                        else:
                            lost_terr = None
                            terr_type = "connection"

                        # Check spearhead ping conditions:
                        # 1. Guild owns more than 7 territories
                        # 2. We had held all territories in this claim for >20 minutes
                        aquarium_territory_count = sum(
                            1 for info in old_data.values()
                            if info.get('guild', {}).get('name') == 'The Aquarium'
                        )

                        should_ping_spearhead = False
                        if aquarium_territory_count > 7:
                            # Check if we had held all claim territories for >20 minutes
                            current_time = datetime.datetime.now(datetime.timezone.utc)
                            most_recent_acquisition = None

                            for terr in members:
                                terr_info = old_data.get(terr)
                                if terr_info and terr_info.get('guild', {}).get('name') == 'The Aquarium':
                                    acquired_str = terr_info.get('acquired', '')
                                    if acquired_str:
                                        acquired_time = datetime.datetime.fromisoformat(acquired_str.rstrip('Z'))
                                        acquired_time = acquired_time.replace(tzinfo=datetime.timezone.utc)
                                        if most_recent_acquisition is None or acquired_time > most_recent_acquisition:
                                            most_recent_acquisition = acquired_time

                            if most_recent_acquisition:
                                time_held = current_time - most_recent_acquisition
                                if time_held.total_seconds() > 1200:  # 20 minutes
                                    should_ping_spearhead = True

                        # Alert
                        alert_chan = self.client.get_channel(military_channel)

                        # Check if attack pings are enabled via toggle
                        if should_ping_spearhead and alert_chan:
                            try:
                                db = DB()
                                db.connect()
                                db.cursor.execute(
                                    "SELECT setting_value FROM guild_settings WHERE guild_id = %s AND setting_key = %s",
                                    (alert_chan.guild.id, 'attack_ping')
                                )
                                result = db.cursor.fetchone()
                                db.close()
                                # Default to True if no setting exists, but respect toggle if set
                                if result is not None and not result[0]:
                                    should_ping_spearhead = False
                            except Exception:
                                pass  # If DB check fails, use the existing should_ping_spearhead value

                        # get the guild that took the territory and build message
                        if lost_terr:
                            attacker = new_data.get(lost_terr, {}).get("guild", {}).get("name", "Unknown")
                            attacker_prefix = new_data.get(lost_terr, {}).get("guild", {}).get("prefix", "???")
                            if should_ping_spearhead:
                                mention = f"<@&{spearhead_role_id}>"
                                msg = f"{mention} **Attack on {claim_name}!** {terr_type.capitalize()} **{lost_terr}** taken by **{attacker} [{attacker_prefix}]**"
                            else:
                                msg = f"**Attack on {claim_name}!** {terr_type.capitalize()} **{lost_terr}** taken by **{attacker} [{attacker_prefix}]**"
                        else:
                            if should_ping_spearhead:
                                mention = f"<@&{spearhead_role_id}>"
                                msg = f"{mention} **Attack on {claim_name}!** A {terr_type} was taken."
                            else:
                                msg = f"**Attack on {claim_name}!** A {terr_type} was taken."

                        if alert_chan:
                            await alert_chan.send(msg)

            # ---------- Territory Change Embeds ----------
            owner_changes = {}
            for terr, new_info in new_data.items():
                old_info = old_data.get(terr)
                if not old_info:
                    continue
                old_owner = old_info['guild']['name']
                new_owner = new_info['guild']['name']
                if old_owner != new_owner and ('The Aquarium' in (old_owner, new_owner)):
                    owner_changes[terr] = {
                        'old': {
                            'owner': old_owner,
                            'prefix': old_info['guild']['prefix'],
                            'acquired': old_info['acquired']
                        },
                        'new': {
                            'owner': new_owner,
                            'prefix': new_info['guild']['prefix'],
                            'acquired': new_info['acquired']
                        }
                    }

            # Check for HQ captures and send congratulations
            hq_territories = get_all_hq_territories()
            for terr, change in owner_changes.items():
                old = change['old']
                new = change['new']

                # Check if this is an HQ capture by The Aquarium
                if (terr in hq_territories and
                    old['owner'] != 'The Aquarium' and
                    new['owner'] == 'The Aquarium'):

                    # Find which claim this HQ belongs to
                    claim_name = None
                    for c_name, cfg in claims.items():
                        if cfg.get("hq") == terr:
                            claim_name = c_name
                            break

                    if claim_name:
                        # Send congratulations message to military channel (no ping)
                        alert_chan = self.client.get_channel(military_channel)
                        if alert_chan:
                            congrats_msg = f"🎉 Congratulations on a successful snipe of **{claim_name}** owned by **{old['owner']}**!"
                            await alert_chan.send(congrats_msg)

                # Determine gain vs loss
                if new['owner'] == 'The Aquarium':
                    color = discord.Color.green()
                    title = f"🟢 Territory Gained: **{terr}**"
                else:
                    color = discord.Color.red()
                    title = f"🔴 Territory Lost: **{terr}**"

                taken_dt = datetime.datetime.fromisoformat(new['acquired'].rstrip('Z'))
                taken_dt = taken_dt.replace(tzinfo=datetime.timezone.utc)

                embed = discord.Embed(
                    title=title,
                    color=color,
                    # timestamp=taken_dt
                )
                embed.add_field(
                    name="Old Owner",
                    value=(
                        f"{old['owner']} [{old['prefix']}]\n"
                        f"Territories: {new_counts.get(old['owner'], 0)}"
                    ),
                    inline=True
                )

                embed.add_field(
                    name="\u200b",
                    value="➜",
                    inline=True
                )

                embed.add_field(
                    name="New Owner",
                    value=(
                        f"{new['owner']} [{new['prefix']}]\n"
                        f"Territories: {new_counts.get(new['owner'], 0)}"
                    ),
                    inline=True
                )

                await channel.send(embed=embed)

        except Exception as e:
            # Log and continue; the task loop will run again next tick
            print(f"[territory_tracker] error: {e!r}")

    @commands.Cog.listener()
    async def on_ready(self):
        data = await getTerritoryData()
        if data:
            await asyncio.to_thread(saveTerritoryData, data)
        if not self.territory_tracker.is_running():
            self.territory_tracker.start()


def setup(client):
    client.add_cog(TerritoryTracker(client))
