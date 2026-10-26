const fs = require('fs');
const path = require('path');

const targetPath = path.resolve(__dirname, '../desktop/client/www/build/script.js');
const bakPath = path.resolve(__dirname, '../desktop/client/www/build/script.js.bak');

if (!fs.existsSync(bakPath)) {
    if (!fs.existsSync(targetPath)) {
        console.error('Target script not found at:', targetPath);
        process.exit(1);
    }
    fs.copyFileSync(targetPath, bakPath);
    console.log('Created clean backup:', bakPath);
}

let s = fs.readFileSync(bakPath, 'utf8');


// 0. Guard resizeWideScreen against a not-yet-built shortcutBarManager.
//    Client 1.74.9 calls it during boot and on every resize; the old build had
//    the manager ready by then. Without this the exception aborts the layout and
//    the game window can no longer be resized.
const resizeNeedle = 'var c=l.gui.shortcutBarManager.shortcutBars.playerBar';
if (s.indexOf(resizeNeedle) !== -1) {
    // menuBar is built at a different moment than shortcutBarManager, and
    // resizeWideScreen calls menuBar.computeMinimumSize() further down. Guarding
    // only shortcutBarManager let the function through with d=undefined and threw
    // "Cannot read properties of undefined (reading 'computeMinimumSize')".
    s = s.replace(resizeNeedle,
        'if(!l||!l.gui||!l.gui.shortcutBarManager||!l.gui.menuBar)return;var c=l.gui.shortcutBarManager.shortcutBars.playerBar');
    console.log('0. resizeWideScreen guarded.');
} else {
    console.error('[SKIP] resizeWideScreen needle not found');
}

// 1. Patch _setupTouchInteraction
const startTouchIdx = s.indexOf('o.prototype._setupTouchInteraction=function(){');
const endTouchMarker = '),this._setupHighlightInteraction(),this._setupCameraInteraction()}';
const endTouchIdx = s.indexOf(endTouchMarker, startTouchIdx);
if (startTouchIdx === -1 || endTouchIdx === -1) {
    console.error('[SKIP] _setupTouchInteraction boundary not found (client changed?)');

}
const oldTouch = s.substring(startTouchIdx, endTouchIdx + endTouchMarker.length);
console.log('Found oldTouch length:', oldTouch.length);

const newTouch = 'o.prototype._setupTouchInteraction=function(){function e(e){if(l.isVisible()){var t=c(l,e.x,e.y);l.setStyle("webkitTransform","translate3d("+t.x+"px, "+t.y+"px, 0)")}}function t(t){var i=u(t,{useScrollValue:!0});if(e(i),Math.abs(o-i.x)>10||Math.abs(a-i.y)>10){if(!p("TOUCH",f))return r();o=i.x,a=i.y;var n=s.convertScreenToCanvasCoordinate(o,a);d.touchMove(n.x,n.y,this.tapOptions)}}var i,n,o,a,r,s=this,l=this.infoBox,d=window.isoEngine,h=window.gui.fightManager,f={};r=function(){n=!0,d.touchCancel(),s.removeListener("dom.touchmove",t)},this.on("dom.touchstart",function(e){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(this.locked){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}}var s=u(e,{useScrollValue:!0});if(s.touchCount>1)return r();o=s.x,a=s.y,i=!0,n=!1,h.isInFight()&&this.on("dom.touchmove",t)}),this.on("dom.touchend",function(e){this.removeListener("dom.touchmove",t);if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(this.locked){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}}var wasPending=i;i=!1;if(this.locked||n||!wasPending){n=!1;return}n=!1;this.tapOptions.mode=this.tapOptions.mode||(window.gui&&window.gui.playerData&&window.gui.playerData.isFighting?"fight":"roleplay");var oEvt=u(e,{useScrollValue:!0}),aCoord=s.convertScreenToCanvasCoordinate(oEvt.x,oEvt.y);d.touchEnd(aCoord.x,aCoord.y,this.tapOptions)}),this._setupHighlightInteraction(),this._setupCameraInteraction()}';

