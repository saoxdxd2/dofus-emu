const fs = require('fs');
const path = require('path');

const scriptFile = path.resolve(__dirname, '../desktop/client/www/build/script.js');
let scriptContent = fs.readFileSync(scriptFile, 'utf8');

// 1. Fix _resetTransition: unconditionally initialize this._mapSceneTransitionGraphic = null
const oldResetTr = 's.prototype._resetTransition=function(){if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}this._mapSceneTransitionGraphic=null;}this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1}';
const newResetTr = 's.prototype._resetTransition=function(){if(this._mapSceneTransitionGraphic){try{this._mapSceneTransitionGraphic.remove()}catch(e){}}this._mapSceneTransitionGraphic=null,this._loadingFadeInTransitionRunning=!1,this._loadingProgressVisible=!1,this._waitingForMessage=!1,this._mapLoadingMessage=null,this._isMapLoading=!1}';

if (!scriptContent.includes(oldResetTr)) {
  console.error('[ERROR] oldResetTr not found in script.js');
  process.exit(1);
}
scriptContent = scriptContent.replace(oldResetTr, newResetTr);
console.log('[OK] Patched _resetTransition');

// 2. Fix _saveImageForTransition: safe guard against null/undefined, and wrap in try/catch
const oldSaveImage = 's.prototype._saveImageForTransition=function(e){var t=v,i=t*this.mapScene.h/this.mapScene.w,n=this.mapScene.renderer,o=n.startTextureUsage(t,i,1,null,"linear");if(n.startTextureRendering(o,0,t,0,i,!1),"black"===e)this.mapScene.clear(0,0,0,1);else{var a=t/this.mapScene.w;n.save(),n.scale(a,a),this.mapScene.render(),n.restore()}var r=!0;n.stopTextureRendering(r),null===this._mapSceneTransitionGraphic?this._mapSceneTransitionGraphic=new m({w:this.mapScene.w,h:this.mapScene.h,scene:this.mapScene,position:0,isHudElement:!0,alpha:1},o.texture):(this._mapSceneTransitionGraphic.clear(),this._mapSceneTransitionGraphic.texture=o.texture,this._mapSceneTransitionGraphic.show())}';
const newSaveImage = 's.prototype._saveImageForTransition=function(e){try{var t=v,i=t*this.mapScene.h/this.mapScene.w,n=this.mapScene.renderer,o=n.startTextureUsage(t,i,1,null,"linear");if(n.startTextureRendering(o,0,t,0,i,!1),"black"===e)this.mapScene.clear(0,0,0,1);else{var a=t/this.mapScene.w;n.save(),n.scale(a,a),this.mapScene.render(),n.restore()}var r=!0;n.stopTextureRendering(r);if(!this._mapSceneTransitionGraphic||typeof this._mapSceneTransitionGraphic.clear!=="function"){this._mapSceneTransitionGraphic=new m({w:this.mapScene.w,h:this.mapScene.h,scene:this.mapScene,position:0,isHudElement:!0,alpha:1},o.texture);}else{try{this._mapSceneTransitionGraphic.clear();}catch(cEx){}this._mapSceneTransitionGraphic.texture=o.texture;this._mapSceneTransitionGraphic.show();}}catch(trEx){console.error("[IsoEngine] _saveImageForTransition error:",trEx);}}';

if (!scriptContent.includes(oldSaveImage)) {
  console.error('[ERROR] oldSaveImage not found in script.js');
  process.exit(1);
}
scriptContent = scriptContent.replace(oldSaveImage, newSaveImage);
console.log('[OK] Patched _saveImageForTransition');

fs.writeFileSync(scriptFile, scriptContent, 'utf8');
console.log('Successfully updated desktop/client/www/build/script.js');

// 3. Bump version in standalone.html to mapfix9
const htmlFile = path.resolve(__dirname, '../desktop/client/www/standalone.html');
let htmlContent = fs.readFileSync(htmlFile, 'utf8');
htmlContent = htmlContent.replace(/v=3\.14\.2_mapfix8/g, 'v=3.14.2_mapfix9');
fs.writeFileSync(htmlFile, htmlContent, 'utf8');
console.log('Successfully bumped standalone.html to mapfix9');
