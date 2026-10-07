<#
.SYNOPSIS
  Verify and stress-test the Cognitive Human Behavior & Fatigue Modeling Engine.
#>
[CmdletBinding()]
param()

$RepoRoot = Split-Path -Parent $PSScriptRoot
$JsEngine = (Join-Path $PSScriptRoot 'human-behavior-model.js').Replace('\', '/')

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "    COGNITIVE HUMAN BEHAVIOR & FATIGUE MODEL VERIFICATION        " -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Cyan

$testScript = @'
const HumanFatigueModel = require('__ENGINE_PATH__');
const model = new HumanFatigueModel({ timeDecayMinutes: 60 });

console.log('--- Initial Fresh State (0m elapsed) ---');
for (let i = 0; i < 5; i++) {
  model.updateState();
  const delay = model.generateReactionDelay();
  const traj = model.generateTrajectory(100, 200, 500, 450, 32);
  console.log('Action ' + (i+1) + ': State=' + traj.state + ' | Delay=' + delay + 'ms | TrajectoryDuration=' + traj.durationMs + 'ms | TargetScatter=(' + traj.targetX + ', ' + traj.targetY + ') | Points=' + traj.points.length + ' | Fatigue=' + traj.fatigueFactor);
}

// Simulate 90 minutes of active session play
model.sessionStartTime = Date.now() - (90 * 60 * 1000);
model.actionsPerformed = 1200;

console.log('\n--- Fatigued State (90m elapsed, 1200 actions) ---');
for (let i = 0; i < 5; i++) {
  model.updateState();
  const delay = model.generateReactionDelay();
  const traj = model.generateTrajectory(100, 200, 500, 450, 32);
  const pause = model.checkBiologicalPause();
  const pauseStr = pause ? '[PAUSE: ' + pause.type + ' for ' + Math.round(pause.durationMs/1000) + 's]' : 'No pause';
  console.log('Action ' + (i+1) + ': State=' + traj.state + ' | Delay=' + delay + 'ms | TrajectoryDuration=' + traj.durationMs + 'ms | TargetScatter=(' + traj.targetX + ', ' + traj.targetY + ') | Fatigue=' + traj.fatigueFactor + ' | ' + pauseStr);
}
'@.Replace('__ENGINE_PATH__', $JsEngine)

$tempJs = Join-Path $env:TEMP "test_behavior.js"
Set-Content -Path $tempJs -Value $testScript
node $tempJs
Remove-Item $tempJs -Force -EA SilentlyContinue