s = s.replace(oldTouch, newTouch);
console.log('1. _setupTouchInteraction patched successfully.');

// 2. Patch transformEnd
const transformEndMarker = ',this.on("transformEnd",function(){';
const startTrEnd = s.indexOf(transformEndMarker);
const endTrEndMarker = 'Date.now()-i>100||c.addInertia(u,p,.8)});var W=window.isoEngine.actorManager.us';
const endTrEnd = s.indexOf(endTrEndMarker, startTrEnd);
if (startTrEnd === -1 || endTrEnd === -1) {
    console.error('[SKIP] transformEnd boundary not found (client changed?)');

}
const oldTrEnd = s.substring(startTrEnd, endTrEnd + endTrEndMarker.length);
console.log('Found oldTrEnd length:', oldTrEnd.length);

const newTrEnd = ',this.on("transformEnd",function(){if(B.x.playing&&B.x.stop(),B.y.playing&&B.y.stop(),A){var e;if(e="x"===I?"translate3d(0,"+x.y+"px,0)":"translate3d("+x.x+"px,0,0)",m.tween(A,{webkitTransform:e},{time:200,easing:"ease-out"}),A.arrow.setStyle("opacity",0),this.locked&&window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){this.unlock("loadMap"),this.unlock("quickReconnection"),this.locked=!1}var currO=O;A=null;if(this.locked)return;if(Math.abs(x[I])>=E[I]*0.7&&w!==-1){var t=s.convertCanvasToSceneCoordinate(N.x,N.y);r._tapRoleplay(t.x,t.y,r.mapRenderer.getCellId(t.x,t.y),{canvasX:N.x,canvasY:N.y,changeMapRequest:currO,mode:"roleplay"})}}else this.setTranslationEnable(!l.isInBattle()),Date.now()-i>100||c.addInertia(u,p,.8)});var W=window.isoEngine.actorManager.us';

s = s.replace(oldTrEnd, newTrEnd);
console.log('2. transformEnd patched successfully.');

// 3. Patch _tapRoleplay
const tapRoleplayMarker = 'c.prototype._tapRoleplay=function(e,t,i,n){';
const startTapRole = s.indexOf(tapRoleplayMarker);
const endTapRoleMarker = ':void this._movePlayerOnMap(m)}},c.prototype._tapFightWithSpell';
const endTapRole = s.indexOf(endTapRoleMarker, startTapRole);
if (startTapRole === -1 || endTapRole === -1) {
    console.error('[SKIP] _tapRoleplay boundary not found (client changed?)');

}
const oldTapRole = s.substring(startTapRole, endTapRole + endTapRoleMarker.length);
console.log('Found oldTapRole length:', oldTapRole.length);

const newTapRole = 'c.prototype._tapRoleplay=function(e,t,i,n){var o=this,a=n.canvasX,r=n.canvasY,s=n.changeMapRequest,c=this.mapRenderer,l=this.actorManager.userActor,d=this.actionQueue.isActive();if(d){var u=this._tapInteractive(e,t);if(!u&&!l.moving&&(s=s||c.getFirstMapFlag(i.cell))){var p=!0,h=this.actionQueue.enqueue("changeMap",p,function(){o.gotoNeighbourMap(s,i.cell,a,r)});return void(h&&window.foreground.showBorderArrow(s,a,r))}}else this.lastContextualMenuSkillId=null;if(l&&l.isLocked&&!l.moving&&(!this.actionQueue||!this.actionQueue.isActive()))l.isLocked=!1;if(!l||!l.isLocked){if(this.isMovementWaitingForConfirmation&&this.lastMoveRequestTime&&Date.now()-this.lastMoveRequestTime>1500){this.isMovementWaitingForConfirmation=!1,this.isMovementCanceled=!1}if(l.moving||this.isMovementWaitingForConfirmation){if(window.foreground.hideBorderArrow(),d&&!s&&this._tapInteractive(e,t))return;return this.clearHighlights(),this.actionQueue.clear(),void this.cancelUserActorMovement(function(){o._touchEnd(e,t,i,n)})}if(!s){if(this._tapInteractive(e,t))return;var f=this.actorManager.getActorsOnCell(i.cell);if(f[0]&&f[0].position===i)return void(0===i.dist&&f[0].tap(e,t,c.camera))}this.clearHighlights();var m=i.cell;return(s=s||c.getFirstMapFlag(i.cell))?this.gotoNeighbourMap(s,i.cell,a,r):void this._movePlayerOnMap(m)}},c.prototype._tapFightWithSpell';

