from dataclasses import dataclass, field
from copy import deepcopy
import json
import math
import random

# ============================================================
# CONFIG
# ============================================================

CAMP_ID = 10366

LOADOUTS = [
    {
        "id": "loadout-narcissistic",
        "name": "NG",
        "generalName": "Narcissistic General",
        "skills": {
            "First Aid": 1,
            "Jog": 3,
            "Juggernaut": 3,
            "Overrun": 3,
            "Navigation Crash Course": 2,
            "Fast Learner": 3,
            "Garrison Annex": 2,
            "Confident Leader": 3,
            "Lightning Slash": 1,
        },
        "acceptableLosses": {
            "Recruit": 20,
            "Soldier": 0,
            "EliteSoldier": 0,
            "Cannoneer": 0,
            "Cavalry": 0,
            "GENERAL": 0,
        },
    },
    {
        "id": "loadout-mary-christmas",
        "name": "Mary",
        "generalName": "General Mary Christmas",
        "skills": {
            "First Aid": 2,
            "Jog": 3,
            "Overrun": 3,
            "Navigation Crash Course": 2,
            "Hostile Takeover": 1,
            "Battle Frenzy": 3,
            "Fast Learner": 3,
            "Garrison Annex": 3,
            "Bounty Hunter": 1,
        },
        "acceptableLosses": {
            "Recruit": 200,
            "Soldier": 0,
            "EliteSoldier": 0,
            "Cannoneer": 0,
            "Cavalry": 0,
            "GENERAL": 0,
        },
    },
]

PLAYER_UNITS = ["Recruit", "Soldier", "EliteSoldier", "Cannoneer", "Cavalry"]
ORDER = ["Recruit", "Soldier", "EliteSoldier", "Cannoneer", "Cavalry"]

MAX_ROUNDS = 100
TOP_CANDIDATES_FOR_MONTE_CARLO = 20
MONTE_CARLO_RUNS = 1000

random.seed(42)

LOSS_COST = {
    "Recruit": 1,
    "Soldier": 5,
    "EliteSoldier": 10,
    "Cannoneer": 12,
    "Cavalry": 8,
    "GENERAL": 100000,
}

SEND_COST = {
    "Recruit": 0.001,
    "Soldier": 0.005,
    "EliteSoldier": 0.010,
    "Cannoneer": 0.012,
    "Cavalry": 0.008,
}


# ============================================================
# LOAD DATA
# ============================================================

with open("units.json", "r", encoding="utf-8") as f:
    UNITS_RAW = json.load(f)

with open("generals.json", "r", encoding="utf-8") as f:
    GENERALS_RAW = json.load(f)

with open("horseback.json", "r", encoding="utf-8") as f:
    HORSEBACK = json.load(f)

UNITS = {u["identifier"]: u for u in UNITS_RAW if "identifier" in u}
GENERALS = {g["name"]: g for g in GENERALS_RAW}

# Ajout manuel de Mary si absente du fichier generals.json
GENERALS["General Mary Christmas"] = {
    "name": "General Mary Christmas",
    "units": 200,
    "hp": 100,
    "damage": "125-250",
    "accuracy": "80%",
    "attributes": [
        "Increases XP gained from enemy units defeated by this army by 100%"
    ],
    "additional_features": [
        "Travels twice as quickly to adventures.",
        "Recover twice as quickly from defeat.",
        "Increases XP gained from enemy units defeated by this army by 100%."
    ],
}


# ============================================================
# MODEL
# ============================================================

@dataclass
class Stack:
    identifier: str
    count: int
    hp: float
    accuracy: float
    dmg_min: float
    dmg_max: float
    attack_order: int
    category: int | None = None
    experience: int = 0
    skills: list[str] = field(default_factory=list)
    hp_current: float | None = None

    def __post_init__(self):
        if self.hp_current is None:
            self.hp_current = self.hp

    @property
    def alive(self):
        return self.count > 0

    @property
    def avg_damage_per_unit(self):
        return self.accuracy * self.dmg_max + (1 - self.accuracy) * self.dmg_min

    @property
    def first_strike(self):
        return self.attack_order == 1 or "UnitSkillFirstStrike" in self.skills

    @property
    def last_strike(self):
        return self.attack_order == 3 or "UnitSkillLastStrike" in self.skills

    @property
    def flanking(self):
        return "UnitSkillAttackWeakestTarget" in self.skills

    @property
    def splash(self):
        return "UnitSkillSplashDamage" in self.skills

    @property
    def is_boss(self):
        return self.category == 2


