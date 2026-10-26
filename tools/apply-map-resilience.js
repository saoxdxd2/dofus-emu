// Applies the map-transition resilience edits to the client bundle the desktop
// app actually serves (desktop/client/www/build/script.js).
//
//   node tools/apply-map-resilience.js --check   # report only, writes nothing
//   node tools/apply-map-resilience.js           # apply
//
// Each rule carries a `satisfiedWhen` probe so a rule whose protection already
// reached the bundle by another route (patch-rules boundary patches 1-3 rewrite
// the same handlers) is reported APPLIED instead of STALE. Apply refuses to
// write while any rule is STALE, so the bundle is never half-patched.
//
// Rules that share a `group` are two halves of one edit (a try{ opened in one
// rule, closed in the other): a group is only usable when every member is in the
// same state, so a half-applied group is reported STALE and blocks the write.
//
// Exits 0 when every rule is APPLIED or APPLICABLE, 1 otherwise.

const fs = require('fs');
const path = require('path');

const targetFile = path.resolve(__dirname, '../desktop/client/www/build/script.js');

const rules = [
  {
    name: 'foreground touchstart auto-unlock',
    // patch-rules rule 1 rewrites the whole handler and installs the isReady
    // guard, so on a patched bundle this rule is satisfied by that patch.
    satisfiedWhen: 'this.on("dom.touchstart",function(e){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady)',
    target: 'this.on("dom.touchstart",function(e){if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}',
    replacement: 'this.on("dom.touchstart",function(e){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(this.locked){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}}if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}'
  },
  {
    name: 'foreground touchend auto-unlock',
    satisfiedWhen: 'this.on("dom.touchend",function(e){this.removeListener("dom.touchmove",t);if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady)',
    target: 'this.on("dom.touchend",function(e){this.removeListener("dom.touchmove",t);if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}',
    replacement: 'this.on("dom.touchend",function(e){this.removeListener("dom.touchmove",t);if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(this.locked){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}}if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}'
  },
  {
    name: 'camera edge slider transformStart auto-unlock',
    satisfiedWhen: 'this.on("transformStart",function(e,t){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady)',
    target: 'this.on("transformStart",function(e,t){if(n.locked)return n.cancelTransform();',
    replacement: 'this.on("transformStart",function(e,t){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(n.locked){n.unlock("loadMap"),n.unlock("quickReconnection"),n.locked=!1}}if(n.locked)return n.cancelTransform();'
  },
  {
    name: 'IsoEngine._resetTransition safe graphic removal',
    // Emits the final form (null assigned outside the try) so this also satisfies
    // tools/js-error-finder.js step 8 and replaces the two-step chain through
    // tools/fix-transition-graphic.js, which could never run after this rule.
    satisfiedWhen: 'if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}}this._mapSceneTransitionGraphic=null',
    target: 's.prototype._resetTransition=function(){this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1,this._mapSceneTransitionGraphic=null}',
    replacement: 's.prototype._resetTransition=function(){if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}}this._mapSceneTransitionGraphic=null,this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1}'
  },
  {
    name: 'IsoEngine.launchMapTransition watchdog',
    satisfiedWhen: 's.prototype.launchMapTransition=function(e){var t=this;if(this._transitionWatchdog)',
    target: 's.prototype.launchMapTransition=function(e){var t=this;this._loadingProgress.loadAssets(function(){t.mapScene.setShader("mapTransition"),t.emit("launchMapTransition",e),t._activateMapScene()?(t._loadingProgress.progressEgg.alpha=1,t.mapScene.renderingParams.ratio=1,t._saveImageForTransition("black"),t._startLoading()):t._loadingFadeInTransitionRunning===!1&&(t._isMapLoading?t._startLoading():(e&&t._makeUserActorWalkInDirection(e,!1),t._runLoadingFadeInTransition()))})}',
    replacement: 's.prototype.launchMapTransition=function(e){var t=this;if(this._transitionWatchdog)clearTimeout(this._transitionWatchdog);var _selfTr=this;this._transitionWatchdog=setTimeout(function(){if(_selfTr._isMapLoading||_selfTr._mapSceneTransitionGraphic||(window.foreground&&window.foreground.locked)){console.warn("[IsoEngine] Transition watchdog fired: clearing stalled black screen/loading state");try{if(_selfTr._mapSceneTransitionGraphic){_selfTr._mapSceneTransitionGraphic.remove();_selfTr._mapSceneTransitionGraphic=null;}if(_selfTr.mapScene){_selfTr.mapScene.setShader("unfiltering");_selfTr.mapScene.renderingParams.ratio=0.15;_selfTr._hideLoadingProgress&&_selfTr._hideLoadingProgress();_selfTr.mapScene.requireCompleteRefresh();}if(window.foreground){window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;window.foreground.hideBorderArrow&&window.foreground.hideBorderArrow();}_selfTr._isMapLoading=!1;_selfTr._loadingFadeInTransitionRunning=!1;_selfTr._waitingForMessage=!1;}catch(wEx){console.error("[IsoEngine] Watchdog error:",wEx);}}},3500);this._loadingProgress.loadAssets(function(){t.mapScene.setShader("mapTransition"),t.emit("launchMapTransition",e),t._activateMapScene()?(t._loadingProgress.progressEgg.alpha=1,t.mapScene.renderingParams.ratio=1,t._saveImageForTransition("black"),t._startLoading()):t._loadingFadeInTransitionRunning===!1&&(t._isMapLoading?t._startLoading():(e&&t._makeUserActorWalkInDirection(e,!1),t._runLoadingFadeInTransition()))})}'
  },
  {
    name: 'IsoEngine._runLoadingFadeOutTransition watchdog cancellation',
    satisfiedWhen: 's.prototype._runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;if(this._transitionWatchdog)',
    target: 's.prototype._runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;new h(this._loadingProgress.progressEgg',
    replacement: 's.prototype._runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;if(this._transitionWatchdog){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}new h(this._loadingProgress.progressEgg'
  },
  {
    name: 'IsoEngine.loadMap teleportation potion un-stalling',
    satisfiedWhen: 'this._loadingFadeInTransitionRunning===!1?(this._waitingForMessage?this._startLoading():this.launchMapTransition()):(function(mSelf)',
    target: 'loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap"),this._mapLoadingMessage=e,this._loadingFadeInTransitionRunning===!1&&(this._waitingForMessage?this._startLoading():this.launchMapTransition())}',
    replacement: 'loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap"),this._mapLoadingMessage=e,this._loadingFadeInTransitionRunning===!1?(this._waitingForMessage?this._startLoading():this.launchMapTransition()):(function(mSelf){setTimeout(function(){if(mSelf._mapLoadingMessage){mSelf._loadingFadeInTransitionRunning=!1;mSelf._startLoading();}},400)})(this)}'
  },
  {
    name: 'IsoEngine._startLoading series safeguard + safe tacticalMode cells',
    satisfiedWhen: 'var _seriesDone=!1,_seriesTimer=setTimeout',
    target: 'if(t!==C){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return console.error("Unable to get tacticalThemeData information for",e.tacticalModeId,i),t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){n.tacticalMode.loadThemeMapForMap(f,i||t,h.cells,e)}],function(t){return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h.id===C&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}}',
    replacement: 'if(t!==C){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;var _seriesDone=!1,_seriesTimer=setTimeout(function(){if(!_seriesDone){_seriesDone=!0;console.warn("[IsoEngine] _startLoading series timeout fallback");if(h){null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h};}else{n._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}}}},2500);l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return console.error("Unable to get tacticalThemeData information for",e.tacticalModeId,i),t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){var d=!1,tm=setTimeout(function(){d||(d=!0,console.warn("TacticalMode theme timeout, bypassing block"),e())},1000);try{n.tacticalMode.loadThemeMapForMap(f,i||t,h?h.cells:null,function(t){d||(d=!0,clearTimeout(tm),e(t))})}catch(err){d||(d=!0,clearTimeout(tm),e())}}],function(t){if(_seriesDone)return;_seriesDone=!0;clearTimeout(_seriesTimer);return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h&&(h.id==C||!h.id)&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}}'
  },
  {
    name: 'IsoEngine._loadMapAssets try/catch/finally protection',
    satisfiedWhen: 'catch(mErr){console.error("[IsoEngine] _loadMapAssets execution error:"',
    target: 's.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){return null!==w&&C!==t.id?void i._loadMapAssets():(window.foreground.show(),window.foreground.unlock("loadMap"),c.fillPathGrid(t),i._updateMapInfoData(e),i.actorManager.updateMapInfoData(e),i.actorManager.unpause(),i.actorManager.userActor.moving=!1,_.unlockMessages(),i.emit("mapLoaded"),setTimeout(function(){O.checkNonUsedDeepLink()},0),i._runLoadingFadeOutTransition(),i.interactiveBlink&&i.highlightInteractivesWithDifferentType(),C=null,w=null,void(i._isMapLoading=!1))})}',
    replacement: 's.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){if(null!==w&&C!==t.id)return void i._loadMapAssets();try{window.foreground.show();window.foreground.unlock("loadMap");c.fillPathGrid(t);i._updateMapInfoData(e);i.actorManager.updateMapInfoData(e);i.actorManager.unpause();i.actorManager.userActor.moving=!1;_.unlockMessages();i.emit("mapLoaded");setTimeout(function(){O.checkNonUsedDeepLink()},0);i._runLoadingFadeOutTransition();i.interactiveBlink&&i.highlightInteractivesWithDifferentType();}catch(mErr){console.error("[IsoEngine] _loadMapAssets execution error:",mErr);i._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}}finally{C=null;w=null;i._isMapLoading=!1;}})}'
  },
  {
    name: 'IsoEngine._saveImageForTransition undefined .clear() guard',
    // Owned here rather than by fix-transition-graphic.js: that script aborts on
    // its first needle, so this guard could never land on its own.
    satisfiedWhen: '!this._mapSceneTransitionGraphic||typeof this._mapSceneTransitionGraphic.clear!=="function"',
    target: 's.prototype._saveImageForTransition=function(e){var t=v,i=t*this.mapScene.h/this.mapScene.w,n=this.mapScene.renderer,o=n.startTextureUsage(t,i,1,null,"linear");if(n.startTextureRendering(o,0,t,0,i,!1),"black"===e)this.mapScene.clear(0,0,0,1);else{var a=t/this.mapScene.w;n.save(),n.scale(a,a),this.mapScene.render(),n.restore()}var r=!0;n.stopTextureRendering(r),null===this._mapSceneTransitionGraphic?this._mapSceneTransitionGraphic=new m({w:this.mapScene.w,h:this.mapScene.h,scene:this.mapScene,position:0,isHudElement:!0,alpha:1},o.texture):(this._mapSceneTransitionGraphic.clear(),this._mapSceneTransitionGraphic.texture=o.texture,this._mapSceneTransitionGraphic.show())}',
    replacement: 's.prototype._saveImageForTransition=function(e){try{var t=v,i=t*this.mapScene.h/this.mapScene.w,n=this.mapScene.renderer,o=n.startTextureUsage(t,i,1,null,"linear");if(n.startTextureRendering(o,0,t,0,i,!1),"black"===e)this.mapScene.clear(0,0,0,1);else{var a=t/this.mapScene.w;n.save(),n.scale(a,a),this.mapScene.render(),n.restore()}var r=!0;n.stopTextureRendering(r);if(!this._mapSceneTransitionGraphic||typeof this._mapSceneTransitionGraphic.clear!=="function"){this._mapSceneTransitionGraphic=new m({w:this.mapScene.w,h:this.mapScene.h,scene:this.mapScene,position:0,isHudElement:!0,alpha:1},o.texture);}else{try{this._mapSceneTransitionGraphic.clear();}catch(cEx){}this._mapSceneTransitionGraphic.texture=o.texture;this._mapSceneTransitionGraphic.show();}}catch(trEx){console.error("[IsoEngine] _saveImageForTransition error:",trEx);}}'
  },
  {
    name: 'Batch._updateSpriteHighlight null _longView guard',
    satisfiedWhen: 'n.prototype._updateSpriteHighlight=function(e){if(!this._longView||!e)return;',
    target: 'n.prototype._updateSpriteHighlight=function(e){var t=e.spriteIndex*this._spriteByteSize',
    replacement: 'n.prototype._updateSpriteHighlight=function(e){if(!this._longView||!e)return;var t=e.spriteIndex*this._spriteByteSize'
  },
  {
    name: 'TacticalMode._loadAtlas try/catch/finally with guaranteed step callback',
    satisfiedWhen: '}catch(err){}finally{n()}',
    target: 'n.prototype._loadAtlas=function(e,t){var i=this;this._canvas.width=e.width,this._atlasWidth=this._canvas.width,this._canvas.height=e.height,this._atlasHeight=this._canvas.height,this._atlasGraphics=e.graphicsPositions;var n=Object.keys(this._atlasGraphics);s.forEach(n,function(t,n){var o=i._atlasGraphics[t],r=o.jpg?"jpg":"png",s=r+"/"+t+"."+r,l=a.IMG_PATH+s;e.useUiPath&&(l="ui/tactical/"+t+".png"),c.loadImage(l,function(e){var t=o.sx,a=o.sy,r=o.sw,s=o.sh,c=o.cx||0,l=o.cy||0,d=o.cw||r,u=o.ch||s;return c+d>e.width&&(d=e.width-c),l+u>e.height&&(u=e.height-l),i._context.drawImage(e,c,l,d,u,t,a,r,s),n()})},function(){i._atlasTexture&&i._atlasTexture.release(),i._atlasTexture=i.mapScene.createTexture(i._canvas,"tacticalModeMapAtlas"+i._themeMapId,"linear"),i._canvas.width=1,i._canvas.height=1,t()})}',
    replacement: 'n.prototype._loadAtlas=function(e,t){var i=this;this._canvas.width=e.width,this._atlasWidth=this._canvas.width,this._canvas.height=e.height,this._atlasHeight=this._canvas.height,this._atlasGraphics=e.graphicsPositions;var n=Object.keys(this._atlasGraphics);s.forEach(n,function(t,n){var o=i._atlasGraphics[t],r=o.jpg?"jpg":"png",s=r+"/"+t+"."+r,l=a.IMG_PATH+s;e.useUiPath&&(l="ui/tactical/"+t+".png"),c.loadImage(l,function(e){try{if(e&&e.width>0&&e.height>0){var t=o.sx,a=o.sy,r=o.sw,s=o.sh,c=o.cx||0,l=o.cy||0,d=o.cw||r,u=o.ch||s;c>=0&&l>=0&&c<e.width&&l<e.height&&(d=Math.max(0,Math.min(d,e.width-c)),u=Math.max(0,Math.min(u,e.height-l)),d>0&&u>0&&r>0&&s>0&&i._context.drawImage(e,c,l,d,u,t,a,r,s))}}catch(err){}finally{n()}})},function(){i._atlasTexture&&i._atlasTexture.release(),i._atlasTexture=i.mapScene.createTexture(i._canvas,"tacticalModeMapAtlas"+i._themeMapId,"linear"),i._canvas.width=1,i._canvas.height=1,t()})}'
  },
  {
    name: 'TacticalMode._applyThemeToMap defined-cell check (opens the safe block)',
    group: 'applyTheme',
    satisfiedWhen: 'e[h]&&void 0!==e[h].l&&1===(5&e[h].l)',
    target: 'this._wallBatch.setTexture(n._atlasTexture);for(var s,c,h=0;h<a.NB_CELLS;h++){var f=r.cellCoord[h];if(1===(5&e[h].l)){var m=n.floorGraphics[f.x%2][0];',
    replacement: 'this._wallBatch.setTexture(n._atlasTexture);try{if(e){for(var s,c,h=0;h<a.NB_CELLS;h++){var f=r.cellCoord[h];if(e[h]&&void 0!==e[h].l&&1===(5&e[h].l)){var m=n.floorGraphics&&n.floorGraphics[f.x%2]&&n.floorGraphics[f.x%2][0];'
  },
  {
    name: 'TacticalMode._applyThemeToMap guaranteed callback via finally (closes the safe block)',
    group: 'applyTheme',
    satisfiedWhen: 'catch(err){console.warn("safe applyTheme error:",err)}finally{return i()}',
    target: '}}return n._floorBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._floorBatch.hide(),n._wallBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._wallBatch.hide(),i()}',
    replacement: '}}}n._floorBatch&&n._floorBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._floorBatch&&n._floorBatch.hide(),n._wallBatch&&n._wallBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._wallBatch&&n._wallBatch.hide()}catch(err){console.warn("safe applyTheme error:",err)}finally{return i()}}'
  }
];