s = s.replace(oldTapRole, newTapRole);
console.log('3. _tapRoleplay patched successfully.');

// 4 and 5 are GONE. The old section 4 was a boundary patch whose `end` marker
// contained `finally{` - i.e. its own output - so it could never match a raw
// bundle, and its `-1` index path would have replaced the first 38 characters of
// the file had the start marker gone missing too. Section 5 retargeted a
// `_guardTimer` string that exists in no bundle copy. Both reported as a
// "[SKIP]" and the build carried on, which is how a required client-side
// protection went missing unnoticed (KNOWN_GAPS G11/G12).
//
// 20-31. Map-transition resilience (KNOWN_GAPS G12). These are the guards that
// keep a stalled or throwing map load from locking `foreground` forever: the
// 3.5 s transition watchdog, the series/theme timeouts, and try/catch/finally
// around the render callbacks. They live here because make-patch-rules.py
// extracts the rules from THIS file into patch-rules.json/.txt, which is what
// ClientBundle.cs applies after an auto-update - rules that only exist in a
// side script are reverted by the next build.
// tools/apply-map-resilience.js keeps the same strings and is the verifier
// (`node tools/apply-map-resilience.js --check`); agent.regression asserts the
// two stay in step.

const resil20Old = 'this.on("transformStart",function(e,t){if(n.locked)return n.cancelTransform();';
const resil20New = 'this.on("transformStart",function(e,t){if(window.isoEngine&&window.isoEngine.mapRenderer&&window.isoEngine.mapRenderer.isReady){if(n.locked){n.unlock("loadMap"),n.unlock("quickReconnection"),n.locked=!1}}if(n.locked)return n.cancelTransform();';
// #20 camera edge slider transformStart auto-unlock
if (s.indexOf(resil20Old) === -1) { console.error('[SKIP] resilience #20: needle absent (client moved this text)'); }
else { s = s.replace(resil20Old, resil20New); console.log('#20 camera edge slider transformStart auto-unlock.'); }

const resil21Old = 's.prototype._resetTransition=function(){this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1,this._mapSceneTransitionGraphic=null}';
const resil21New = 's.prototype._resetTransition=function(){if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}}this._mapSceneTransitionGraphic=null,this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1}';
// #21 IsoEngine._resetTransition safe graphic removal
if (s.indexOf(resil21Old) === -1) { console.error('[SKIP] resilience #21: needle absent (client moved this text)'); }
else { s = s.replace(resil21Old, resil21New); console.log('#21 IsoEngine._resetTransition safe graphic removal.'); }