# ============================================================
# PARSING
# ============================================================

def parse_accuracy(value):
    if isinstance(value, str):
        return float(value.replace("%", "")) / 100
    return float(value) / 100 if value > 1 else float(value)


def parse_damage(value):
    if isinstance(value, (int, float)):
        return float(value), float(value)

    value = str(value).replace(".", "")

    if "-" in value:
        a, b = value.split("-")
        return float(a), float(b)

    return float(value), float(value)


# ============================================================
# SKILLS
# ============================================================

def first_aid_rate(build):
    return 0.03 * build.get("First Aid", 0)


def juggernaut_bonus_damage(build):
    return 20 * build.get("Juggernaut", 0)


def juggernaut_splash_chance(build):
    return {
        0: 0.00,
        1: 0.33,
        2: 0.66,
        3: 1.00,
    }.get(build.get("Juggernaut", 0), 0.00)


def overrun_boss_hp_reduction(build):
    return {
        0: 0.00,
        1: 0.08,
        2: 0.16,
        3: 0.25,
    }.get(build.get("Overrun", 0), 0.00)


def garrison_annex_bonus(build):
    return 5 * build.get("Garrison Annex", 0)


def confident_leader_round_multiplier(build):
    return {
        0: 1.00,
        1: 0.85,
        2: 0.70,
        3: 0.55,
    }.get(build.get("Confident Leader", 0), 1.00)


def fast_learner_xp_multiplier(build):
    return 1 + 0.10 * build.get("Fast Learner", 0)


def jog_movement_multiplier(build):
    return 1 + 0.33 * build.get("Jog", 0)


def navigation_travel_multiplier(build):
    return {
        0: 1.00,
        1: 0.85,
        2: 0.70,
        3: 0.55,
    }.get(build.get("Navigation Crash Course", 0), 1.00)


def battle_frenzy_multiplier(build, round_number):
    rank = build.get("Battle Frenzy", 0)
    bonus_per_round = 0.10 * rank

    # Round 1 = pas encore de bonus.
    return 1 + bonus_per_round * max(0, round_number - 1)


def natural_xp_multiplier(general_name):
    g = GENERALS[general_name]
    attrs = g.get("attributes", []) + g.get("additional_features", [])

    if any("xp gained" in a.lower() and "100%" in a.lower() for a in attrs):
        return 2.0

    return 1.0


# ============================================================
# FACTORIES
# ============================================================

def make_unit_stack(identifier, count):
    u = UNITS[identifier]

    return Stack(
        identifier=identifier,
        count=count,
        hp=float(u.get("hitPoints", 1)),
        accuracy=parse_accuracy(u.get("accuracy", 100)),
        dmg_min=float(u.get("damageMin", u.get("damageMax", 0))),
        dmg_max=float(u.get("damageMax", u.get("damageMin", 0))),
        attack_order=int(u.get("attackOrder", 2)),
        category=u.get("category"),
        experience=int(u.get("experience", 0)),
        skills=u.get("skills", []),
    )


def make_general_stack(general_name, build):
    g = GENERALS[general_name]
    dmg_min, dmg_max = parse_damage(g["damage"])
    attrs = g.get("attributes", [])

    skills = []

    if any("first" in a.lower() for a in attrs):
        skills.append("UnitSkillFirstStrike")

    if any("flanking" in a.lower() for a in attrs):
        skills.append("UnitSkillAttackWeakestTarget")

    if any("splash" in a.lower() for a in attrs):
        skills.append("UnitSkillSplashDamage")

    if juggernaut_splash_chance(build) >= 1:
        skills.append("UnitSkillSplashDamage")

    return Stack(
        identifier="GENERAL",
        count=1,
        hp=float(g["hp"]),
        accuracy=parse_accuracy(g["accuracy"]),
        dmg_min=dmg_min + juggernaut_bonus_damage(build),
        dmg_max=dmg_max + juggernaut_bonus_damage(build),
        attack_order=1 if any("first" in a.lower() for a in attrs) else 2,
        category=None,
        experience=0,
        skills=skills,
    )


