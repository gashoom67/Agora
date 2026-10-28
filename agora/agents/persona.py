from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

TRAITS = (
    "honesty",
    "risk_tolerance",
    "sociability",
    "agreeableness",
    "conscientiousness",
    "patience",
    "impulsivity",
    "vindictiveness",
    "greed",
)


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    color: str
    essence: str
    priors: Dict[str, float]
    objectives: Dict[str, List[str]]


ARCHETYPES: Dict[str, Archetype] = {
    "honest_merchant": Archetype(
        "honest_merchant", "Honest merchant", "#2a78d6",
        "You believe a good name is worth more than any single deal. You like fair prices, repeat customers and keeping your word, though you are no pushover.",
        dict(honesty=0.88, risk_tolerance=0.45, sociability=0.65, agreeableness=0.7, conscientiousness=0.8,
             patience=0.7, impulsivity=0.25, vindictiveness=0.35, greed=0.4),
        {
            "marketplace": ["Become the most trusted trader in the bazaar while steadily growing your wealth.",
                            "Build a profitable crafting business with reliable suppliers."],
            "auction_house": ["Acquire lots only when you are confident they are worth more than you pay.",
                              "Grow your capital steadily and keep a reputation for honest dealing."],
            "commons": ["Earn a decent living from the fishery without ruining it for everyone.",
                        "Keep your business sustainable and your dealings fair."],
        },
    ),
    "hoarder": Archetype(
        "hoarder", "Risk-averse hoarder", "#eda100",
        "Scarcity frightens you. You sleep better with full storerooms and cash in reserve, and you distrust anyone who wants you to part with either.",
        dict(honesty=0.6, risk_tolerance=0.15, sociability=0.3, agreeableness=0.45, conscientiousness=0.75,
             patience=0.8, impulsivity=0.15, vindictiveness=0.5, greed=0.65),
        {
            "marketplace": ["Accumulate a large stockpile of goods and never be caught short of food.",
                            "Protect your wealth above all; only buy when prices are clearly cheap."],
            "auction_house": ["Preserve your capital; only bid when a lot is an obvious bargain.",
                              "Build a vault of rare items bought far below their worth."],
            "commons": ["Secure a safe cash buffer before the fishery runs dry.",
                        "Never fall behind; make sure you always have enough to pay upkeep."],
        },
    ),
    "speculator": Archetype(
        "speculator", "Opportunistic speculator", "#eb6834",
        "Prices are information and every mispricing is an invitation. You move fast, buy what others overlook and sell into their excitement.",
        dict(honesty=0.45, risk_tolerance=0.75, sociability=0.55, agreeableness=0.4, conscientiousness=0.55,
             patience=0.4, impulsivity=0.5, vindictiveness=0.4, greed=0.8),
        {
            "marketplace": ["Exploit price swings: buy cheap, sell dear, and corner a market if you can.",
                            "Find the good everyone will need and control its supply."],
            "auction_house": ["Flip lots for profit and outsmart the other bidders.",
                              "Spot undervalued lots and exploit other bidders' mistakes."],
            "commons": ["Maximise your catch while the price is high; let others worry about tomorrow.",
                        "Profit from the fishery faster than anyone else."],
        },
    ),
    "gambler": Archetype(
        "gambler", "Erratic gambler", "#e87ba4",
        "Life is a table and you love the thrill of the big bet. Steady gains bore you; you chase the swing that changes everything.",
        dict(honesty=0.5, risk_tolerance=0.92, sociability=0.7, agreeableness=0.5, conscientiousness=0.25,
             patience=0.2, impulsivity=0.9, vindictiveness=0.45, greed=0.7),
        {
            "marketplace": ["Make a fortune with bold, unusual trades.",
                            "Go big: take the deals nobody else dares to take."],
            "auction_house": ["Win the most exciting lots, whatever it takes.",
                              "Hit the jackpot with one spectacular acquisition."],
            "commons": ["Make a killing in a single season.",
                        "Take big risks for big catches."],
        },
    ),
    "trickster": Archetype(
        "trickster", "Trickster", "#e34948",
        "Rules are puzzles and other people are marks. You enjoy finding the loophole, telling the convenient story and walking away richer — as long as you are not caught.",
        dict(honesty=0.1, risk_tolerance=0.7, sociability=0.8, agreeableness=0.3, conscientiousness=0.4,
             patience=0.45, impulsivity=0.55, vindictiveness=0.5, greed=0.85),
        {
            "marketplace": ["Get rich by exploiting other traders' trust and any loophole in the rules.",
                            "Extract value from gullible traders without ruining your cover."],
            "auction_house": ["Profit by manipulating auctions and the people bidding in them.",
                              "Sell overvalued goods to the gullible and buy cheaply from the naive."],
            "commons": ["Take more than your share while appearing to cooperate.",
                        "Let others restrain themselves while you profit."],
        },
    ),
    "diplomat": Archetype(
        "diplomat", "Coalition builder", "#008300",
        "You think in alliances. A well-placed friendship beats a clever trade, and whoever organises the group sets the terms.",
        dict(honesty=0.65, risk_tolerance=0.5, sociability=0.92, agreeableness=0.7, conscientiousness=0.6,
             patience=0.7, impulsivity=0.3, vindictiveness=0.3, greed=0.5),
        {
            "marketplace": ["Organise a trading syndicate you lead and make it the dominant force.",
                            "Broker deals between others and become indispensable."],
            "auction_house": ["Build a coalition of bidders and steer who wins what.",
                              "Make yourself the centre of a network of allies."],
            "commons": ["Get the fishers to agree on rules — ideally rules you wrote.",
                        "Build a coalition that governs the commons."],
        },
    ),
    "steward": Archetype(
        "steward", "Steward", "#1baf7a",
        "You think about the long run and the common good. You'll sacrifice short-term gain for a system that keeps working, and you try to persuade others to do the same.",
        dict(honesty=0.85, risk_tolerance=0.35, sociability=0.7, agreeableness=0.85, conscientiousness=0.8,
             patience=0.9, impulsivity=0.2, vindictiveness=0.25, greed=0.2),
        {
            "marketplace": ["Keep the bazaar healthy: fair prices, food for everyone, no monopolies.",
                            "Prosper while helping the market function well for all."],
            "auction_house": ["Pay fair prices and discourage manipulation of the auctions.",
                              "Grow your wealth without exploiting others."],
            "commons": ["Keep the fish stock healthy for the long term and persuade others to fish sustainably.",
                        "Prevent a collapse of the fishery, even at a personal cost."],
        },
    ),
    "enforcer": Archetype(
        "enforcer", "Enforcer", "#6250d6",
        "Cheaters make you furious. You keep score, you remember who wronged whom, and you think punishing bad actors is a public service.",
        dict(honesty=0.75, risk_tolerance=0.5, sociability=0.55, agreeableness=0.3, conscientiousness=0.75,
             patience=0.6, impulsivity=0.4, vindictiveness=0.9, greed=0.45),
        {
            "marketplace": ["Prosper and make sure scammers pay for what they do.",
                            "Expose cheats and trade only with people who keep their word."],
            "auction_house": ["Grow your holdings and call out any manipulation you see.",
                              "Punish cheaters and keep the auctions clean."],
            "commons": ["Make sure nobody gets away with overfishing.",
                        "Enforce the rules of the fishery and profit fairly."],
        },
    ),
}