const resil22Old = 's.prototype.launchMapTransition=function(e){var t=this;this._loadingProgress.loadAssets(function(){t.mapScene.setShader("mapTransition"),t.emit("launchMapTransition",e),t._activateMapScene()?(t._loadingProgress.progressEgg.alpha=1,t.mapScene.renderingParams.ratio=1,t._saveImageForTransition("black"),t._startLoading()):t._loadingFadeInTransitionRunning===!1&&(t._isMapLoading?t._startLoading():(e&&t._makeUserActorWalkInDirection(e,!1),t._runLoadingFadeInTransition()))})}';
const resil22New = 's.prototype.launchMapTransition=function(e){var t=this;if(this._transitionWatchdog)clearTimeout(this._transitionWatchdog);var _selfTr=this;this._transitionWatchdog=setTimeout(function(){if(_selfTr._isMapLoading||_selfTr._mapSceneTransitionGraphic||(window.foreground&&window.foreground.locked)){console.warn("[IsoEngine] Transition watchdog fired: clearing stalled black screen/loading state");try{if(_selfTr._mapSceneTransitionGraphic){_selfTr._mapSceneTransitionGraphic.remove();_selfTr._mapSceneTransitionGraphic=null;}if(_selfTr.mapScene){_selfTr.mapScene.setShader("unfiltering");_selfTr.mapScene.renderingParams.ratio=0.15;_selfTr._hideLoadingProgress&&_selfTr._hideLoadingProgress();_selfTr.mapScene.requireCompleteRefresh();}if(window.foreground){window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;window.foreground.hideBorderArrow&&window.foreground.hideBorderArrow();}_selfTr._isMapLoading=!1;_selfTr._loadingFadeInTransitionRunning=!1;_selfTr._waitingForMessage=!1;}catch(wEx){console.error("[IsoEngine] Watchdog error:",wEx);}}},3500);this._loadingProgress.loadAssets(function(){t.mapScene.setShader("mapTransition"),t.emit("launchMapTransition",e),t._activateMapScene()?(t._loadingProgress.progressEgg.alpha=1,t.mapScene.renderingParams.ratio=1,t._saveImageForTransition("black"),t._startLoading()):t._loadingFadeInTransitionRunning===!1&&(t._isMapLoading?t._startLoading():(e&&t._makeUserActorWalkInDirection(e,!1),t._runLoadingFadeInTransition()))})}';
// #22 IsoEngine.launchMapTransition watchdog
if (s.indexOf(resil22Old) === -1) { console.error('[SKIP] resilience #22: needle absent (client moved this text)'); }
else { s = s.replace(resil22Old, resil22New); console.log('#22 IsoEngine.launchMapTransition watchdog.'); }

const resil23Old = 's.prototype._runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;new h(this._loadingProgress.progressEgg';
const resil23New = 's.prototype._runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;if(this._transitionWatchdog){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}new h(this._loadingProgress.progressEgg';
// #23 IsoEngine._runLoadingFadeOutTransition watchdog cancellation
if (s.indexOf(resil23Old) === -1) { console.error('[SKIP] resilience #23: needle absent (client moved this text)'); }
else { s = s.replace(resil23Old, resil23New); console.log('#23 IsoEngine._runLoadingFadeOutTransition watchdog cancellation.'); }

const resil24Old = 'loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap"),this._mapLoadingMessage=e,this._loadingFadeInTransitionRunning===!1&&(this._waitingForMessage?this._startLoading():this.launchMapTransition())}';
const resil24New = 'loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap"),this._mapLoadingMessage=e,this._loadingFadeInTransitionRunning===!1?(this._waitingForMessage?this._startLoading():this.launchMapTransition()):(function(mSelf){setTimeout(function(){if(mSelf._mapLoadingMessage){mSelf._loadingFadeInTransitionRunning=!1;mSelf._startLoading();}},400)})(this)}';
// #24 IsoEngine.loadMap teleportation potion un-stalling
if (s.indexOf(resil24Old) === -1) { console.error('[SKIP] resilience #24: needle absent (client moved this text)'); }
else { s = s.replace(resil24Old, resil24New); console.log('#24 IsoEngine.loadMap teleportation potion un-stalling.'); }