def general_capacity(general_name, build):
    return int(GENERALS[general_name]["units"]) + garrison_annex_bonus(build)


# ============================================================
# ARMY HELPERS
# ============================================================

def army_alive(army):
    return any(stack.alive for stack in army)


def army_counts(army):
    return {stack.identifier: stack.count for stack in army if stack.count > 0}


def target_priority(stack):
    if stack.identifier == "GENERAL":
        return 999

    if stack.identifier in ORDER:
        return ORDER.index(stack.identifier)

    return 500


def select_target(defenders, flanking=False):
    candidates = [stack for stack in defenders if stack.alive]

    if not candidates:
        return None

    if flanking:
        return min(candidates, key=lambda stack: (target_priority(stack), stack.hp))

    return min(candidates, key=target_priority)


# ============================================================
# DAMAGE
# ============================================================

def roll_damage(stack, deterministic=True):
    if deterministic:
        return stack.avg_damage_per_unit

    return stack.dmg_max if random.random() < stack.accuracy else stack.dmg_min


def apply_damage(defenders, damage, flanking=False, splash=False):
    killed = {}

    while damage > 0 and army_alive(defenders):
        target = select_target(defenders, flanking=flanking)

        if target is None:
            break

        if damage >= target.hp_current:
            damage -= target.hp_current
            target.count -= 1
            killed[target.identifier] = killed.get(target.identifier, 0) + 1

            if target.count > 0:
                target.hp_current = target.hp
            else:
                target.hp_current = 0

            if not splash:
                break
        else:
            target.hp_current -= damage
            damage = 0

    return killed


# ============================================================
# BUILD EFFECTS
# ============================================================

def apply_general_natural_boss_reduction(enemy_army, general_name):
    g = GENERALS[general_name]
    attrs = g.get("attributes", [])

    has_boss_reduction = any(
        "bosses have 25% reduced health" in a.lower()
        for a in attrs
    )

    if not has_boss_reduction:
        return

    for stack in enemy_army:
        if stack.is_boss:
            stack.hp *= 0.75
            stack.hp_current = stack.hp


def apply_overrun(enemy_army, build):
    reduction = overrun_boss_hp_reduction(build)

    for stack in enemy_army:
        if stack.is_boss:
            stack.hp *= 1 - reduction
            stack.hp_current = stack.hp


# ============================================================
# COMBAT
# ============================================================

def stack_attacks_in_phase(stack, phase):
    if not stack.alive:
        return False

    if phase == 1:
        return stack.first_strike

    if phase == 2:
        return not stack.first_strike and not stack.last_strike

    if phase == 3:
        return stack.last_strike

    return False


def attack_phase(attackers, defenders, phase, build, round_number, deterministic=True):
    killed_total = {}

    for stack in attackers:
        if not stack_attacks_in_phase(stack, phase):
            continue

        if not army_alive(defenders):
            break

        for _ in range(stack.count):
            if not army_alive(defenders):
                break

            damage = roll_damage(stack, deterministic=deterministic)

            # Battle Frenzy appliqué uniquement à l'armée qui possède ce build.
            damage *= battle_frenzy_multiplier(build, round_number)

            killed = apply_damage(
                defenders=defenders,
                damage=damage,
                flanking=stack.flanking,
                splash=stack.splash,
            )

            for unit, count in killed.items():
                killed_total[unit] = killed_total.get(unit, 0) + count

    return killed_total


def lightning_slash_attack(player_army, enemy_army, build, round_number, deterministic=True):
    general = player_army[0]

    if not general.alive or not army_alive(enemy_army):
        return {}

    damage = roll_damage(general, deterministic=deterministic)
    damage *= battle_frenzy_multiplier(build, round_number)

    return apply_damage(
        defenders=enemy_army,
        damage=damage,
        flanking=general.flanking,
        splash=general.splash,
    )