function inspect(content) {
  const rows = rules.map((r, i) => {
    const satisfied = r.satisfiedWhen ? content.includes(r.satisfiedWhen) : false;
    const tCount = content.split(r.target).length - 1;
    if (satisfied) return { i: i + 1, name: r.name, group: r.group, status: 'APPLIED', note: 'protection already present' };
    if (tCount === 1) return { i: i + 1, name: r.name, group: r.group, status: 'APPLICABLE', note: '' };
    if (tCount === 0) return { i: i + 1, name: r.name, group: r.group, status: 'STALE', note: 'target absent (client moved this text)' };
    return { i: i + 1, name: r.name, group: r.group, status: 'STALE', note: 'target ambiguous (' + tCount + ' hits)' };
  });
  for (const g of new Set(rows.filter((r) => r.group).map((r) => r.group))) {
    const members = rows.filter((r) => r.group === g);
    if (new Set(members.map((r) => r.status)).size > 1) {
      for (const m of members) {
        m.status = 'STALE';
        m.note = 'half-applied group "' + g + '" - would emit unbalanced braces';
      }
    }
  }
  return rows;
}

const check = process.argv.includes('--check');
const content = fs.readFileSync(targetFile, 'utf8');
const report = inspect(content);
for (const r of report) {
  console.log('#' + r.i + ' [' + r.status + '] ' + r.name + (r.note ? ' - ' + r.note : ''));
}