const resil25Old = 'if(t!==C){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return console.error("Unable to get tacticalThemeData information for",e.tacticalModeId,i),t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){n.tacticalMode.loadThemeMapForMap(f,i||t,h.cells,e)}],function(t){return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h.id===C&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}}';
const resil25New = 'if(t!==C){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;var _seriesDone=!1,_seriesTimer=setTimeout(function(){if(!_seriesDone){_seriesDone=!0;console.warn("[IsoEngine] _startLoading series timeout fallback");if(h){null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h};}else{n._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}}}},2500);l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return console.error("Unable to get tacticalThemeData information for",e.tacticalModeId,i),t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){var d=!1,tm=setTimeout(function(){d||(d=!0,console.warn("TacticalMode theme timeout, bypassing block"),e())},1000);try{n.tacticalMode.loadThemeMapForMap(f,i||t,h?h.cells:null,function(t){d||(d=!0,clearTimeout(tm),e(t))})}catch(err){d||(d=!0,clearTimeout(tm),e())}}],function(t){if(_seriesDone)return;_seriesDone=!0;clearTimeout(_seriesTimer);return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h&&(h.id==C||!h.id)&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}}';
// #25 IsoEngine._startLoading series safeguard + safe tacticalMode cells
if (s.indexOf(resil25Old) === -1) { console.error('[SKIP] resilience #25: needle absent (client moved this text)'); }
else { s = s.replace(resil25Old, resil25New); console.log('#25 IsoEngine._startLoading series safeguard + safe tacticalMode cells.'); }

const resil26Old = 's.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){return null!==w&&C!==t.id?void i._loadMapAssets():(window.foreground.show(),window.foreground.unlock("loadMap"),c.fillPathGrid(t),i._updateMapInfoData(e),i.actorManager.updateMapInfoData(e),i.actorManager.unpause(),i.actorManager.userActor.moving=!1,_.unlockMessages(),i.emit("mapLoaded"),setTimeout(function(){O.checkNonUsedDeepLink()},0),i._runLoadingFadeOutTransition(),i.interactiveBlink&&i.highlightInteractivesWithDifferentType(),C=null,w=null,void(i._isMapLoading=!1))})}';
const resil26New = 's.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){if(null!==w&&C!==t.id)return void i._loadMapAssets();try{window.foreground.show();window.foreground.unlock("loadMap");c.fillPathGrid(t);i._updateMapInfoData(e);i.actorManager.updateMapInfoData(e);i.actorManager.unpause();i.actorManager.userActor.moving=!1;_.unlockMessages();i.emit("mapLoaded");setTimeout(function(){O.checkNonUsedDeepLink()},0);i._runLoadingFadeOutTransition();i.interactiveBlink&&i.highlightInteractivesWithDifferentType();}catch(mErr){console.error("[IsoEngine] _loadMapAssets execution error:",mErr);i._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}}finally{C=null;w=null;i._isMapLoading=!1;}})}';
// #26 IsoEngine._loadMapAssets try/catch/finally protection
if (s.indexOf(resil26Old) === -1) { console.error('[SKIP] resilience #26: needle absent (client moved this text)'); }
else { s = s.replace(resil26Old, resil26New); console.log('#26 IsoEngine._loadMapAssets try/catch/finally protection.'); }

const resil27Old = 's.prototype._saveImageForTransition=function(e){var t=v,i=t*this.mapScene.h/this.mapScene.w,n=this.mapScene.renderer,o=n.startTextureUsage(t,i,1,null,"linear");if(n.startTextureRendering(o,0,t,0,i,!1),"black"===e)this.mapScene.clear(0,0,0,1);else{var a=t/this.mapScene.w;n.save(),n.scale(a,a),this.mapScene.render(),n.restore()}var r=!0;n.stopTextureRendering(r),null===this._mapSceneTransitionGraphic?this._mapSceneTransitionGraphic=new m({w:this.mapScene.w,h:this.mapScene.h,scene:this.mapScene,position:0,isHudElement:!0,alpha:1},o.texture):(this._mapSceneTransitionGraphic.clear(),this._mapSceneTransitionGraphic.texture=o.texture,this._mapSceneTransitionGraphic.show())}';
const resil27New = 's.prototype._saveImageForTransition=function(e){try{var t=v,i=t*this.mapScene.h/this.mapScene.w,n=this.mapScene.renderer,o=n.startTextureUsage(t,i,1,null,"linear");if(n.startTextureRendering(o,0,t,0,i,!1),"black"===e)this.mapScene.clear(0,0,0,1);else{var a=t/this.mapScene.w;n.save(),n.scale(a,a),this.mapScene.render(),n.restore()}var r=!0;n.stopTextureRendering(r);if(!this._mapSceneTransitionGraphic||typeof this._mapSceneTransitionGraphic.clear!=="function"){this._mapSceneTransitionGraphic=new m({w:this.mapScene.w,h:this.mapScene.h,scene:this.mapScene,position:0,isHudElement:!0,alpha:1},o.texture);}else{try{this._mapSceneTransitionGraphic.clear();}catch(cEx){}this._mapSceneTransitionGraphic.texture=o.texture;this._mapSceneTransitionGraphic.show();}}catch(trEx){console.error("[IsoEngine] _saveImageForTransition error:",trEx);}}';
// #27 IsoEngine._saveImageForTransition undefined .clear() guard
if (s.indexOf(resil27Old) === -1) { console.error('[SKIP] resilience #27: needle absent (client moved this text)'); }
else { s = s.replace(resil27Old, resil27New); console.log('#27 IsoEngine._saveImageForTransition undefined .clear() guard.'); }