def simulate(player_army, enemy_army, build, general_name, deterministic=True):
    player = deepcopy(player_army)
    enemy = deepcopy(enemy_army)

    apply_general_natural_boss_reduction(enemy, general_name)
    apply_overrun(enemy, build)

    initial_player_counts = {stack.identifier: stack.count for stack in player}
    initial_enemy_counts = {stack.identifier: stack.count for stack in enemy}

    rounds = 0

    while army_alive(player) and army_alive(enemy) and rounds < MAX_ROUNDS:
        rounds += 1

        for phase in [1, 2, 3]:
            attack_phase(
                attackers=player,
                defenders=enemy,
                phase=phase,
                build=build,
                round_number=rounds,
                deterministic=deterministic,
            )

            if not army_alive(enemy):
                break

            attack_phase(
                attackers=enemy,
                defenders=player,
                phase=phase,
                build={},
                round_number=rounds,
                deterministic=deterministic,
            )

            if not army_alive(player):
                break

        if build.get("Lightning Slash", 0) >= 1 and army_alive(player) and army_alive(enemy):
            lightning_slash_attack(
                player_army=player,
                enemy_army=enemy,
                build=build,
                round_number=rounds,
                deterministic=deterministic,
            )

    final_player_counts = {stack.identifier: stack.count for stack in player}
    final_enemy_counts = {stack.identifier: stack.count for stack in enemy}

    losses = {
        unit: initial_player_counts[unit] - final_player_counts.get(unit, 0)
        for unit in initial_player_counts
    }

    recovered = {
        unit: math.floor(loss * first_aid_rate(build))
        for unit, loss in losses.items()
    }

    net_losses = {
        unit: losses[unit] - recovered[unit]
        for unit in losses
    }

    killed_enemies = {
        unit: initial_enemy_counts[unit] - final_enemy_counts.get(unit, 0)
        for unit in initial_enemy_counts
    }

    base_xp = sum(
        UNITS[identifier].get("experience", 0) * killed_count
        for identifier, killed_count in killed_enemies.items()
    )

    xp_after_fast_learner = base_xp * fast_learner_xp_multiplier(build)
    xp_after_general_bonus = xp_after_fast_learner * natural_xp_multiplier(general_name)

    if general_name == "Narcissistic General":
        final_xp = xp_after_general_bonus * 0.20
    else:
        final_xp = xp_after_general_bonus

    return {
        "win": army_alive(player) and not army_alive(enemy),
        "rounds": rounds,
        "effective_round_time_multiplier": confident_leader_round_multiplier(build),
        "losses_before_first_aid": losses,
        "recovered_by_first_aid": recovered,
        "net_losses": net_losses,
        "remaining_player": army_counts(player),
        "remaining_enemy": army_counts(enemy),
        "killed_enemies": killed_enemies,
        "base_xp": base_xp,
        "xp_after_fast_learner": xp_after_fast_learner,
        "xp_after_general_bonus": xp_after_general_bonus,
        "final_xp": final_xp,
        "non_combat_bonuses": {
            "jog_movement_multiplier": jog_movement_multiplier(build),
            "navigation_travel_multiplier": navigation_travel_multiplier(build),
        },
    }


# ============================================================
# FILTERING & SCORING
# ============================================================

def losses_are_acceptable(net_losses, acceptable_losses):
    for unit, lost in net_losses.items():
        allowed = acceptable_losses.get(unit, 0)

        if lost > allowed:
            return False

    return True


def composition_size(composition):
    return sum(composition.values())


def score_losses(net_losses):
    score = 0

    for unit, lost in net_losses.items():
        score += lost * LOSS_COST.get(unit, 9999)

    return score


def score_result(result, composition):
    score = score_losses(result["net_losses"])

    score += composition_size(composition) * 0.001

    for unit, count in composition.items():
        score += count * SEND_COST.get(unit, 1)

    score += result["rounds"] * 0.01

    # Plus l'XP est haute, plus le score baisse légèrement.
    score -= result["final_xp"] * 0.00001

    return score


