"use strict";

const OFFICIAL_CLASS_SOURCES = [
    { breed_id: 3, name: "Enutrof", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/3-enutrof" },
    { breed_id: 6, name: "Ecaflip", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/6-ecaflip" },
    { breed_id: 8, name: "Iop", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/8-iop" },
    { breed_id: 10, name: "Sadida", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/10-sadida" },
    { breed_id: 11, name: "Sacrier", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/11-sacrier" },
    { breed_id: 1, name: "Feca", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/1-feca" },
    { breed_id: 2, name: "Osamodas", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/2-osamodas" },
    { breed_id: 4, name: "Sram", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/4-sram" },
    { breed_id: 9, name: "Cra", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/9-cra" },
    { breed_id: 12, name: "Pandawa", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/12-pandawa" },
    { breed_id: 14, name: "Masqueraider", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/14-masqueraider" },
    { breed_id: 5, name: "Xelor", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/5-xelor" },
    { breed_id: 7, name: "Eniripsa", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/7-eniripsa" },
    { breed_id: 13, name: "Rogue", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/13-rogue" },
    { breed_id: 15, name: "Foggernaut", url: "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/15-foggernaut" },
];

function cleanText(value) {
    return (value || "").replace(/\s+/g, " ").trim();
}

function numericValue(text) {
    if (/^unlimited$/i.test(text)) return null;
    const match = text.match(/-?\d+/);
    return match ? Number(match[0]) : text;
}

function characteristic(label, value) {
    const boolean = /^yes$/i.test(value) ? true : /^no$/i.test(value) ? false : null;
    const fields = {
        "Probability of Critical Hit": ["critical_hit_percent", numericValue(value)],
        "Uses per turn": ["max_cast_per_turn", numericValue(value)],
        "Uses per turn per player": ["max_cast_per_target", numericValue(value)],
        "Uses per target": ["max_cast_per_target", numericValue(value)],
        "Number of casts per turn per target": ["max_cast_per_target", numericValue(value)],
        "Cooldown": ["cooldown", numericValue(value)],
        "Recast interval": ["cooldown", numericValue(value)],
        "Global recast interval": ["global_cooldown", numericValue(value)],
        "Area of Effect": ["area_of_effect", value],
        "Modifiable range": ["modifiable_range", boolean],
        "Line of sight": ["cast_test_los", boolean],
        "Cast in a straight line only": ["cast_in_line", boolean],
        "Cast diagonally only": ["cast_in_diagonal", boolean],
        "Target necessary": ["target_required", boolean],
        "Free cell required": ["need_free_cell", boolean],
    };
    const mapped = fields[label];
    return mapped ? { [mapped[0]]: mapped[1] } : {};
}

function parseEffect(text) {
    const effect = { text };
    const damage = text.match(/(\d+)(?:\s+to\s+(\d+))?\s+\((Neutral|Earth|Fire|Water|Air) damage\)/i);
    if (damage) {
        effect.damage = {
            element: damage[3].toUpperCase(),
            min_roll: Number(damage[1]),
            max_roll: Number(damage[2] || damage[1]),
        };
    }
    const heal = text.match(/(\d+)(?:\s+to\s+(\d+))?\s+\(HP restored\)/i);
    if (heal) {
        effect.heal = { min_roll: Number(heal[1]), max_roll: Number(heal[2] || heal[1]) };
    }
    return effect;
}

function parseSpellLevel(doc, sourceUrl, level, spellLevelId) {
    const panel = doc.querySelector(".ak-spell-details");
    if (!panel) throw new Error(`No spell details at ${sourceUrl}`);
    const header = cleanText(panel.querySelector(".ak-spell-po-pa")?.textContent);
    const range = header.match(/(\d+)(?:\s*-\s*(\d+))?\s+Range\s*\/\s*(\d+)AP/i);
    if (!range) throw new Error(`Unrecognised range/AP header ${JSON.stringify(header)} at ${sourceUrl}`);
    const normalPanel = panel.querySelector(".ak-spell-details-effects:not(.ak-spell-details-critical)");
    const criticalPanel = panel.querySelector(".ak-spell-details-critical");
    const effects = node => [...(node?.querySelectorAll(".ak-list-element") || [])]
        .map(element => parseEffect(cleanText(element.querySelector(".ak-title")?.textContent || element.textContent)));
    const rawCharacteristics = {};
    const characteristics = {};
    for (const element of panel.querySelectorAll(".ak-spell-details-other .ak-list-element")) {
        const label = cleanText(element.querySelector(".ak-title")?.textContent);
        const value = cleanText(element.querySelector(".ak-aside")?.textContent);
        if (!label) continue;
        rawCharacteristics[label] = value;
        Object.assign(characteristics, characteristic(label, value));
    }
    const required = cleanText(panel.querySelector(".ak-spell-required-lvl")?.textContent).match(/\d+/);
    return {
        level,
        spell_level_id: spellLevelId,
        source_url: sourceUrl,
        required_level: required ? Number(required[0]) : null,
        ap_cost: Number(range[3]),
        min_range: Number(range[1]),
        max_range: Number(range[2] || range[1]),
        description: cleanText(panel.querySelector(".ak-spell-description")?.textContent),
        normal_effects: effects(normalPanel),
        critical_effects: effects(criticalPanel),
        characteristics,
        raw_characteristics: rawCharacteristics,
    };
}

async function fetchDocument(url) {
    let response;
    for (let attempt = 0; attempt < 3; attempt += 1) {
        response = await fetch(url, { credentials: "same-origin" });
        if (response.ok) break;
        if (attempt === 2) throw new Error(`${response.status} ${url}`);
        await new Promise(resolve => setTimeout(resolve, 1500 * (attempt + 1)));
    }
    const document = new DOMParser().parseFromString(await response.text(), "text/html");
    await new Promise(resolve => setTimeout(resolve, 100));
    return document;
}

async function mapLimit(items, limit, fn) {
    const output = new Array(items.length);
    let next = 0;
    async function worker() {
        while (next < items.length) {
            const index = next++;
            output[index] = await fn(items[index]);
        }
    }
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    return output;
}

async function extractOfficialClass(source) {
    const classDocument = await fetchDocument(source.url);
    const spells = [...classDocument.querySelectorAll("a.ak-ajaxloader[spell-id][href*=\"spells/details\"]")]
        .map(anchor => {
            const url = new URL(anchor.href, source.url);
            const initialLevelId = Number(url.searchParams.get("level"));
            return {
                id: Number(anchor.getAttribute("spell-id")),
                breed_id: source.breed_id,
                breed: source.name,
                name_en: cleanText(anchor.querySelector("img")?.alt || anchor.textContent),
                icon_url: anchor.querySelector("img")?.src || null,
                source_url: url.href,
                initial_spell_level_id: initialLevelId,
            };
        });
    const uniqueSpells = [...new Map(spells.map(spell => [spell.id, spell])).values()];
    return mapLimit(uniqueSpells, 1, async spell => {
        const firstDocument = await fetchDocument(spell.source_url);
        const selector = firstDocument.querySelector(".ak-spell-details-level-selector");
        const requests = [{ level: 1, url: spell.source_url, spellLevelId: spell.initial_spell_level_id, doc: firstDocument }];
        for (const anchor of selector?.querySelectorAll("a[href*=\"spells/details\"]") || []) {
            const level = Number(cleanText(anchor.textContent));
            requests.push({ level, url: anchor.href, spellLevelId: null, doc: null });
        }
        const levels = await mapLimit(requests, 1, async request => {
            const doc = request.doc || await fetchDocument(request.url);
            return parseSpellLevel(doc, request.url, request.level, request.spellLevelId);
        });
        return { ...spell, levels };
    });
}

async function extractOfficialSpells(sources = OFFICIAL_CLASS_SOURCES) {
    const classes = [];
    for (const source of sources) {
        const spells = await extractOfficialClass(source);
        classes.push({ breed_id: source.breed_id, name: source.name, source_url: source.url, spells });
    }
    return {
        source: "Dofus Touch official encyclopedia",
        locale: "en",
        retrieved_on: new Date().toISOString().slice(0, 10),
        classes,
    };
}

if (typeof module !== "undefined") {
    module.exports = { OFFICIAL_CLASS_SOURCES, cleanText, characteristic, parseEffect, parseSpellLevel, extractOfficialClass, extractOfficialSpells };
}