const resil28Old = 'n.prototype._updateSpriteHighlight=function(e){var t=e.spriteIndex*this._spriteByteSize';
const resil28New = 'n.prototype._updateSpriteHighlight=function(e){if(!this._longView||!e)return;var t=e.spriteIndex*this._spriteByteSize';
// #28 Batch._updateSpriteHighlight null _longView guard
if (s.indexOf(resil28Old) === -1) { console.error('[SKIP] resilience #28: needle absent (client moved this text)'); }
else { s = s.replace(resil28Old, resil28New); console.log('#28 Batch._updateSpriteHighlight null _longView guard.'); }

const resil29Old = 'n.prototype._loadAtlas=function(e,t){var i=this;this._canvas.width=e.width,this._atlasWidth=this._canvas.width,this._canvas.height=e.height,this._atlasHeight=this._canvas.height,this._atlasGraphics=e.graphicsPositions;var n=Object.keys(this._atlasGraphics);s.forEach(n,function(t,n){var o=i._atlasGraphics[t],r=o.jpg?"jpg":"png",s=r+"/"+t+"."+r,l=a.IMG_PATH+s;e.useUiPath&&(l="ui/tactical/"+t+".png"),c.loadImage(l,function(e){var t=o.sx,a=o.sy,r=o.sw,s=o.sh,c=o.cx||0,l=o.cy||0,d=o.cw||r,u=o.ch||s;return c+d>e.width&&(d=e.width-c),l+u>e.height&&(u=e.height-l),i._context.drawImage(e,c,l,d,u,t,a,r,s),n()})},function(){i._atlasTexture&&i._atlasTexture.release(),i._atlasTexture=i.mapScene.createTexture(i._canvas,"tacticalModeMapAtlas"+i._themeMapId,"linear"),i._canvas.width=1,i._canvas.height=1,t()})}';
const resil29New = 'n.prototype._loadAtlas=function(e,t){var i=this;this._canvas.width=e.width,this._atlasWidth=this._canvas.width,this._canvas.height=e.height,this._atlasHeight=this._canvas.height,this._atlasGraphics=e.graphicsPositions;var n=Object.keys(this._atlasGraphics);s.forEach(n,function(t,n){var o=i._atlasGraphics[t],r=o.jpg?"jpg":"png",s=r+"/"+t+"."+r,l=a.IMG_PATH+s;e.useUiPath&&(l="ui/tactical/"+t+".png"),c.loadImage(l,function(e){try{if(e&&e.width>0&&e.height>0){var t=o.sx,a=o.sy,r=o.sw,s=o.sh,c=o.cx||0,l=o.cy||0,d=o.cw||r,u=o.ch||s;c>=0&&l>=0&&c<e.width&&l<e.height&&(d=Math.max(0,Math.min(d,e.width-c)),u=Math.max(0,Math.min(u,e.height-l)),d>0&&u>0&&r>0&&s>0&&i._context.drawImage(e,c,l,d,u,t,a,r,s))}}catch(err){}finally{n()}})},function(){i._atlasTexture&&i._atlasTexture.release(),i._atlasTexture=i.mapScene.createTexture(i._canvas,"tacticalModeMapAtlas"+i._themeMapId,"linear"),i._canvas.width=1,i._canvas.height=1,t()})}';
// #29 TacticalMode._loadAtlas try/catch/finally with guaranteed step callback
if (s.indexOf(resil29Old) === -1) { console.error('[SKIP] resilience #29: needle absent (client moved this text)'); }
else { s = s.replace(resil29Old, resil29New); console.log('#29 TacticalMode._loadAtlas try/catch/finally with guaranteed step callback.'); }