def monte_carlo_score(mc):
    return (
        (100 - mc["win_rate"]) * 1000
        + (100 - mc["acceptable_rate"]) * 100
        + mc["avg_loss_score"]
        + mc["avg_rounds"] * 0.01
        + mc["avg_sent_cost"]
        - mc["avg_final_xp"] * 0.00001
    )


# ============================================================
# MONTE CARLO
# ============================================================

def monte_carlo_validate(
    general_name,
    build,
    acceptable_losses,
    composition,
    enemy_army,
    runs=1000,
):
    wins = 0
    acceptable_count = 0
    total_rounds = 0
    total_losses = {}
    max_losses = {}
    total_final_xp = 0

    for _ in range(runs):
        player_army = [make_general_stack(general_name, build)]

        for unit, count in composition.items():
            if count > 0:
                player_army.append(make_unit_stack(unit, count))

        result = simulate(
            player_army=player_army,
            enemy_army=enemy_army,
            build=build,
            general_name=general_name,
            deterministic=False,
        )

        if result["win"]:
            wins += 1

        if result["win"] and losses_are_acceptable(
            result["net_losses"],
            acceptable_losses,
        ):
            acceptable_count += 1

        total_rounds += result["rounds"]
        total_final_xp += result["final_xp"]

        for unit, lost in result["net_losses"].items():
            total_losses[unit] = total_losses.get(unit, 0) + lost
            max_losses[unit] = max(max_losses.get(unit, 0), lost)

    avg_losses = {
        unit: total / runs
        for unit, total in total_losses.items()
    }

    avg_loss_score = sum(
        avg_lost * LOSS_COST.get(unit, 9999)
        for unit, avg_lost in avg_losses.items()
    )

    sent_cost = sum(
        count * SEND_COST.get(unit, 1)
        for unit, count in composition.items()
    )

    return {
        "runs": runs,
        "wins": wins,
        "win_rate": wins / runs * 100,
        "acceptable_rate": acceptable_count / runs * 100,
        "avg_rounds": total_rounds / runs,
        "avg_losses": avg_losses,
        "max_losses": max_losses,
        "avg_loss_score": avg_loss_score,
        "avg_sent_cost": sent_cost,
        "avg_final_xp": total_final_xp / runs,
    }


# ============================================================
# FORMAT
# ============================================================

def format_comp(general_name, composition):
    parts = [general_name]

    for unit in ORDER:
        count = composition.get(unit, 0)

        if count > 0:
            parts.append(f"{count} {unit}")

    return " + ".join(parts)


# ============================================================
# ENEMY ANALYSIS & SMART COMPOSITION GENERATOR
# ============================================================

def analyze_enemy_army(enemy_army):
    total_hp = 0
    total_units = 0
    total_avg_damage = 0
    has_first_strike = False
    has_flanking = False
    has_splash = False
    has_boss = False

    for stack in enemy_army:
        total_hp += stack.hp * stack.count
        total_units += stack.count
        total_avg_damage += stack.avg_damage_per_unit * stack.count
        has_first_strike = has_first_strike or stack.first_strike
        has_flanking = has_flanking or stack.flanking
        has_splash = has_splash or stack.splash
        has_boss = has_boss or stack.is_boss

    avg_damage_per_enemy_unit = total_avg_damage / total_units if total_units else 0
    avg_hp_per_enemy_unit = total_hp / total_units if total_units else 0

    return {
        "total_hp": total_hp,
        "total_units": total_units,
        "avg_damage_per_enemy_unit": avg_damage_per_enemy_unit,
        "avg_hp_per_enemy_unit": avg_hp_per_enemy_unit,
        "has_first_strike": has_first_strike,
        "has_flanking": has_flanking,
        "has_splash": has_splash,
        "has_boss": has_boss,
    }