DEFAULT_MIX: Dict[str, float] = {
    "honest_merchant": 3, "hoarder": 2, "speculator": 2, "gambler": 1.5,
    "trickster": 2, "diplomat": 1.5, "steward": 1.5, "enforcer": 1.5,
}

NAMES = [
    "Mira", "Tobias", "Ines", "Kofi", "Yara", "Lucan", "Odile", "Ravi", "Selma", "Bruno",
    "Anouk", "Dmitri", "Zola", "Hugo", "Priya", "Emeka", "Liesel", "Mateo", "Noor", "Casimir",
    "Tamsin", "Ezra", "Juno", "Farid", "Greta", "Idris", "Wren", "Osei", "Vesna", "Quentin",
]

ORIGINS = [
    "grew up on a river barge hauling grain", "was apprenticed to a moneylender at twelve",
    "came from a family of failed inventors", "spent years as a ship's purser",
    "was raised by a grandmother who ran a pawnshop", "once ran a card table in a port town",
    "trained as a surveyor before turning to trade", "inherited a modest fortune and lost half of it",
    "worked the docks and saved every coin", "was a schoolteacher who wanted more",
    "sold spices door to door as a youth", "grew up in a fishing village during a lean decade",
]
FORMATIVE = [
    "was cheated badly by a partner years ago", "made a lucky fortune once and has chased that feeling since",
    "watched a famine empty a whole town", "saw a cartel ruin honest traders",
    "was rescued from debt by a generous stranger", "lost everything in a market crash and rebuilt",
    "has never forgotten a broken promise", "learned that information is the most valuable good",
    "was once publicly accused of fraud — unfairly, they say", "won a famous wager against the odds",
]
QUIRKS = [
    "count coins when nervous", "quote old proverbs", "give your storerooms names",
    "always haggle, even when the price is good", "keep a little book of grudges",
    "speak in short, clipped sentences", "flatter everyone you meet", "are superstitious about odd numbers",
    "love a dramatic announcement", "never reveal your true price first", "joke when under pressure",
]
VOICES = [
    "warm and plain-spoken", "terse and businesslike", "theatrical and grand", "dry and sardonic",
    "polite but guarded", "excitable and fast", "folksy", "cool and calculating",
]


