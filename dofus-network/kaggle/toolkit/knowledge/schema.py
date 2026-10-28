"""SQLite schema for the canonical knowledge store.

Every row carries its source so a conflict can be resolved instead of silently
overwritten. `verified_live` starts at 0 and is only raised by a capture that
shows the row behaving as stated.
"""

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE entities (
    kind           TEXT    NOT NULL,
    entity_id      INTEGER NOT NULL,
    name_fr        TEXT,
    name_en        TEXT,
    name_es        TEXT,
    name_de        TEXT,
    name_pt        TEXT,
    source         TEXT    NOT NULL,
    source_version TEXT,
    verified_live  INTEGER NOT NULL DEFAULT 0,
    data_json      TEXT,
    PRIMARY KEY (kind, entity_id)
);

CREATE TABLE quests (
    quest_id     INTEGER PRIMARY KEY,
    name_json    TEXT,
    level_min    INTEGER,
    level_max    INTEGER,
    category_id  INTEGER,
    is_dungeon   INTEGER NOT NULL DEFAULT 0,
    is_event     INTEGER NOT NULL DEFAULT 0,
    is_party     INTEGER NOT NULL DEFAULT 0,
    start_json   TEXT,
    source       TEXT NOT NULL,
    source_version TEXT,
    verified_live INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE quest_steps (
    quest_id       INTEGER NOT NULL,
    step_index     INTEGER NOT NULL,
    step_id        INTEGER,
    name_json      TEXT,
    description_json TEXT,
    optimal_level  INTEGER,
    source         TEXT NOT NULL,
    PRIMARY KEY (quest_id, step_index)
);

CREATE TABLE quest_objectives (
    objective_id   INTEGER PRIMARY KEY,
    quest_id       INTEGER NOT NULL,
    step_index     INTEGER NOT NULL,
    objective_index INTEGER NOT NULL,
    type_id        INTEGER,
    text_json      TEXT,
    map_id         INTEGER,
    coords_json    TEXT,
    params_json    TEXT,
    source         TEXT NOT NULL
);

CREATE INDEX idx_quest_objectives_quest ON quest_objectives (quest_id, step_index);

CREATE TABLE achievements (
    achievement_id INTEGER PRIMARY KEY,
    name_json      TEXT,
    description_json TEXT,
    points         INTEGER,
    level          INTEGER,
    category_id    INTEGER,
    source         TEXT NOT NULL
);

CREATE TABLE achievement_objectives (
    achievement_id INTEGER NOT NULL,
    objective_id   INTEGER NOT NULL,
    details_json   TEXT,
    source         TEXT NOT NULL,
    PRIMARY KEY (achievement_id, objective_id)
);

CREATE TABLE monsters (
    monster_id  INTEGER PRIMARY KEY,
    name_json   TEXT,
    is_boss     INTEGER NOT NULL DEFAULT 0,
    grades_json TEXT,
    source      TEXT NOT NULL
);

CREATE TABLE npcs (
    npc_id      INTEGER PRIMARY KEY,
    name_json   TEXT,
    actions_json TEXT,
    dialog_ids_json TEXT,
    source      TEXT NOT NULL
);

CREATE TABLE items (
    item_id       INTEGER PRIMARY KEY,
    type_id       INTEGER,
    level         INTEGER,
    name_json     TEXT,
    description_json TEXT,
    price         INTEGER,
    item_set_id   INTEGER,
    is_saleable   INTEGER,
    is_legendary  INTEGER,
    source        TEXT NOT NULL
);

CREATE TABLE guide_refs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    url        TEXT NOT NULL UNIQUE,
    title      TEXT,
    lang       TEXT,
    text       TEXT,
    license    TEXT,
    fetched_at TEXT
);

CREATE TABLE npc_placements (
    npc_id     INTEGER NOT NULL,
    map_id     INTEGER NOT NULL,
    cell       INTEGER,
    source     TEXT NOT NULL,
    verified_live INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (npc_id, map_id, cell)
);
"""
