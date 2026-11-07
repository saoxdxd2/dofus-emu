// One reader for the patch-set quarantine flag, shared by the tools that decide whether the
// bundle the client serves is ours or the vendor's. They must not disagree: patch-engine.js
// skips writing, apply-map-resilience.js must not apply, and the gate asserts the *inverse*
// invariant (a pristine bundle) while the flag exists.
//
// The flag is a file at the repo root whose content is the reason. It exists because on
// 2026-11-04 the resilience rules matched build 3.3.11 byte-for-byte and the patched series
// callback still double-fired ("Callback was already called") -> black screen. Anchors
// matching is not proof the patch is correct, so the set is off until re-derived and proven
// in the game; delete the file to re-enable.

const fs = require('fs');
const path = require('path');

const FILE = path.resolve(__dirname, '..', 'client-patches.quarantine');

function state() {
  let reason = '';
  let active = false;
  try {
    reason = fs.readFileSync(FILE, 'utf8').trim().replace(/\s*\n\s*/g, ' ');
    active = true;
  } catch (e) {
    active = false;
  }
  return { active: active, reason: reason, file: FILE };
}

module.exports = { state: state, FILE: FILE };