def composition_from_ratio(total_units, ratio):
    composition = {unit: 0 for unit in PLAYER_UNITS}
    raw = {unit: total_units * ratio.get(unit, 0) for unit in PLAYER_UNITS}
    composition = {unit: int(raw[unit]) for unit in PLAYER_UNITS}
    remaining = total_units - sum(composition.values())

    remainders = sorted(
        PLAYER_UNITS,
        key=lambda unit: raw[unit] - composition[unit],
        reverse=True,
    )

    idx = 0
    while remaining > 0:
        composition[remainders[idx % len(remainders)]] += 1
        idx += 1
        remaining -= 1

    return composition


def generate_profiled_compositions(capacity, general_name, enemy_profile):
    candidate_totals = {
        max(1, min(capacity, enemy_profile["total_units"])),
        max(1, min(capacity, int(enemy_profile["total_units"] * 0.6))),
        max(1, min(capacity, int(enemy_profile["total_units"] * 1.2))),
    }

    for ratio in [0.15, 0.30, 0.50, 0.75, 1.00]:
        candidate_totals.add(max(1, min(capacity, int(capacity * ratio))))

    if enemy_profile["has_boss"]:
        candidate_totals.add(max(1, min(capacity, int(capacity * 0.40))))
        candidate_totals.add(max(1, min(capacity, int(capacity * 0.90))))

    if general_name == "Narcissistic General":
        for minimal in [1, 2, 5, 10]:
            candidate_totals.add(minimal if minimal <= capacity else capacity)

    archetypes = [
        {
            "name": "balanced",
            "ratio": {
                "Recruit": 0.35,
                "Soldier": 0.10,
                "EliteSoldier": 0.20,
                "Cannoneer": 0.30,
                "Cavalry": 0.05,
            },
        },
        {
            "name": "tank-heavy",
            "ratio": {
                "Recruit": 0.60,
                "Soldier": 0.15,
                "EliteSoldier": 0.15,
                "Cannoneer": 0.05,
                "Cavalry": 0.05,
            },
        },
        {
            "name": "dps-heavy",
            "ratio": {
                "Recruit": 0.15,
                "Soldier": 0.10,
                "EliteSoldier": 0.30,
                "Cannoneer": 0.40,
                "Cavalry": 0.05,
            },
        },
    ]

    if enemy_profile["has_first_strike"] or enemy_profile["has_flanking"]:
        archetypes.append({
            "name": "first-strike-buffer",
            "ratio": {
                "Recruit": 0.75,
                "Soldier": 0.10,
                "EliteSoldier": 0.10,
                "Cannoneer": 0.05,
                "Cavalry": 0.00,
            },
        })

    if enemy_profile["avg_hp_per_enemy_unit"] >= 70:
        archetypes.append({
            "name": "anti-high-hp",
            "ratio": {
                "Recruit": 0.20,
                "Soldier": 0.10,
                "EliteSoldier": 0.20,
                "Cannoneer": 0.45,
                "Cavalry": 0.05,
            },
        })

    if enemy_profile["has_boss"]:
        archetypes.append({
            "name": "boss-burst",
            "ratio": {
                "Recruit": 0.05,
                "Soldier": 0.05,
                "EliteSoldier": 0.20,
                "Cannoneer": 0.70,
                "Cavalry": 0.00,
            },
        })

    unique = set()

    for total_units in sorted(candidate_totals):
        for archetype in archetypes:
            composition = composition_from_ratio(total_units, archetype["ratio"])
            key = tuple(composition[unit] for unit in ORDER)

            if sum(composition.values()) > capacity or key in unique:
                continue

            unique.add(key)
            yield composition

    if general_name == "Narcissistic General":
        for unit in PLAYER_UNITS:
            composition = {k: 0 for k in PLAYER_UNITS}
            composition[unit] = 1
            key = tuple(composition[u] for u in ORDER)

            if key not in unique:
                unique.add(key)
                yield composition


# ============================================================
# MAIN
# ============================================================