const resil30Old = 'this._wallBatch.setTexture(n._atlasTexture);for(var s,c,h=0;h<a.NB_CELLS;h++){var f=r.cellCoord[h];if(1===(5&e[h].l)){var m=n.floorGraphics[f.x%2][0];';
const resil30New = 'this._wallBatch.setTexture(n._atlasTexture);try{if(e){for(var s,c,h=0;h<a.NB_CELLS;h++){var f=r.cellCoord[h];if(e[h]&&void 0!==e[h].l&&1===(5&e[h].l)){var m=n.floorGraphics&&n.floorGraphics[f.x%2]&&n.floorGraphics[f.x%2][0];';
// #30 TacticalMode._applyThemeToMap defined-cell check (opens the safe block)
if (s.indexOf(resil30Old) === -1) { console.error('[SKIP] resilience #30: needle absent (client moved this text)'); }
else { s = s.replace(resil30Old, resil30New); console.log('#30 TacticalMode._applyThemeToMap defined-cell check (opens the safe block).'); }

const resil31Old = '}}return n._floorBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._floorBatch.hide(),n._wallBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._wallBatch.hide(),i()}';
const resil31New = '}}}n._floorBatch&&n._floorBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._floorBatch&&n._floorBatch.hide(),n._wallBatch&&n._wallBatch.finalize(n._atlasWidth,n._atlasHeight,{sort:!0}),n._wallBatch&&n._wallBatch.hide()}catch(err){console.warn("safe applyTheme error:",err)}finally{return i()}}';
// #31 TacticalMode._applyThemeToMap guaranteed callback via finally (closes the safe block)
if (s.indexOf(resil31Old) === -1) { console.error('[SKIP] resilience #31: needle absent (client moved this text)'); }
else { s = s.replace(resil31Old, resil31New); console.log('#31 TacticalMode._applyThemeToMap guaranteed callback via finally (closes the safe block).'); }

// 6. Neutralise the server log writer, but KEEP its key: the logger does
//    `new I[type]` and removing the entry made every log call raise
//    "Unknown writer type: server" (77 of them in one session).
const logWriterOld = 'var I={console:a,server:r}';
const logWriterNew = 'var I={console:a,server:function(){this.addChannel=function(){}}}';
if (s.indexOf(logWriterOld) === -1) {
    console.error('[SKIP] Patch 6: Cannot find logger writer map!');
}
s = s.replace(logWriterOld, logWriterNew);
console.log('6. Server log writer removed from logger writer map.');

// 7. Remove Adjust SDK initialize call from analytics startup sequence
const adjustInitOld = 'N.initHaapiModule(t.logger,t.connectionManager),I.init(window.Config.analytics,N),T.initialize(t.logger,window.Config.adjust,window.Adjust,window.AdjustConfig,window.AdjustEvent),S.initialize()';
const adjustInitNew = 'N.initHaapiModule(t.logger,t.connectionManager),I.init(window.Config.analytics,N),S.initialize()';
if (s.indexOf(adjustInitOld) === -1) {
    console.error('[SKIP] Patch 7: Cannot find Adjust SDK initialize call!');
}
s = s.replace(adjustInitOld, adjustInitNew);
console.log('7. Adjust SDK T.initialize() call removed from startup sequence.');