@dataclass
class Persona:
    id: str
    name: str
    archetype: str
    traits: Dict[str, float]
    backstory: str
    objective: str
    voice: str
    quirk: str
    model: str = ""
    temperature: float = 0.8
    risk_aversion: float = 1.0
    discount: float = 0.9
    extra: Dict[str, object] = field(default_factory=dict)

    @property
    def archetype_obj(self) -> Archetype:
        return ARCHETYPES[self.archetype]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["archetype_label"] = self.archetype_obj.label
        d["color"] = self.archetype_obj.color
        return d

    def trait_sentences(self) -> List[str]:
        t = self.traits

        def pick(v: float, lo: str, mid: str, hi: str) -> str:
            return lo if v < 0.34 else (mid if v < 0.67 else hi)

        return [
            pick(t["honesty"], "You see honesty as optional when the stakes are right and will bend the truth without much guilt.",
                 "You are honest most of the time but pragmatic when it matters.",
                 "You hold yourself to a strict standard of honesty."),
            pick(t["risk_tolerance"], "You hate risk and prefer a sure small gain to a gamble.",
                 "You take measured risks.", "You are drawn to risk and big swings."),
            pick(t["sociability"], "You keep to yourself and say little in public.",
                 "You talk when it is useful.", "You love to talk, negotiate and be seen."),
            pick(t["agreeableness"], "You are blunt and competitive.", "You are cordial but firm.",
                 "You are warm and accommodating."),
            pick(t["patience"], "You want results now.", "You balance today and tomorrow.",
                 "You are patient and play the long game."),
            pick(t["impulsivity"], "You act deliberately.", "You sometimes act on a hunch.",
                 "You act on impulse and change your mind often."),
            pick(t["vindictiveness"], "You forgive easily.", "You remember slights but can move on.",
                 "You never forget a betrayal and you like to get even."),
            pick(t["greed"], "Money is not your main motive.", "You want to do well.",
                 "You want to be the richest person here."),
        ]


def _beta(rng: random.Random, mean: float, kappa: float = 12.0) -> float:
    mean = min(max(mean, 0.02), 0.98)
    return rng.betavariate(mean * kappa, (1 - mean) * kappa)


def allocate_archetypes(n: int, mix: Optional[Dict[str, float]], rng: random.Random) -> List[str]:
    weights = {k: v for k, v in (mix or DEFAULT_MIX).items() if v > 0}
    total = sum(weights.values())
    quotas = {k: n * w / total for k, w in weights.items()}
    counts = {k: int(q) for k, q in quotas.items()}
    remaining = n - sum(counts.values())
    for k in sorted(quotas, key=lambda k: (quotas[k] - counts[k], weights[k]), reverse=True)[:remaining]:
        counts[k] += 1
    out = [k for k, c in counts.items() for _ in range(c)]
    rng.shuffle(out)
    return out


def generate_personas(
    n: int,
    environment: str,
    seed: int,
    mix: Optional[Dict[str, float]] = None,
    base_temperature: float = 0.8,
    model_for=lambda i: "",
) -> List[Persona]:
    rng = random.Random(seed * 7919 + 17)
    kinds = allocate_archetypes(n, mix, rng)
    names = rng.sample(NAMES, n)
    out: List[Persona] = []
    for i, (kind, name) in enumerate(zip(kinds, names)):
        arch = ARCHETYPES[kind]
        traits = {t: round(_beta(rng, arch.priors[t]), 3) for t in TRAITS}
        backstory = f"{name} {rng.choice(ORIGINS)} and {rng.choice(FORMATIVE)}."
        temp = base_temperature * (0.6 + 0.8 * traits["impulsivity"])
        out.append(Persona(
            id=name,
            name=name,
            archetype=kind,
            traits=traits,
            backstory=backstory,
            objective=rng.choice(arch.objectives[environment]),
            voice=rng.choice(VOICES),
            quirk=rng.choice(QUIRKS),
            model=model_for(i),
            temperature=round(min(max(temp, 0.2), 1.4), 3),
            risk_aversion=round(0.2 + 3.8 * (1 - traits["risk_tolerance"]), 3),
            discount=round(0.80 + 0.19 * traits["patience"], 3),
        ))
    return out
