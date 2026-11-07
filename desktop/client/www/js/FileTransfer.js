'use strict';

/**
 * @typedef {Object} TransferOptions
 * @property {number[]} [retry]
 * @property {Function} [onprogress]
 */

window.FileTransferClass = (function () {
	function checkHttpStatus(response) {
		if (response.status >= 200 && response.status < 300) {
			return response;
		}

		var error = new Error(response.statusText);
		error.response = response;
		throw error;
	}

	/**
	 * @constructor
	 */
	function FileTransfer() {
		/**
		 * @type {Promise}
		 * @private
		 */
		this._fetching = null;
		this._aborted = false;
	}

	FileTransfer.prototype.onprogress = function () {
		// Placeholder.
	};

	FileTransfer.prototype.abort = function () {
		if (this._fetching) {
			this._fetching.abort();
		}
		this._aborted = true;
	};

	/** @returns {boolean} */
	FileTransfer.prototype.isAborted = function () {
		return this._aborted;
	};

	/**
	 * @param {string} serverUrl - e.g. http://xxx.ankama.xxx:7777/native.css?xxxxxxxx
	 * @param {Function} win
	 * @param {Function} fail
	 */
	FileTransfer.prototype.download = function (serverUrl, win, fail) {
		var that = this;
		this._fetching = window
			.fetch(serverUrl)
			.then(checkHttpStatus)
			.then(function (response) {
				var mimeType = null;
				var headers = response.headers;
				if (headers.get) {
					mimeType = headers.get('content-type') || null;
				}
				that._fetching = null;
				return win(response.blob(), mimeType);
			})
			.catch(fail);
	};

	return FileTransfer;
}());
