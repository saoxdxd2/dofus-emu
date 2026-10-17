const fs = require('fs');
const path = require('path');

const targetFile = path.resolve(__dirname, '../desktop/client/www/build/script.js');
let content = fs.readFileSync(targetFile, 'utf8');

const replacements = [
  // 1. Foreground touchstart auto-unlock
  {
    target: 'this.on("dom.touchstart",function(e){if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}',
    replacement: 'this.on("dom.touchstart",function(e){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(this.locked){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}}if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}'
  },
  // 2. Foreground touchend auto-unlock
  {
    target: 'this.on("dom.touchend",function(e){this.removeListener("dom.touchmove",t);if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}',
    replacement: 'this.on("dom.touchend",function(e){this.removeListener("dom.touchmove",t);if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(this.locked){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}}if(this.locked&&window.isoEngine&&!window.isoEngine._isMapLoading&&!window.isoEngine._loadingFadeInTransitionRunning){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}'
  },
  // 3. Camera edge slider transformStart auto-unlock
  {
    target: 'this.on("transformStart",function(e,t){if(n.locked)return n.cancelTransform();',
    replacement: 'this.on("transformStart",function(e,t){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(n.locked){n.unlock("loadMap"),n.unlock("quickReconnection"),n.locked=!1}}if(n.locked)return n.cancelTransform();'
  },
  // 4. IsoEngine._resetTransition safe graphic removal
  {
    target: 's.prototype._resetTransition=function(){this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1,this._mapSceneTransitionGraphic=null}',
    replacement: 's.prototype._resetTransition=function(){if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}this._mapSceneTransitionGraphic=null;}this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1}'
  },
  // 5. IsoEngine.launchMapTransition watchdog
  {
    target: 's.prototype.launchMapTransition=function(e){var t=this;this._loadingProgress.loadAssets(function(){t.mapScene.setShader("mapTransition"),t.emit("launchMapTransition",e),t._activateMapScene()?(t._loadingProgress.progressEgg.alpha=1,t.mapScene.renderingParams.ratio=1,t._saveImageForTransition("black"),t._startLoading()):t._loadingFadeInTransitionRunning===!1&&(t._isMapLoading?t._startLoading():(e&&t._makeUserActorWalkInDirection(e,!1),t._runLoadingFadeInTransition()))})}',
    replacement: 's.prototype.launchMapTransition=function(e){var t=this;if(this._transitionWatchdog)clearTimeout(this._transitionWatchdog);var _selfTr=this;this._transitionWatchdog=setTimeout(function(){if(_selfTr._isMapLoading||_selfTr._mapSceneTransitionGraphic||(window.foreground&&window.foreground.locked)){console.warn("[IsoEngine] Transition watchdog fired: clearing stalled black screen/loading state");try{if(_selfTr._mapSceneTransitionGraphic){_selfTr._mapSceneTransitionGraphic.remove();_selfTr._mapSceneTransitionGraphic=null;}if(_selfTr.mapScene){_selfTr.mapScene.setShader("unfiltering");_selfTr.mapScene.renderingParams.ratio=0.15;_selfTr._hideLoadingProgress&&_selfTr._hideLoadingProgress();_selfTr.mapScene.requireCompleteRefresh();}if(window.foreground){window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;window.foreground.hideBorderArrow&&window.foreground.hideBorderArrow();}_selfTr._isMapLoading=!1;_selfTr._loadingFadeInTransitionRunning=!1;_selfTr._waitingForMessage=!1;}catch(wEx){console.error("[IsoEngine] Watchdog error:",wEx);}}},3500);this._loadingProgress.loadAssets(function(){t.mapScene.setShader("mapTransition"),t.emit("launchMapTransition",e),t._activateMapScene()?(t._loadingProgress.progressEgg.alpha=1,t.mapScene.renderingParams.ratio=1,t._saveImageForTransition("black"),t._startLoading()):t._loadingFadeInTransitionRunning===!1&&(t._isMapLoading?t._startLoading():(e&&t._makeUserActorWalkInDirection(e,!1),t._runLoadingFadeInTransition()))})}'
  },
  // 6. IsoEngine._runLoadingFadeOutTransition watchdog cancellation
  {
    target: '_runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;setTimeout(function(){try{if(e._mapSceneTransitionGraphic){e._mapSceneTransitionGraphic.remove();e._mapSceneTransitionGraphic=null;}t.setShader("unfiltering");t.renderingParams.ratio=.15;e._hideLoadingProgress();t.requireCompleteRefresh();}catch(err){}},350);',
    replacement: '_runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;if(this._transitionWatchdog){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}setTimeout(function(){try{if(e._mapSceneTransitionGraphic){e._mapSceneTransitionGraphic.remove();e._mapSceneTransitionGraphic=null;}t.setShader("unfiltering");t.renderingParams.ratio=.15;e._hideLoadingProgress();t.requireCompleteRefresh();}catch(err){}},350);'
  },
  // 7. IsoEngine.loadMap teleportation potion un-stalling
  {
    target: 'loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap"),this._mapLoadingMessage=e,this._loadingFadeInTransitionRunning===!1&&(this._waitingForMessage?this._startLoading():this.launchMapTransition())}',
    replacement: 'loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap"),this._mapLoadingMessage=e,this._loadingFadeInTransitionRunning===!1?(this._waitingForMessage?this._startLoading():this.launchMapTransition()):(function(mSelf){setTimeout(function(){if(mSelf._mapLoadingMessage){mSelf._loadingFadeInTransitionRunning=!1;mSelf._startLoading();}},400)})(this)}'
  },
  // 8. IsoEngine._startLoading series safeguard timeout & safe tacticalMode cells check
  {
    target: 'if(t!==C){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return console.error("Unable to get tacticalThemeData information for",e.tacticalModeId,i),t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){var d=!1,tm=setTimeout(function(){d||(d=!0,console.warn("TacticalMode theme timeout, bypassing block"),e())},1000);try{n.tacticalMode.loadThemeMapForMap(f,i||t,h.cells,function(t){d||(d=!0,clearTimeout(tm),e(t))})}catch(err){d||(d=!0,clearTimeout(tm),e())}}],function(t){return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h.id===C&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}}',
    replacement: 'if(t!==C){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;var _seriesDone=!1,_seriesTimer=setTimeout(function(){if(!_seriesDone){_seriesDone=!0;console.warn("[IsoEngine] _startLoading series timeout fallback");if(h){null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h};}else{n._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}}}},2500);l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return console.error("Unable to get tacticalThemeData information for",e.tacticalModeId,i),t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){var d=!1,tm=setTimeout(function(){d||(d=!0,console.warn("TacticalMode theme timeout, bypassing block"),e())},1000);try{n.tacticalMode.loadThemeMapForMap(f,i||t,h?h.cells:null,function(t){d||(d=!0,clearTimeout(tm),e(t))})}catch(err){d||(d=!0,clearTimeout(tm),e())}}],function(t){if(_seriesDone)return;_seriesDone=!0;clearTimeout(_seriesTimer);return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h&&(h.id==C||!h.id)&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}}'
  },
  // 9. IsoEngine._loadMapAssets try/catch/finally protection
  {
    target: 's.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){return null!==w&&C!==t.id?void i._loadMapAssets():(window.foreground.show(),window.foreground.unlock("loadMap"),c.fillPathGrid(t),i._updateMapInfoData(e),i.actorManager.updateMapInfoData(e),i.actorManager.unpause(),i.actorManager.userActor.moving=!1,_.unlockMessages(),i.emit("mapLoaded"),setTimeout(function(){O.checkNonUsedDeepLink()},0),i._runLoadingFadeOutTransition(),i.interactiveBlink&&i.highlightInteractivesWithDifferentType(),C=null,w=null,void(i._isMapLoading=!1))})}',
    replacement: 's.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){if(null!==w&&C!==t.id)return void i._loadMapAssets();try{window.foreground.show();window.foreground.unlock("loadMap");c.fillPathGrid(t);i._updateMapInfoData(e);i.actorManager.updateMapInfoData(e);i.actorManager.unpause();i.actorManager.userActor.moving=!1;_.unlockMessages();i.emit("mapLoaded");setTimeout(function(){O.checkNonUsedDeepLink()},0);i._runLoadingFadeOutTransition();i.interactiveBlink&&i.highlightInteractivesWithDifferentType();}catch(mErr){console.error("[IsoEngine] _loadMapAssets execution error:",mErr);i._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}}finally{C=null;w=null;i._isMapLoading=!1;}})}'
  }
];

let applied = 0;
for (let i = 0; i < replacements.length; i++) {
  const { target, replacement } = replacements[i];
  if (!content.includes(target)) {
    console.error(`[ERROR] Replacement #${i + 1} target not found in file!`);
    process.exit(1);
  }
  content = content.replace(target, replacement);
  applied++;
  console.log(`[OK] Replacement #${i + 1} matched and applied.`);
}

fs.writeFileSync(targetFile, content, 'utf8');
console.log(`Successfully applied all ${applied} resilience patches to ${targetFile}`);