def main():
    camp = next(c for c in HORSEBACK["leirit"] if c["tunniste"] == CAMP_ID)

    enemy_army = [
        make_unit_stack(identifier, count)
        for identifier, count in camp["joukot"].items()
    ]
    enemy_profile = analyze_enemy_army(enemy_army)

    all_validated_results = []

    print("=" * 80)
    print("SIMULATION MULTI-GÉNÉRAUX")
    print("=" * 80)
    print("Aventure : Horseback")
    print(f"Camp : {CAMP_ID}")
    print(f"Ennemis : {camp['joukot']}")
    print("Analyse camp ennemi :")
    print(
        "  HP totaux={:.0f}, unités={}, dégâts moyens/unité={:.2f}, HP moyens/unité={:.2f}".format(
            enemy_profile["total_hp"],
            enemy_profile["total_units"],
            enemy_profile["avg_damage_per_enemy_unit"],
            enemy_profile["avg_hp_per_enemy_unit"],
        )
    )
    print(
        "  Propriétés : FirstStrike={}, Flanking={}, Splash={}, Boss={}".format(
            enemy_profile["has_first_strike"],
            enemy_profile["has_flanking"],
            enemy_profile["has_splash"],
            enemy_profile["has_boss"],
        )
    )
    print("=" * 80)

    for loadout in LOADOUTS:
        general_name = loadout["generalName"]
        build = loadout["skills"]
        acceptable_losses = loadout["acceptableLosses"]
        capacity = general_capacity(general_name, build)

        deterministic_results = []
        tested = 0

        print()
        print("-" * 80)
        print(f"Test général : {general_name}")
        print(f"Capacité max : {capacity}")
        print(f"Pertes acceptables : {acceptable_losses}")

        for composition in generate_profiled_compositions(
            capacity=capacity,
            general_name=general_name,
            enemy_profile=enemy_profile,
        ):
            tested += 1

            player_army = [make_general_stack(general_name, build)]

            for unit, count in composition.items():
                if count > 0:
                    player_army.append(make_unit_stack(unit, count))

            result = simulate(
                player_army=player_army,
                enemy_army=enemy_army,
                build=build,
                general_name=general_name,
                deterministic=True,
            )

            if result["win"] and losses_are_acceptable(
                result["net_losses"],
                acceptable_losses,
            ):
                deterministic_results.append({
                    "general_name": general_name,
                    "build": build,
                    "acceptable_losses": acceptable_losses,
                    "deterministic_score": score_result(result, composition),
                    "composition": composition,
                    "deterministic_result": result,
                })

        deterministic_results.sort(key=lambda item: item["deterministic_score"])

        print(f"Compositions testées : {tested}")
        print(f"Compositions retenues déterministes : {len(deterministic_results)}")

        if not deterministic_results:
            continue

        candidates = deterministic_results[:TOP_CANDIDATES_FOR_MONTE_CARLO]

        for item in candidates:
            mc = monte_carlo_validate(
                general_name=item["general_name"],
                build=item["build"],
                acceptable_losses=item["acceptable_losses"],
                composition=item["composition"],
                enemy_army=enemy_army,
                runs=MONTE_CARLO_RUNS,
            )

            all_validated_results.append({
                "final_score": monte_carlo_score(mc),
                "general_name": item["general_name"],
                "composition": item["composition"],
                "acceptable_losses": item["acceptable_losses"],
                "deterministic_score": item["deterministic_score"],
                "deterministic_result": item["deterministic_result"],
                "monte_carlo": mc,
            })

    if not all_validated_results:
        print("Aucune attaque valide trouvée.")
        return

    all_validated_results.sort(key=lambda item: item["final_score"])
    best = all_validated_results[0]

    print()
    print("=" * 80)
    print("MEILLEURE ATTAQUE TOUS GÉNÉRAUX")
    print("=" * 80)
    print("Général:", best["general_name"])
    print("Composition attaquante:", format_comp(best["general_name"], best["composition"]))
    print("Nombre d’unités envoyées:", composition_size(best["composition"]))
    print("Pertes acceptables:", best["acceptable_losses"])
    print("Score déterministe:", best["deterministic_score"])
    print("Score final Monte Carlo:", best["final_score"])

    print()
    print("Résultat déterministe:")
    for key, value in best["deterministic_result"].items():
        print(f"  {key}: {value}")

    print()
    print("Validation Monte Carlo:")
    for key, value in best["monte_carlo"].items():
        print(f"  {key}: {value}")

    print("=" * 80)


if __name__ == "__main__":
    main()