// 8. Void performance telemetry fetch (/logger POST with GPU/RAM/FPS data)
const perfFetchOld = 'window.fetch(p+"/logger",{method:"post",headers:f,body:JSON.stringify({channelName:"error",message:u,data:{label:"performance",sessionId:h}})})';
const perfFetchNew = 'Promise.resolve()';
if (s.indexOf(perfFetchOld) === -1) {
    console.error('[SKIP] Patch 8: Cannot find performance telemetry fetch!');
}
s = s.replace(perfFetchOld, perfFetchNew);
console.log('8. Performance telemetry fetch voided.');

// 9. Spoof WebGL GPU fingerprint — fixed neutral vendor/renderer strings
const webglFnOld = 'function Qe(){var e,t=document.createElement("canvas"),i=null!==(e=t.getContext("webgl"))&&void 0!==e?e:t.getContext("experimental-webgl");if(i&&"getExtension"in i){var n=i.getExtension("WEBGL_debug_renderer_info");if(n)return{vendor:(i.getParameter(n.UNMASKED_VENDOR_WEBGL)||"").toString(),renderer:(i.getParameter(n.UNMASKED_RENDERER_WEBGL)||"").toString()}}}';
const webglFnNew = 'function Qe(){return{vendor:"ARM",renderer:"Mali-G78"}}';
if (s.indexOf(webglFnOld) === -1) {
    console.error('[SKIP] Patch 9: Cannot find WebGL fingerprint function Qe!');
}
s = s.replace(webglFnOld, webglFnNew);
console.log('9. WebGL GPU fingerprint spoofed to fixed ARM/Mali-G78 profile.');

// 10. Void logger writer raw fetch call (belt-and-suspenders on top of fetch intercept §20)
const logFetchOld = 'window.fetch(s+"/logger",{method:"post",headers:c,body:JSON.stringify({channelName:e,message:i.message,data:i.data})})';
const logFetchNew = 'void 0';
if (s.indexOf(logFetchOld) === -1) {
    console.error('[SKIP] Patch 10: Cannot find logger writer fetch call!');
}
s = s.replace(logFetchOld, logFetchNew);
console.log('10. Logger writer raw fetch call voided at source.');

// 11. Short-circuit font fingerprinter probe-map builder — return empty object
const fontProbeOld = 'd=function(){for(var e={},t=function(t){e[t]=bt.map(function(e){return c(t,e)})},i=0,n=gt;i<n.length;i++){var o=n[i];t(o)}return e}';
const fontProbeNew = 'd=function(){return{}}';
if (s.indexOf(fontProbeOld) === -1) {
    console.error('[SKIP] Patch 11: Cannot find font fingerprinter probe-map builder!');
}
s = s.replace(fontProbeOld, fontProbeNew);
console.log('11. Font fingerprinter probe-map builder short-circuited.');

// 12. Re-read the viewport on every layout.
//     updateScreen cached viewportWidth/Height with `||`, so _resizeUi() kept
//     laying the game out at the size measured during boot and the window no
//     longer stretched it. The mobile bundle already re-reads them.
const viewportOld = 'f.viewportWidth=f.viewportWidth||l.document.documentElement.clientWidth,f.viewportHeight=f.viewportHeight||l.document.documentElement.clientHeight';
const viewportNew = 'f.viewportWidth=l.document.documentElement.clientWidth||l.innerWidth||1024,f.viewportHeight=l.document.documentElement.clientHeight||l.innerHeight||640';
if (s.indexOf(viewportOld) === -1) {
    console.error('[SKIP] Patch 12: Cannot find cached viewport in updateScreen!');
}
s = s.replace(viewportOld, viewportNew);
console.log('12. Layout re-reads the viewport size on every updateScreen().');

fs.writeFileSync(targetPath, s, 'utf8');
console.log('All changes written to ' + targetPath + ' successfully!');
