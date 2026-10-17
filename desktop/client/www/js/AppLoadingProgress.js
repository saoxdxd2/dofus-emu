'use strict';

function AppLoadingProgress() {
	this._domCreated = false;
	this._progressBarBg = null;
	this._progressBarFg = null;
	this._background = null;
	this._maxForegroundSize = 0;
	this._minForegroundSize = 0;
}

AppLoadingProgress.prototype.show = function (msg) {
	if (!this._domCreated) {
		this._createDom(msg);
	}
	this._background.style.display = 'block';
};

AppLoadingProgress.prototype.hide = function () {
	if (!this._domCreated) {
		return;
	}
	this._background.style.display = 'none';
};

AppLoadingProgress.prototype.setProgress = function (percentage) {
	if (!this._domCreated) {
		return;
	}
	percentage = Math.max(0, Math.min(1, percentage));
	var range = this._maxForegroundSize - this._minForegroundSize;
	this._progressBarFg.style.width = Math.round(this._minForegroundSize + range * percentage) + 'px';
};

AppLoadingProgress.prototype._createDom = function (msg) {
	// preparation
	var screenWidth = window.innerWidth;
	var screenHeight = window.innerHeight;
	var barWidth = Math.round(screenWidth / 2.5);
	var barHeight = Math.round(screenWidth / 50);
	var barBorderRadius = Math.round(barHeight / 2.5);
	this._maxForegroundSize = barWidth - 1;
	this._minForegroundSize = barBorderRadius * 2;
	var msgSize = barHeight;

	// Background color of the body in the loading
	window.document.body.style.backgroundColor = '#222222';

	// background
	var background = window.document.createElement('div');
	this._background = background;
	window.document.body.appendChild(background);
	background.style.background = '#222222';
	background.style.position = 'absolute';
	background.style.width = screenWidth + 'px';
	background.style.height = screenHeight + 'px';
	background.style.top = '0px';
	background.style.left = '0px';

	// progress bar background
	var progressBarBg = window.document.createElement('div');
	this._progressBarBg = progressBarBg;
	progressBarBg.style.width = barWidth + 'px';
	progressBarBg.style.height = barHeight + 'px';
	progressBarBg.style.lineHeight = barHeight + 'px';
	progressBarBg.style.backgroundColor = '#BBFFAA';
	progressBarBg.style.borderRadius = barBorderRadius + 'px';
	progressBarBg.style.position = 'relative';
	progressBarBg.style.backgroundColor = '#111111';
	progressBarBg.style.borderBottom = '1px solid #777777';
	progressBarBg.style.borderTop = '1px solid #000000';

	// progress bar foreground
	var progressBarFg = window.document.createElement('div');
	this._progressBarFg = progressBarFg;
	progressBarBg.appendChild(progressBarFg);
	progressBarFg.style.position = 'relative';
	progressBarFg.style.display = 'block';
	progressBarFg.style.top = '0px';
	progressBarFg.style.left = '0px';
	progressBarFg.style.width = this._minForegroundSize + 'px';
	progressBarFg.style.height = barHeight + 'px';
	progressBarFg.style.backgroundColor = 'yellow';
	progressBarFg.style.borderRadius = barBorderRadius + 'px';
	progressBarFg.style.boxSizing = 'border-box';
	progressBarFg.style.background = '#cef83f';
	progressBarFg.style.background = '-webkit-linear-gradient(top, #cef83f 0%,#709418 100%)';
	progressBarFg.style.background = 'linear-gradient(to bottom, #cef83f 0%,#709418 100%)';
	progressBarFg.style.background = '-moz-linear-gradient(top, #cef83f 0%, #709418 100%)';
	progressBarFg.style.transition = 'width 1s';
	progressBarFg.style.webkitTransition = 'width 1s';

	// message
	var infoMessage = window.document.createElement('div');
	infoMessage.style.display = 'block';
	infoMessage.style.width = '100%';
	infoMessage.style.textAlign = 'center';
	infoMessage.style.color = '#EEE';
	infoMessage.style.fontFamily = 'arial, helvetica';
	infoMessage.style.position = 'absolute';
	infoMessage.style.height = msgSize + 'px';
	infoMessage.style.lineHeight = msgSize + 'px';
	infoMessage.style.fontSize = Math.round(msgSize * 0.8) + 'px';
	infoMessage.style.color = '#EEEEEE';
	infoMessage.innerText = msg;

	function placeElement(element, top, left) {
		background.appendChild(element);
		element.style.left = Math.round(left) + 'px';
		element.style.top = Math.round(top) + 'px';
	}

	// logo
	var logo = window.document.createElement('img');
	var imgSrc = 'img/dofus_touch_logo.png';
	logo.src = imgSrc;
	logo.style.position = 'absolute';
	logo.onload = function () {
		var width = Math.round(barWidth * 0.8);
		var height = Math.round(width / (logo.width / logo.height));

		logo.style.width = width + 'px';
		logo.style.height = height + 'px';

		var SPACING = barHeight;

		var contentHeight = height + SPACING + msgSize + SPACING + barHeight;

		var logoTop = (screenHeight - contentHeight) * 0.5;
		var infoMessageTop = logoTop + height + SPACING;
		var progressBarBgTop = infoMessageTop + msgSize + SPACING;

		placeElement(logo, logoTop, (screenWidth - width) * 0.5);
		placeElement(infoMessage, infoMessageTop, 0);
		placeElement(progressBarBg, progressBarBgTop, (screenWidth - barWidth) * 0.5);
	};

	this._domCreated = true;
};

AppLoadingProgress.prototype.destroy = function () {
	if (!this._domCreated) {
		return;
	}
	this.hide();
	// Removing the background color style after the loading
	window.document.body.style.backgroundColor = '';
	window.document.body.removeChild(this._background);
	this._background = null;
	this._progressBarBg = null;
	this._progressBarFg = null;
};

window.AppLoadingProgress = AppLoadingProgress;
