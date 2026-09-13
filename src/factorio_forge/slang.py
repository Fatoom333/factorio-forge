"""How players talk about things, mapped to the names the base game gives them.

"Красные колбы", "green circuits", "синяя лента", "мазут" -- none of these is
a word in any name the game shows, and some official Russian names are far
from how players speak (heavy oil is «Мазут», steel plate «Стальная балка»,
the battery «Аккумулятор» while players call the accumulator that). Working
the slang out afresh on every request is slow and uneven, so the common terms
are written down here.

This is knowledge of words, not of the game. The names on the right are the
base game's and Space Age's internal names -- the ones mods keep even when they
rename what is shown (Krastorio 2's red science is still
`automation-science-pack`). Whether a name exists in the active mod set, what
it is called there and whether it is unlocked is decided from the data every
time, never from this table. A term the table does not know is not a failure:
the model translates it, and adding it here saves the next one the trouble.

Each term lists names most likely first; `note` says when a phrase is
genuinely ambiguous or means something that changed between game versions,
which is when the player should be asked rather than guessed for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    phrases: tuple[str, ...]
    names: tuple[str, ...]
    note: str = ""


def _t(phrases: str, names: str, note: str = "") -> Term:
    return Term(
        tuple(p.strip() for p in phrases.split(",") if p.strip()),
        tuple(names.split()),
        note,
    )


BASE_SCIENCE = (
    "automation-science-pack logistic-science-pack military-science-pack chemical-science-pack "
    "production-science-pack utility-science-pack space-science-pack"
)

TERMS: tuple[Term, ...] = (
    # --- science -----------------------------------------------------------
    _t("красные колбы, красная наука, красные банки, красные пакеты, красные карты, "
       "red science, red packs, red flasks, automation science", "automation-science-pack"),
    _t("зелёные колбы, зелёная наука, зелёные банки, зелёные пакеты, зелёные карты, "
       "green science, green packs, logistic science, logistics science", "logistic-science-pack"),
    _t("чёрные колбы, серые колбы, военные колбы, чёрная наука, серая наука, военная наука, "
       "black science, grey science, gray science, military science", "military-science-pack"),
    _t("синие колбы, синяя наука, синие банки, синие карты, химические колбы, химическая наука, "
       "blue science, blue packs, chemical science", "chemical-science-pack"),
    _t("фиолетовые колбы, фиолетовая наука, пурпурные колбы, производственная наука, "
       "purple science, production science", "production-science-pack"),
    _t("жёлтые колбы, жёлтая наука, вспомогательная наука, "
       "yellow science, utility science", "utility-science-pack"),
    _t("белые колбы, белая наука, космическая наука, "
       "white science, space science", "space-science-pack"),
    _t("оранжевые колбы, металлургическая наука, orange science, metallurgic science", "metallurgic-science-pack"),
    _t("розовые колбы, электромагнитная наука, pink science, electromagnetic science", "electromagnetic-science-pack"),
    _t("сельскохозяйственная наука, аграрная наука, agricultural science, ag science", "agricultural-science-pack"),
    _t("криогенная наука, cryogenic science, cryo science", "cryogenic-science-pack"),
    _t("прометиевая наука, promethium science", "promethium-science-pack"),
    _t("колбы, наука, исследовательские пакеты, технологические карты, science packs, science",
       BASE_SCIENCE, "which colour? all science packs match"),

    # --- intermediates -----------------------------------------------------
    _t("зелёные схемы, зелёные микросхемы, зелёные платы, электросхемы, "
       "green circuits, green chips, electronic circuits", "electronic-circuit"),
    _t("красные схемы, красные микросхемы, красные платы, продвинутые схемы, "
       "red circuits, red chips, advanced circuits", "advanced-circuit"),
    _t("синие схемы, синие микросхемы, синие платы, процессоры, "
       "blue circuits, blue chips, processing units", "processing-unit"),
    _t("схемы, микросхемы, circuits, chips", "electronic-circuit advanced-circuit processing-unit",
       "which circuit? green, red or blue"),
    _t("шестерёнки, шестерни, шестерёнка, gears, gear wheels", "iron-gear-wheel"),
    _t("медный провод, медные провода, провода, кабель, copper wire, copper cable, cables", "copper-cable",
       "'wire' can also mean a red or green circuit wire"),
    _t("лдс, малоплотные конструкции, конструкции малой плотности, lds, low density structure, low density",
       "low-density-structure"),
    _t("rcu, блок управления ракетой, rocket control unit", "rocket-control-unit",
       "removed in Factorio 2.0; exists only under 1.1 or a mod that restores it"),
    _t("двигатели, моторы, engines, engine units", "engine-unit"),
    _t("электродвигатели, электромоторы, electric engines, electric engine units", "electric-engine-unit"),
    _t("каркасы роботов, каркасы дронов, рамы роботов, robot frames, flying robot frames", "flying-robot-frame"),
    _t("сталь, стальные плиты, стальные слитки, steel, steel plates", "steel-plate"),
    _t("железо, железные плиты, iron, iron plates", "iron-plate"),
    _t("медь, медные плиты, copper, copper plates", "copper-plate"),
    _t("пластик, пластмасса, plastic", "plastic-bar"),
    _t("кирпичи, каменные кирпичи, bricks, stone bricks", "stone-brick"),
    _t("батареи, батарейки, batteries", "battery"),
    _t("аккумуляторы, accumulators", "accumulator battery",
       "players usually mean the accumulator block, but in the base game's Russian "
       "«Аккумулятор» is the battery"),
    _t("сера, sulfur, sulphur", "sulfur"),
    _t("ракетное топливо, rocket fuel", "rocket-fuel"),
    _t("твёрдое топливо, solid fuel", "solid-fuel"),
    _t("отсыпка, засыпка, landfill", "landfill"),
    _t("взрывчатка, explosives", "explosives"),
    _t("бетон, concrete", "concrete"),
    _t("железобетон, refined concrete", "refined-concrete"),

    # --- fluids ------------------------------------------------------------
    _t("тяжёлая нефть, тяжёлое масло, мазут, heavy oil", "heavy-oil"),
    _t("лёгкая нефть, лёгкое масло, дизель, light oil", "light-oil"),
    _t("газ, петрогаз, нефтяной газ, petroleum gas, petroleum, petgas, pgas", "petroleum-gas"),
    _t("смазка, lube, lubricant", "lubricant"),
    _t("кислота, серная кислота, acid, sulfuric acid", "sulfuric-acid"),
    _t("нефть, сырая нефть, crude, crude oil", "crude-oil"),

    # --- belts -------------------------------------------------------------
    _t("жёлтая лента, жёлтые ленты, жёлтый конвейер, жёлтые белты, yellow belt, yellow belts", "transport-belt"),
    _t("красная лента, красные ленты, красный конвейер, красные белты, red belt, red belts", "fast-transport-belt"),
    _t("синяя лента, синие ленты, синий конвейер, синие белты, blue belt, blue belts", "express-transport-belt"),
    _t("зелёная лента, зелёные ленты, зелёный конвейер, зелёные белты, green belt, green belts",
       "turbo-transport-belt", "Space Age; in Krastorio 2 the tiers above blue are its own belts"),
    _t("ленты, конвейеры, белты, belts", "transport-belt fast-transport-belt express-transport-belt turbo-transport-belt",
       "which tier?"),
    _t("жёлтые подземки, yellow undergrounds", "underground-belt"),
    _t("красные подземки, red undergrounds", "fast-underground-belt"),
    _t("синие подземки, blue undergrounds", "express-underground-belt"),
    _t("зелёные подземки, green undergrounds", "turbo-underground-belt"),
    _t("подземки, подземные конвейеры, undergrounds, underground belts",
       "underground-belt fast-underground-belt express-underground-belt turbo-underground-belt",
       "which tier? 'подземка' can also mean a pipe to ground"),
    _t("жёлтый сплиттер, жёлтый разделитель, yellow splitter", "splitter"),
    _t("красный сплиттер, красный разделитель, red splitter", "fast-splitter"),
    _t("синий сплиттер, синий разделитель, blue splitter", "express-splitter"),
    _t("зелёный сплиттер, зелёный разделитель, green splitter", "turbo-splitter"),
    _t("сплиттеры, разделители, splitters", "splitter fast-splitter express-splitter turbo-splitter", "which tier?"),
    _t("подземная труба, труба в землю, pipe to ground, underground pipe", "pipe-to-ground"),

    # --- inserters ---------------------------------------------------------
    _t("жёлтый манипулятор, жёлтые манипуляторы, обычный манипулятор, yellow inserter, basic inserter", "inserter"),
    _t("красный манипулятор, красные манипуляторы, длинный манипулятор, длиннорукий манипулятор, "
       "red inserter, long inserter, long handed inserter", "long-handed-inserter"),
    _t("синий манипулятор, синие манипуляторы, быстрый манипулятор, blue inserter, fast inserter", "fast-inserter"),
    _t("зелёный манипулятор, зелёные манипуляторы, массовый манипулятор, стак манипулятор, стековый манипулятор, "
       "green inserter, bulk inserter", "bulk-inserter",
       "in 1.1 this was the 'stack inserter'; in 2.0 that name belongs to a different Space Age inserter"),
    _t("стак инсертер, stack inserter", "bulk-inserter stack-inserter",
       "1.1 'stack inserter' is 2.0's bulk inserter; 2.0's stack inserter is the Space Age one"),
    _t("пакетный манипулятор", "stack-inserter"),
    _t("угольный манипулятор, твердотопливный манипулятор, burner inserter", "burner-inserter"),
    _t("фильтр манипулятор, фильтрующий манипулятор, filter inserter", "fast-inserter bulk-inserter",
       "removed in 2.0: fast and bulk inserters take filters themselves"),
    _t("манипуляторы, inserters", "inserter long-handed-inserter fast-inserter bulk-inserter", "which kind?"),

    # --- machines ----------------------------------------------------------
    _t("ассемблер 1, сборщик 1, автомат 1, assembler 1, am1", "assembling-machine-1"),
    _t("ассемблер 2, сборщик 2, автомат 2, assembler 2, am2", "assembling-machine-2"),
    _t("ассемблер 3, сборщик 3, автомат 3, assembler 3, am3", "assembling-machine-3"),
    _t("ассемблеры, сборщики, сборочные автоматы, assemblers, assembling machines",
       "assembling-machine-3 assembling-machine-2 assembling-machine-1", "which tier?"),
    _t("каменная печь, каменные печи, stone furnace", "stone-furnace"),
    _t("стальная печь, стальные печи, steel furnace", "steel-furnace"),
    _t("электропечь, электропечи, электрическая печь, electric furnace", "electric-furnace"),
    _t("печи, печки, furnaces", "electric-furnace steel-furnace stone-furnace", "which furnace?"),
    _t("нпз, нефтезавод, нефтепереработка, refinery, oil refinery", "oil-refinery"),
    _t("химзавод, химка, химзаводы, chem plant, chemical plant", "chemical-plant"),
    _t("качалка, качалки, нефтекачалка, станок качалка, pumpjack, pumpjacks", "pumpjack"),
    _t("насос, прибрежный насос, водяной насос, offshore pump", "offshore-pump",
       "'насос' can also mean the inline pump («Помпа»)"),
    _t("помпа, pump", "pump"),
    _t("центрифуга, centrifuge", "centrifuge"),
    _t("лаба, лабы, лаборатория, лаборатории, lab, labs", "lab"),
    _t("ракетная шахта, сило, silo, rocket silo", "rocket-silo"),
    _t("маяк, маяки, beacon, beacons", "beacon"),
    _t("литейка, литейная, foundry", "foundry"),
    _t("эм завод, электромагнитный завод, em plant, electromagnetic plant", "electromagnetic-plant"),
    _t("криозавод, криогенный завод, cryo plant, cryogenic plant", "cryogenic-plant"),
    _t("биокамера, biochamber", "biochamber"),
    _t("переработчик, ресайклер, recycler", "recycler"),

    # --- modules -----------------------------------------------------------
    _t("прод модули, модули продуктивности, продуктивки, prod modules, productivity modules",
       "productivity-module-3 productivity-module-2 productivity-module", "which tier?"),
    _t("модули скорости, скоростные модули, speed modules", "speed-module-3 speed-module-2 speed-module", "which tier?"),
    _t("модули эффективности, энергомодули, efficiency modules, energy efficiency modules",
       "efficiency-module-3 efficiency-module-2 efficiency-module", "which tier?"),
    _t("модули качества, quality modules", "quality-module-3 quality-module-2 quality-module", "which tier?"),

    # --- power and logistics network ---------------------------------------
    _t("малый столб, деревянный столб, маленький столб, small pole, small electric pole", "small-electric-pole"),
    _t("средний столб, средние столбы, medium pole, medium electric pole", "medium-electric-pole"),
    _t("большой столб, большие столбы, big pole, big electric pole", "big-electric-pole"),
    _t("подстанция, подстанции, substation, substations", "substation"),
    _t("столбы, опоры, poles", "medium-electric-pole small-electric-pole big-electric-pole substation", "which pole?"),
    _t("солнечные панели, солнечные батареи, солнце, solar, solar panels", "solar-panel"),
    _t("робопорт, робопорты, дронстанция, roboport, roboports", "roboport"),
    _t("строительные дроны, строительные роботы, стройботы, construction bots, construction robots", "construction-robot"),
    _t("транспортные дроны, логистические роботы, логистические дроны, logistic bots, logistic robots", "logistic-robot"),
    _t("реквестер, сундук запроса, requester, requester chest", "requester-chest"),
    _t("пассивный провайдер, сундук пассивного снабжения, passive provider", "passive-provider-chest"),
    _t("активный провайдер, сундук активного снабжения, active provider", "active-provider-chest"),
    _t("буферный сундук, буфер, buffer chest", "buffer-chest"),
    _t("сундук хранения, storage chest", "storage-chest"),

    # --- nuclear and military ----------------------------------------------
    _t("уран 235, u235, 235", "uranium-235"),
    _t("уран 238, u238, 238", "uranium-238"),
    _t("твэлы, топливные элементы, fuel cells, uranium fuel cells", "uranium-fuel-cell"),
    _t("жёлтые патроны, обычные патроны, yellow ammo, firearm magazine", "firearm-magazine"),
    _t("красные патроны, бронебойные патроны, red ammo, piercing ammo", "piercing-rounds-magazine"),
    _t("зелёные патроны, урановые патроны, green ammo, uranium ammo", "uranium-rounds-magazine"),
    _t("пулемётные турели, турели, gun turrets, turrets", "gun-turret"),
    _t("лазерные турели, лазерки, laser turrets", "laser-turret"),
    _t("стены, стена, walls", "stone-wall"),
)
