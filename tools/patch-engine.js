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

// 4. Patch launchMapTransition through _loadMapAssets to end of file
const launchMarker = 's.prototype.launchMapTransition=function(e){';
const startLaunch = s.indexOf(launchMarker);
const endMarker = 'finally{C=null;w=null;i._isMapLoading=!1;}})}}]);';
const endLaunch = s.indexOf(endMarker, startLaunch);
if (startLaunch === -1 || endLaunch === -1) {
    console.error('[SKIP] launchMapTransition boundary not found (client changed?)');

}
const oldLaunchBlock = s.substring(startLaunch, endLaunch + endMarker.length);
console.log('Found oldLaunchBlock length:', oldLaunchBlock.length);

const newLaunchBlock = 's.prototype.launchMapTransition=function(e){var t=this;if(this._transitionWatchdog)clearTimeout(this._transitionWatchdog);var _selfTr=this;this._transitionWatchdog=setTimeout(function(){if(_selfTr._isMapLoading||_selfTr._mapSceneTransitionGraphic||(window.foreground&&window.foreground.locked)){console.warn("[IsoEngine] Transition watchdog fired: clearing stalled loading state");try{if(_selfTr._mapSceneTransitionGraphic){_selfTr._mapSceneTransitionGraphic.remove();_selfTr._mapSceneTransitionGraphic=null;}if(_selfTr.mapScene){_selfTr.mapScene.setShader("unfiltering");_selfTr.mapScene.renderingParams.ratio=0.15;_selfTr._hideLoadingProgress&&_selfTr._hideLoadingProgress();_selfTr.mapScene.requireCompleteRefresh();}if(window.foreground){window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;window.foreground.hideBorderArrow&&window.foreground.hideBorderArrow();}_selfTr._isMapLoading=!1;_selfTr._loadingFadeInTransitionRunning=!1;_selfTr._waitingForMessage=!1;if(_.unlockMessages)_.unlockMessages();}catch(wEx){console.error("[IsoEngine] Watchdog error:",wEx);}}},3500);this._loadingProgress.loadAssets(function(){if(t.mapScene){t.mapScene.setShader("unfiltering");t.mapScene.renderingParams.ratio=0.15;}t.emit("launchMapTransition",e);if(t._activateMapScene()){t._loadingProgress.progressEgg.alpha=1;t._loadingProgress.progressEgg.x=t.mapScene.w/2;t._loadingProgress.progressEgg.y=t.mapScene.h/2;t._loadingProgress.show();t._startLoading();}else if(t._loadingFadeInTransitionRunning===!1){if(t._isMapLoading){t._startLoading();}else{if(e)t._makeUserActorWalkInDirection(e,!1);t._runLoadingFadeInTransition();}}});},s.prototype.cancelMapTransition=function(e){this.emit("cancelMapTransition",e);if(this.actorManager&&this.actorManager.userActor){this.actorManager.userActor.setOnScreenPosition(this.actorManager.userActor.position);this._makeUserActorWalkInDirection(e,!0);}this._runLoadingFadeOutTransition();this._mapLoadingMessage=null;this._waitingForMessage=!1;},s.prototype._saveImageForTransition=function(e){try{if(!this._mapSceneTransitionGraphic||typeof this._mapSceneTransitionGraphic.clear!=="function"){if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}}this._mapSceneTransitionGraphic=null;}else{try{this._mapSceneTransitionGraphic.remove()}catch(e){}this._mapSceneTransitionGraphic=null;}}catch(ex){console.warn("[IsoEngine] _saveImageForTransition safe handled:",ex);}},s.prototype._runLoadingFadeInTransition=function(){this._loadingFadeInTransitionRunning=!0;this._waitingForMessage=!0;this.showLoadingProgress(0);new h(this._loadingProgress.progressEgg,["alpha","scaleX","scaleY"]).from({alpha:0,scaleX:.8,scaleY:.8}).to({alpha:1,scaleX:1,scaleY:1},15,f.backOut,1.5).start();if(this.mapScene){this.mapScene.setShader("unfiltering");this.mapScene.renderingParams.ratio=0.15;}this._loadingFadeInTransitionRunning=!1;this._startLoading();},s.prototype._runLoadingFadeOutTransition=function(){var e=this,t=this.mapScene;if(this._transitionWatchdog){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(ex){}this._mapSceneTransitionGraphic=null;}this._hideLoadingProgress();if(t){t.setShader("unfiltering");t.renderingParams.ratio=0.15;t.requireCompleteRefresh();}if(window.foreground){window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;window.foreground.hideBorderArrow&&window.foreground.hideBorderArrow();}this._isMapLoading=!1;this._loadingFadeInTransitionRunning=!1;this._waitingForMessage=!1;if(A.isRoleplayMode&&this.actorManager&&this.actorManager.userActor){this.makeActorWalkIn(this.actorManager.userActor);}},s.prototype.makeActorWalkIn=function(e,t,i){t=t||100;var n,o=this.mapScene,a=e.x,s=e.y,c=Math.abs(o.l-a),l=Math.abs(o.l+o.w-a),d=Math.abs(o.t-s),u=Math.abs(o.t+o.h-s),p=c<t,h=l<t,f=d<t,m=u<t;if(p)n=f?1:m?7:0;else if(h)n=f?3:m?5:4;else if(f)n=2;else{if(!m)return;n=6}var b=r.ANGLE_PER_DIRECTION[n];e.x-=Math.cos(b)*y,e.y-=Math.sin(b)*y,e.walkToSceneCoordinate(a,s,n,z,i)},s.prototype.loadMap=function(e){this.changeMapTimeout?(window.clearTimeout(this.changeMapTimeout),this.changeMapTimeout=null):window.foreground.lock("loadMap");this._mapLoadingMessage=e;if(this._loadingFadeInTransitionRunning===!1){if(this._waitingForMessage){this._startLoading();}else{this.launchMapTransition();}}},s.prototype.reloadMap=function(e){var t=this;if(this.mapRenderer.isReady){this._updateMapInfoData(e,{noMovementWaitReset:!0});if(window.foreground){window.foreground.show();window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;}this.actorManager.updateMapInfoData(e);this.actorManager.unpause();this.clearPendingMovement();this.emit("mapLoaded",{isReload:!0});this.interactiveBlink&&this.highlightInteractivesWithDifferentType();}else{this.mapRenderer.once("ready",function(){t.reloadMap(e);});}};var w=null,C=null;s.prototype._startLoading=function(){var e=this._mapLoadingMessage;if(null!==e){this._mapLoadingMessage=null,this._waitingForMessage=!1,this.emit("mapChange",e);if(this.actorManager&&this.actorManager.userActor){this.actorManager.userActor.staticAnim();this.actorManager.pause();}var t=e.mapId,i=e.obstacleMapId;if(t!==C||!this.mapRenderer||!this.mapRenderer.isReady){this.releaseMap(C),C=t,this._isMapLoading=!0;var n=this,s=r.MAP_PATH+i+".json",c=r.MAP_PATH+t+".json",u=null,h=null,f=r.DEFAULT_TACTICAL_THEME_MAP;var _seriesDone=!1,_seriesTimer=setTimeout(function(){if(!_seriesDone){_seriesDone=!0;console.warn("[IsoEngine] _startLoading series timeout fallback");if(h){null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h};}else{n._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}_.unlockMessages();}}},2500);l.series([function(e){return i?void d.loadJson(s,function(t){return u=t,e()}):e()},function(e){d.loadJson(c,function(t){return h=t,u&&(h.obstacleMapId=i,h.cells=u.cells,h.midgroundLayer=o(h.midgroundLayer,u.midgroundLayer),h.atlasLayout=a(h.atlasLayout,u.atlasLayout)),e()})},function(t){p.getDataMap("TacticalThemes",[e.tacticalModeId],null,function(i,n){if(i)return t();if(!n)return t();var o=n[e.tacticalModeId];return o&&(f=o.mapId),t()})},function(e){var d=!1,tm=setTimeout(function(){d||(d=!0,e())},1000);try{n.tacticalMode.loadThemeMapForMap(f,i||t,h?h.cells:null,function(t){d||(d=!0,clearTimeout(tm),e(t))})}catch(err){d||(d=!0,clearTimeout(tm),e())}}],function(t){if(_seriesDone)return;_seriesDone=!0;clearTimeout(_seriesTimer);return t?window.dofus.disconnect("LOADING_MAP_ERROR "+t):h===r.EMPTY_JSON?window.dofus.disconnect("ASSET_MISSING"):void(h&&(h.id==C||!h.id)&&(null===w?(w={msg:e,mapData:h},n._loadMapAssets()):w={msg:e,mapData:h}))})}else{this.reloadMap(e);this._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.locked=!1;}_.unlockMessages();this._isMapLoading=!1;}}},s.prototype._loadMapAssets=function(){var e=w.msg,t=w.mapData,i=this;this.mapRenderer.setMap(w,function(){if(null!==w&&C!==t.id)return void i._loadMapAssets();try{window.foreground.show();window.foreground.unlock("loadMap");c.fillPathGrid(t);i._updateMapInfoData(e);i.actorManager.updateMapInfoData(e);i.actorManager.unpause();if(i.actorManager.userActor)i.actorManager.userActor.moving=!1;i.clearPendingMovement();_.unlockMessages();i.emit("mapLoaded");setTimeout(function(){O.checkNonUsedDeepLink()},0);i._runLoadingFadeOutTransition();i.interactiveBlink&&i.highlightInteractivesWithDifferentType();}catch(mErr){console.error("[IsoEngine] _loadMapAssets execution error:",mErr);i._runLoadingFadeOutTransition();if(window.foreground){window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");window.foreground.locked=!1;}_.unlockMessages();}finally{C=null;w=null;i._isMapLoading=!1;}})}}]);';

if (oldLaunchBlock.length > 60000) {
    console.error('[SKIP] launchMapTransition block matched ' + oldLaunchBlock.length +
                  ' chars (markers landed at the extremes) - not replacing');
} else {
    s = s.replace(oldLaunchBlock, newLaunchBlock);
}
console.log('4. launchMapTransition through _loadMapAssets patched successfully.');

// 5. Patch AssetLoader guardTimer timeout to 1200ms
s = s.replace('this._guardTimer=setTimeout(function(){if(_self.onLoadedCallback){console.warn("[MapRenderer] Safety watchdog timeout fired', 'this._guardTimer=setTimeout(function(){if(_self.onLoadedCallback){console.warn("[MapRenderer] Fast safety timer fired');
s = s.replace('emit("ready");_self.obsolete=!0;}}},3500);', 'emit("ready");_self.obsolete=!0;}}},1200);');
console.log('5. Fast safety timer (1200ms) patched.');

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

fs.writeFileSync(targetPath, s, 'utf8');
console.log('All changes written to ' + targetPath + ' successfully!');
