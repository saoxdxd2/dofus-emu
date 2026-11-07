#!/usr/bin/env node
/* Map data exporter for the dofus-emu asset cache.
 *
 * Reads every cached map JSON (asset_cache/files/<host>/.../maps/<id>.json,
 * written by the Go relay/prefetcher) and emits a structured dataset:
 *
 *   maps.json        full records: id, direction neighbours, graph neighbours,
 *                    atlas size, tile gids, cell count, byte size
 *   graph.json       { "<id>": [neighbour ids] } for pathfinding/BFS
 *   maps-index.csv   one row per map for spreadsheets
 *
 * Usage:
 *   node tools/map-export.js [--cache asset_cache] [--out tools/out] [--pretty]
 *
 * If the mirror tree is missing, falls back to sniffing the hashed cache
 * blobs in <cache> directly. No dependencies, Node >= 14.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const NEIGHBOUR_FIELDS = ["topNeighbourId", "bottomNeighbourId", "leftNeighbourId", "rightNeighbourId"];

function usage() {
  console.log("usage: node tools/map-export.js [--cache <dir>] [--out <dir>] [--pretty]");
}

function parseArgs(argv) {
  const args = { cache: "asset_cache", out: path.join("tools", "out"), pretty: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--cache") args.cache = argv[++i];
    else if (a === "--out") args.out = argv[++i];
    else if (a === "--pretty") args.pretty = true;
    else if (a === "--help" || a === "-h") { usage(); process.exit(0); }
    else { console.error("unknown argument: " + a); usage(); process.exit(2); }
  }
  return args;
}

function walk(dir, cb) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return;
  }
  for (const ent of entries) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(full, cb);
    else if (ent.isFile()) cb(full);
  }
}

/* Mirror tree (real paths) unioned with hashed blobs (maps warmed by the
 * prefetcher are cached but only mirrored once served through the relay). */
function findMapFiles(cacheDir) {
  const out = [];
  const mirror = path.join(cacheDir, "files");
  if (fs.existsSync(mirror)) {
    walk(mirror, (full) => {
      if (/[\\/]maps[\\/]\d+\.json$/.test(full)) out.push(full);
    });
  }
  const mirrorCount = out.length;
  let entries = [];
  try { entries = fs.readdirSync(cacheDir, { withFileTypes: true }); } catch (e) { }
  for (const ent of entries) {
    if (!ent.isFile()) continue;
    const full = path.join(cacheDir, ent.name);
    let size = 0;
    try { size = fs.statSync(full).size; } catch (e) { continue; }
    if (size < 512 || size > 4 << 20) continue;
    try {
      const text = fs.readFileSync(full, "utf8");
      if (text.indexOf("\"atlasLayout\"") === -1) continue;
      out.push(full);
    } catch (e) { }
  }
  const source = mirrorCount > 0
    ? "mirror(" + mirrorCount + ") + blobs(" + (out.length - mirrorCount) + ")"
    : "blobs(" + out.length + ")";
  return { files: out, source: source };
}

function loadMap(full, cacheDir) {
  let raw;
  try { raw = fs.readFileSync(full, "utf8"); } catch (e) { return null; }
  let d;
  try { d = JSON.parse(raw); } catch (e) { return null; }
  if (!d || typeof d.id !== "number" || !d.atlasLayout) return null;

  const gp = d.atlasLayout.graphicsPositions || {};
  const gids = [];
  let jpgTiles = 0;
  for (const gid of Object.keys(gp)) {
    gids.push(Number(gid));
    if (gp[gid] && gp[gid].jpg) jpgTiles++;
  }
  gids.sort((a, b) => a - b);

  const dir = {};
  const neighbours = [];
  for (const field of NEIGHBOUR_FIELDS) {
    const v = d[field];
    const name = field.replace("NeighbourId", "");
    dir[name] = (typeof v === "number" && v > 0) ? v : null;
    if (dir[name] !== null && neighbours.indexOf(dir[name]) === -1) neighbours.push(dir[name]);
  }

  return {
    id: d.id,
    dir: dir,
    neighbours: neighbours,
    width: d.atlasLayout.width | 0,
    height: d.atlasLayout.height | 0,
    tileCount: gids.length,
    jpgTiles: jpgTiles,
    gids: gids,
    cells: Array.isArray(d.cells) ? d.cells.length : 0,
    bytes: raw.length,
    file: path.relative(cacheDir, full).split(path.sep).join("/")
  };
}

function csvRow(rec) {
  return [
    rec.id,
    rec.dir.top === null ? "" : rec.dir.top,
    rec.dir.bottom === null ? "" : rec.dir.bottom,
    rec.dir.left === null ? "" : rec.dir.left,
    rec.dir.right === null ? "" : rec.dir.right,
    rec.tileCount,
    rec.jpgTiles,
    rec.cells,
    rec.bytes,
    rec.file
  ].join(",");
}

function main() {
  const args = parseArgs(process.argv);
  if (!fs.existsSync(args.cache)) {
    console.error("cache directory not found: " + args.cache);
    process.exit(1);
  }

  const scan = findMapFiles(args.cache);
  if (scan.files.length === 0) {
    console.error("no map JSON files found under " + args.cache +
      " (mirror: " + path.join(args.cache, "files") + ")");
    process.exit(1);
  }

  const records = [];
  let skipped = 0;
  const seenIds = new Set();
  for (const file of scan.files) {
    const rec = loadMap(file, args.cache);
    if (!rec || seenIds.has(rec.id)) {
      skipped++;
      continue;
    }
    seenIds.add(rec.id);
    records.push(rec);
  }
  records.sort((a, b) => a.id - b.id);

  const known = new Set(records.map((r) => r.id));
  let orphanRefs = 0;
  const graph = {};
  for (const rec of records) {
    graph[rec.id] = rec.neighbours;
    for (const n of rec.neighbours) if (!known.has(n)) orphanRefs++;
  }

  fs.mkdirSync(args.out, { recursive: true });
  const indent = args.pretty ? 2 : 0;
  const mapsPath = path.join(args.out, "maps.json");
  const graphPath = path.join(args.out, "graph.json");
  const csvPath = path.join(args.out, "maps-index.csv");

  fs.writeFileSync(mapsPath, JSON.stringify(records, null, indent));
  fs.writeFileSync(graphPath, JSON.stringify(graph, null, indent));

  const rows = ["id,top,bottom,left,right,tiles,jpgTiles,cells,bytes,file"];
  for (const rec of records) rows.push(csvRow(rec));
  fs.writeFileSync(csvPath, rows.join("\n") + "\n");

  const totalTiles = records.reduce((sum, r) => sum + r.tileCount, 0);
  const totalBytes = records.reduce((sum, r) => sum + r.bytes, 0);
  console.log("source:           " + scan.source + " (" + scan.files.length + " files, " + skipped + " skipped)");
  console.log("maps exported:    " + records.length);
  console.log("tiles referenced: " + totalTiles + " (" + records.reduce((s, r) => s + r.jpgTiles, 0) + " jpg)");
  console.log("map JSON bytes:   " + totalBytes);
  console.log("orphan neighbour references (map JSON not cached yet): " + orphanRefs);
  console.log("wrote: " + mapsPath);
  console.log("wrote: " + graphPath);
  console.log("wrote: " + csvPath);
}

main();