const stale = report.filter((r) => r.status === 'STALE');
const pending = report.filter((r) => r.status === 'APPLICABLE');

if (check) {
  console.log('\n' + targetFile);
  console.log('applied ' + (report.length - stale.length - pending.length) +
              '/' + report.length + ', applicable ' + pending.length +
              ', stale ' + stale.length);
  if (stale.length) {
    console.error('CHECK FAILED: ' + stale.length + ' rule(s) need re-deriving against this client build.');
    process.exit(1);
  }
  console.log(pending.length
    ? 'CHECK OK: resilience is not yet in the bundle; ' + pending.length + ' rule(s) ready to apply.'
    : 'CHECK OK: bundle already carries every resilience rule.');
  process.exit(0);
}

if (stale.length) {
  console.error('[ABORT] ' + stale.length + ' stale rule(s); nothing written.');
  process.exit(1);
}
if (!pending.length) {
  console.log('Nothing to do: bundle already carries every resilience rule.');
  process.exit(0);
}

let out = content;
for (const r of pending) {
  const rule = rules[r.i - 1];
  out = out.replace(rule.target, rule.replacement);
  console.log('[OK] applied #' + r.i + ' ' + rule.name);
}
fs.writeFileSync(targetFile, out, 'utf8');
console.log('Applied ' + pending.length + ' resilience rule(s) to ' + targetFile);
