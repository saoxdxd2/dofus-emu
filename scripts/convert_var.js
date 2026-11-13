'use strict';
const fs = require('fs');
const FILE = 'c:/Users/sao/Documents/dofus-emu/scripts/mobile-disguise.js';
const REPORT = 'c:/Users/sao/Documents/dofus-emu/scripts/convert_report.txt';
const src = fs.readFileSync(FILE, 'utf8');
const lines = src.split('\n');
function maskSource(s) {
    const out = [];
    let prev = ''; // carried across lines so a regex starting at line start (e.g. after
                   // `.match(` ended on the previous line) is still recognized
    for (const line of s.split('\n')) {
        let r = '', j = 0, m = line.length;
        let inStr = false, sq = '', inTpl = false, inReg = false;
        let inLC = false, inBC = false, esc = false;
        while (j < m) {
            const c = line[j], nx = line[j + 1] || '';
            if (inLC) { r += ' '; j++; continue; }
            if (inBC) {
                r += ' ';
                if (c === '*' && nx === '/') { r += ' '; j += 2; inBC = false; continue; }
                j++; continue;
            }
            if (inStr) {
                r += ' ';
                if (esc) { esc = false; }
                else if (c === '\\') { esc = true; }
                else if (c === sq) { inStr = false; }
                j++; continue;
            }
            if (inTpl) {
                r += ' ';
                if (esc) { esc = false; }
                else if (c === '\\') { esc = true; }
                else if (c === '`') { inTpl = false; }
                j++; continue;
            }
            if (inReg) {
                r += ' ';
                if (esc) { esc = false; }
                else if (c === '\\') { esc = true; }
                else if (c === '/') { inReg = false; }
                j++; continue;
            }
            if (c === '/' && nx === '/') { r += '  '; inLC = true; j += 2; continue; }
            if (c === '/' && nx === '*') { r += '  '; inBC = true; j += 2; continue; }
            if (c === '"' || c === "'") { inStr = true; sq = c; r += ' '; j++; continue; }
            if (c === '`') { inTpl = true; r += ' '; j++; continue; }
            if (c === '/' && prev !== '' && !/[\w$)\]]/.test(prev) && !/[\d\)\]]/.test(prev)) {
                inReg = true; r += ' '; j++; continue;
            }
            r += c; if (!/\s/.test(c)) prev = c; j++;
        }
        out.push(r);
    }
    return out.join('\n');
}
const masked = maskSource(src);
const lsOff = []; let acc = 0;
for (const line of lines) { lsOff.push(acc); acc += line.length + 1; }
function lineOf(pos) {
    let lo = 0, hi = lsOff.length - 1, ans = 0;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (lsOff[mid] <= pos) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
}
function findFunctionBodies(m) {
    const scopes = [];
    const seen = new Set();
    function addScope(braceOpenPos) {
        let depth = 1, k = braceOpenPos + 1, n = m.length;
        while (k < n && depth > 0) {
            const c = m[k];
            if (c === '{') depth++;
            else if (c === '}') depth--;
            k++;
        }
        const endPos = k - 1;
        scopes.push({ startPos: braceOpenPos + 1, endPos,
            startLine: lineOf(braceOpenPos) + 1, endLine: lineOf(endPos) });
    }
    const fnRe = /\bfunction\b/g;
    let mm;
    while ((mm = fnRe.exec(m)) !== null) {
        let k = fnRe.lastIndex;
        while (k < m.length && /\s/.test(m[k])) k++;
        if (/[A-Za-z_$]/.test(m[k])) {
            while (k < m.length && /[\w$]/.test(m[k])) k++;
            while (k < m.length && /\s/.test(m[k])) k++;
        }
        if (k < m.length && m[k] === '(') {
            let pd = 1, p = k + 1;
            while (p < m.length && pd > 0) { if (m[p] === '(') pd++; else if (m[p] === ')') pd--; p++; }
            while (p < m.length && /\s/.test(m[p])) p++;
            if (p < m.length && m[p] === '{' && !seen.has(p)) { seen.add(p); addScope(p); }
        }
    }
    const arRe = /=>/g;
    while ((mm = arRe.exec(m)) !== null) {
        let k = arRe.lastIndex;
        while (k < m.length && /\s/.test(m[k])) k++;
        if (k < m.length && m[k] === '{' && !seen.has(k)) { seen.add(k); addScope(k); }
    }
    return scopes;
}
const scopes = findFunctionBodies(masked);
function innermostScope(pos) {
    let best = null;
    for (const sc of scopes) {
        if (pos > sc.startPos && pos < sc.endPos) {
            if (!best || sc.startPos > best.startPos) best = sc;
        }
    }
    return best;
}
function insideNestedScope(pos, outer) {
    for (const sc of scopes) {
        if (sc === outer) continue;
        if (pos >= sc.startPos && pos <= sc.endPos) return true;
    }
    return false;
}
const VAR_RE = /\bvar\b/;
function findVarStatements(m) {
    const stmts = [], re = new RegExp(VAR_RE.source, 'g');
    let mm;
    while ((mm = re.exec(m)) !== null) {
        const varPos = mm.index;
        const before = m.slice(Math.max(0, varPos - 1), varPos);
        if (varPos > 0 && /[\w$.)]/.test(before)) continue;
        let j = re.lastIndex, depth = 0, declEnd = -1;
        while (j < m.length) {
            const c = m[j];
            if (c === '(' || c === '[' || c === '{') depth++;
            else if (c === ')' || c === ']' || c === '}') depth--;
            else if (c === ';' && depth === 0) { declEnd = j; break; }
            else if (depth === 0) {
                const n2 = m.slice(j, j + 2);
                if ((n2 === 'in' || n2 === 'of') && (j === 0 || !/[\w$]/.test(m[j - 1])) && (j + 2 >= m.length || !/[\w$]/.test(m[j + 2]))) { declEnd = j; break; } // for-in / for-of init
            }
            j++;
        }
        if (declEnd === -1) continue;
        stmts.push({ varPos, lineIdx: lineOf(varPos), declStart: re.lastIndex, declEnd,
            declText: m.slice(re.lastIndex, declEnd) });
    }
    return stmts;
}
const stmts = findVarStatements(masked);
function splitDeclarators(text) {
    const parts = [], cur = []; let depth = 0;
    for (const c of text) {
        if (c === '(' || c === '[' || c === '{') { depth++; cur.push(c); }
        else if (c === ')' || c === ']' || c === '}') { depth--; cur.push(c); }
        else if (c === ',' && depth === 0) { parts.push(cur.join('').trim()); cur.length = 0; }
        else cur.push(c);
    }
    const last = cur.join('').trim();
    if (last) parts.push(last);
    return parts;
}
const NAME_INIT_RE = /^([A-Za-z_$][\w$]*)\s*(=\s*([\s\S]*))?$/;
function parseDeclarator(text) {
    const mm = NAME_INIT_RE.exec(text);
    if (!mm) return null;
    return { name: mm[1], hasInit: !!mm[2], init: mm[3] || '' };
}
function escRe(name) { return name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function isReassignedIn(m, name, start, end) {
    const re = new RegExp('\\b' + escRe(name) + '\\s*(=|\\+=|-=|\\*=|/=|%=|<<=|>>=|>>>=|&=|\\^=|\\|=|\\*\\*=|\\+\\+|\\-\\-)', 'g');
    let mm;
    re.lastIndex = start;
    while ((mm = re.exec(m)) !== null) {
        if (mm.index >= end) break;
        if (mm.index >= start) return true;
    }
    return false;
}
function isReadBeforeDeclTDZ(m, name, start, end, declPos, outerScope) {
    const re = new RegExp('\\b' + escRe(name) + '\\b', 'g');
    let mm;
    re.lastIndex = start;
    while ((mm = re.exec(m)) !== null) {
        const pos = mm.index;
        if (pos >= declPos) break;
        if (pos < start) continue;
        if (insideNestedScope(pos, outerScope)) continue;
        const b = m.slice(Math.max(0, pos - 6), pos);
        if (/\b(var|let|const)\s*$/.test(b)) continue;
        return true;
    }
    return false;
}
const changes = [], skipped = [];
for (const st of stmts) {
    const scope = innermostScope(st.varPos);
    const scopeStart = scope ? scope.startPos : 0;
    const scopeEnd = scope ? scope.endPos : masked.length;
    const decls = splitDeclarators(st.declText);
    const parsed = decls.map(d => parseDeclarator(d)).filter(Boolean);
    if (parsed.length === 0) continue;
    const decisions = parsed.map(p => {
        const reassigned = isReassignedIn(masked, p.name, st.declEnd + 1, scopeEnd);
        const tdz = isReadBeforeDeclTDZ(masked, p.name, scopeStart, scopeEnd, st.varPos, scope);
        return { name: p.name, reassigned, tdz };
    });
    if (parsed.length === 1) {
        const p = parsed[0], d = decisions[0];
        if (d.tdz) { skipped.push({ line: st.lineIdx + 1, name: p.name, reason: 'read before declaration (TDZ risk)' }); continue; }
        const kw = d.reassigned ? 'let' : 'const';
        changes.push({ line: st.lineIdx + 1, name: p.name, old: 'var', new: kw,
            reason: d.reassigned ? 'reassigned' : 'never reassigned', pos: st.varPos, len: 3 });
    } else {
        const anyTdz = parsed.some((p, idx) => decisions[idx].tdz);
        if (anyTdz) { skipped.push({ line: st.lineIdx + 1, name: 'multi', reason: 'multi-declarator with TDZ risk' }); continue; }
        const allReassigned = parsed.every((p, idx) => decisions[idx].reassigned);
        const allConst = parsed.every((p, idx) => !decisions[idx].reassigned);
        if (allReassigned || allConst) {
            const kw = allReassigned ? 'let' : 'const';
            changes.push({ line: st.lineIdx + 1, name: 'multi(' + parsed.map(p => p.name).join(',') + ')',
                old: 'var', new: kw, reason: allReassigned ? 'all reassigned' : 'none reassigned',
                pos: st.varPos, len: 3 });
        } else {
            const parts = [];
            for (const p of parsed) {
                const d = decisions[parsed.indexOf(p)];
                const kw = d.reassigned ? 'let' : 'const';
                parts.push(kw + ' ' + decls[parsed.indexOf(p)]);
            }
            changes.push({ line: st.lineIdx + 1, name: 'multi(' + parsed.map(p => p.name).join(',') + ')',
                old: 'var', new: 'SPLIT', reason: 'mixed const/let', pos: st.varPos,
                declEnd: st.declEnd, parts });
        }
    }
}
const constCount = changes.filter(c => c.new === 'const').length;
const letCount = changes.filter(c => c.new === 'let').length;
const splitCount = changes.filter(c => c.new === 'SPLIT').length;
const reportLines = [];
reportLines.push('function scopes detected: ' + scopes.length);
reportLines.push('var statements found: ' + stmts.length);
reportLines.push('convertible changes: ' + changes.length);
reportLines.push('skipped: ' + skipped.length);
reportLines.push('const: ' + constCount + ' let: ' + letCount + ' split: ' + splitCount);
fs.writeFileSync(REPORT, reportLines.join('\n') + '\n');
let result = src;
const srcArr = src.split('\n');
const ops = [];
for (const ch of changes) {
    if (ch.new !== 'SPLIT') {
        ops.push({ pos: ch.pos, oldLen: 3, newText: ch.new });
    } else {
        const lineIdx = ch.line - 1;
        const match = srcArr[lineIdx].match(/^(\s*)/);
        const indent = match ? match[1] : '';
        const stmtsOut = ch.parts.map(p => indent + p + ';').join('\n');
        ops.push({ pos: ch.pos, oldLen: ch.declEnd - ch.pos + 1, newText: stmtsOut });
    }
}
ops.sort((a, b) => b.pos - a.pos);
for (const op of ops) {
    // Descending order: earlier (higher) ops only grow the string after op.pos,
    // so op.pos stays valid in the running `result`.
    result = result.slice(0, op.pos) + op.newText + result.slice(op.pos + op.oldLen);
}
fs.writeFileSync(FILE, result);
const detail = [];
detail.push('--- Changes applied ---');
for (const ch of changes) {
    detail.push('  L' + ch.line + ': ' + ch.name + ': var -> ' + ch.new + ' (' + ch.reason + ')');
}
detail.push('--- Skipped ---');
for (const s of skipped) {
    detail.push('  L' + s.line + ': ' + s.name + ': kept var (' + s.reason + ')');
}
fs.writeFileSync(REPORT, reportLines.join('\n') + '\n\n' + detail.join('\n') + '\n');
console.log('DONE: ' + constCount + ' const, ' + letCount + ' let, ' + splitCount + ' split, ' + skipped.length + ' skipped');


