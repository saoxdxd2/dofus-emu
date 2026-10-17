'use strict';

window.CordovaFileCache = (function () {
	/**
	 * JS Implementation of MurmurHash3 (r136) (as of May 20, 2011)
	 *
	 * @author <a href="mailto:gary.court@gmail.com">Gary Court</a>
	 * @see http://github.com/garycourt/murmurhash-js
	 * @author <a href="mailto:aappleby@gmail.com">Austin Appleby</a>
	 * @see http://sites.google.com/site/murmurhash/
	 *
	 * @param {string} key ASCII only
	 * @param {number} seed Positive integer only
	 * @return {number} 32-bit positive integer hash
	 */
	function murmurhash3(key, seed) {
		var remainder, bytes, h1, h1b, c1, c2, k1, i;

		remainder = key.length & 3; // key.length % 4
		bytes = key.length - remainder;
		h1 = seed;
		c1 = 0xcc9e2d51;
		c2 = 0x1b873593;
		i = 0;

		while (i < bytes) {
			k1 =
				(key.charCodeAt(i) & 0xff) |
				((key.charCodeAt(++i) & 0xff) << 8) |
				((key.charCodeAt(++i) & 0xff) << 16) |
				((key.charCodeAt(++i) & 0xff) << 24);
			++i;

			k1 = ((k1 & 0xffff) * c1 + ((((k1 >>> 16) * c1) & 0xffff) << 16)) & 0xffffffff;
			k1 = (k1 << 15) | (k1 >>> 17);
			k1 = ((k1 & 0xffff) * c2 + ((((k1 >>> 16) * c2) & 0xffff) << 16)) & 0xffffffff;

			h1 ^= k1;
			h1 = (h1 << 13) | (h1 >>> 19);
			h1b = ((h1 & 0xffff) * 5 + ((((h1 >>> 16) * 5) & 0xffff) << 16)) & 0xffffffff;
			h1 = (h1b & 0xffff) + 0x6b64 + ((((h1b >>> 16) + 0xe654) & 0xffff) << 16);
		}

		k1 = 0;
		/* jshint -W086 */
		switch (remainder) {
			case 3:
				k1 ^= (key.charCodeAt(i + 2) & 0xff) << 16;
			case 2:
				k1 ^= (key.charCodeAt(i + 1) & 0xff) << 8;
			case 1:
				k1 ^= key.charCodeAt(i) & 0xff;

				k1 = ((k1 & 0xffff) * c1 + ((((k1 >>> 16) * c1) & 0xffff) << 16)) & 0xffffffff;
				k1 = (k1 << 15) | (k1 >>> 17);
				k1 = ((k1 & 0xffff) * c2 + ((((k1 >>> 16) * c2) & 0xffff) << 16)) & 0xffffffff;
				h1 ^= k1;
		}
		/* jshint +W086 */
		h1 ^= key.length;

		h1 ^= h1 >>> 16;
		h1 = ((h1 & 0xffff) * 0x85ebca6b + ((((h1 >>> 16) * 0x85ebca6b) & 0xffff) << 16)) & 0xffffffff;
		h1 ^= h1 >>> 13;
		h1 = ((h1 & 0xffff) * 0xc2b2ae35 + ((((h1 >>> 16) * 0xc2b2ae35) & 0xffff) << 16)) & 0xffffffff;
		h1 ^= h1 >>> 16;

		return h1 >>> 0;
	}

	/**
	 * Cordova File Cache x
	 * @param {object} options
	 * @constructor
	 */
	function FileCache(options) {
		var that = this;

		/**
		 * cordova-promise-fs
		 * @type {FileSystem}
		 * @private
		 */
		this._fs = options.fs;
		if (!this._fs) {
			throw new Error('Missing required option "fs". Add an instance of cordova-promise-fs.');
		}

		function setMetadataSuccess() {
			console.log('setMetadata success');
		}
		function setMetadataFail(error) {
			console.error('setMetadata fail: ' + error);
		}
		this._fs.fs.then(function (fs) {
			fs.root.setMetadata(setMetadataSuccess, setMetadataFail, {
				'com.apple.MobileBackup': true
			});
		}, setMetadataFail);

		// 'mirror' mirrors files structure from "serverRoot" to "localRoot"
		// 'hash' creates a 1-deep filestructure, where the filenames are hashed server urls (with extension)
		this._mirrorMode = options.mode !== 'hash';
		this._retry = options.retry || [500, 1500, 8000];
		this._cacheBuster = Boolean(options.cacheBuster);

		// normalize path
		this.localRoot = this._fs.normalize(options.localRoot || 'data');
		this.serverRoot = this._fs.normalize(options.serverRoot || '');

		// set internal variables
		this._downloading = []; // download promises
		this._added = []; // added files
		this._cached = {}; // cached files

		// list existing cache contents
		this.ready = this._fs.ensure(this.localRoot).then(function (entry) {
			that.localInternalURL = entry.toInternalURL();
			that.localUrl = entry.toURL();
			return that.list();
		});
	}

	FileCache.hash = murmurhash3;

	/**
	 * Helper to cache all 'internalURL' and 'URL' for quick synchronous access
	 * to the cached files.
	 */
	FileCache.prototype.list = function list() {
		var that = this;
		return new Promise(function (resolve) {
			that._fs.list(that.localRoot, 'rfe').then(
				function (entries) {
					that._cached = {};
					entries = entries.map(function (entry) {
						var fullPath = that._fs.normalize(entry.fullPath);
						that._cached[fullPath] = {
							toInternalURL: entry.toInternalURL(),
							toURL: entry.toURL()
						};
						// fullPath data/assets/ui/mount/stamina.png
						// toInternalURL: "file:///__cdvfile_persistent__/data/assets/ui/mount/stamina.png"
						// toURL: "file:///data/user/0/com.ankama.dofustouchnext/files/files/data/assets/ui/mount/stamina.png"
						//
						// fullPath js/build/script.js
						// toInternalURL: "file:///__cdvfile_persistent__/js/build/script.js"
						// toURL: "file:///data/user/0/com.ankama.dofustouchnext/files/files/js/build/script.js"
						return fullPath;
					});
					resolve(entries);
				},
				function () {
					resolve([]);
				}
			);
		});
	};

	/**
	 * @param {string[]|string} [urls]
	 * @returns {boolean}
	 */
	FileCache.prototype.add = function (urls) {
		if (!urls) {
			urls = [];
		}
		if (typeof urls === 'string') {
			urls = [urls];
		}
		var that = this;
		urls.forEach(function (url) {
			url = that.toServerURL(url);
			if (that._added.indexOf(url) === -1) {
				that._added.push(url);
			}
		});
		return that.isDirty();
	};

	FileCache.prototype.remove = function remove(urls, returnPromises) {
		if (!urls) {
			urls = [];
		}
		var promises = [];
		if (typeof urls === 'string') {
			urls = [urls];
		}
		var that = this;
		urls.forEach(function (url) {
			var index = that._added.indexOf(that.toServerURL(url));
			if (index >= 0) {
				that._added.splice(index, 1);
			}
			var path = that.toPath(url);
			promises.push(that._fs.remove(path));
			delete that._cached[path];
		});
		return returnPromises ? Promise.all(promises) : that.isDirty();
	};

	FileCache.prototype.getDownloadQueue = function () {
		var that = this;
		return that._added.filter(function (url) {
			return !that.isCached(url);
		});
	};

	FileCache.prototype.getAdded = function () {
		return this._added;
	};

	FileCache.prototype.isDirty = function isDirty() {
		return this.getDownloadQueue().length > 0;
	};

	/**
	 * @param {Function} onprogress
	 * @returns {Promise}
	 */
	FileCache.prototype.download = function download(onprogress) {
		/** @type {FileSystem} */
		var fs = this._fs;
		var that = this;
		that.abort();

		return new Promise(function (resolve, reject) {
			// make sure cache directory exists and that
			// we have retrieved the latest cache contents
			// to avoid downloading files we already have!
			fs.ensure(that.localRoot)
				.then(function () {
					return that.list();
				})
				.then(function () {
					// no downloads needed, resolve
					if (!that.isDirty()) {
						resolve(that);
						return;
					}

					// keep track of number of downloads!
					var queue = that.getDownloadQueue();
					var started = [];
					var index = that._downloading.length;
					var done = that._downloading.length;
					var total = that._downloading.length + queue.length;

					// download every file in the queue (which is the diff from _added with _cached)
					queue.forEach(function (url) {
						var path = that.toPath(url);
						// augment progress event with index/total stats
						var onSingleDownloadProgress;
						if (typeof onprogress === 'function') {
							/** @param {ProgressEvent} ev - from ProgressEvent() */
							onSingleDownloadProgress = function (ev) {
								ev.queueIndex = index;
								ev.queueSize = total;
								ev.url = url;
								ev.path = path;
								ev.percentage = index / total;
								if (ev.loaded > 0 && ev.total > 0 && index !== total) {
									ev.percentage += ev.loaded / ev.total / total;
								}
								if (started.indexOf(url) < 0) {
									started.push(url);
									index++;
								}
								onprogress(ev);
							};
						}

						// callback
						function onDone(err) {
							if (err) {
								console.error('onDone err', err);
							}
							done++;
							// when we're done
							if (done === total) {
								// reset downloads
								that._downloading = [];
								// check if we got everything
								that.list().then(function () {
									// final progress event!
									if (onSingleDownloadProgress) {
										onSingleDownloadProgress(new ProgressEvent());
									}
									// Yes, we're not dirty anymore!
									if (that.isDirty()) {
										reject(that.getDownloadQueue());
									} else {
										resolve(that);
										// Aye, some files got left behind!
									}
								}, reject);
							}
						}
						var downloadUrl = url;
						if (that._cacheBuster) {
							downloadUrl += '?' + Date.now();
						}
						var download = fs.download(
							downloadUrl,
							path,
							{ retry: that._retry },
							onSingleDownloadProgress
						);
						download.then(onDone, onDone);
						that._downloading.push(download);
					});
				}, reject);
		});
	};

	FileCache.prototype.abort = function abort() {
		this._downloading.forEach(function (download) {
			download.abort();
		});
		this._downloading = [];
	};

	FileCache.prototype.isCached = function isCached(url) {
		url = this.toPath(url);
		return Boolean(this._cached[url]);
	};

	FileCache.prototype.clear = function clear() {
		var that = this;
		this._cached = {};
		return this._fs.removeDir(this.localRoot).then(function () {
			return that._fs.ensure(that.localRoot);
		});
	};

	/**
	 * Helpers to output to various formats
	 */
	FileCache.prototype.toInternalURL = function toInternalURL(url) {
		var path = this.toPath(url);
		if (this._cached[path]) {
			return this._cached[path].toInternalURL;
		}
		return url;
	};

	FileCache.prototype.get = function get(url) {
		var path = this.toPath(url);
		if (this._cached[path]) {
			return this._cached[path].toInternalURL;
		}
		return this.toServerURL(url);
	};

	FileCache.prototype.toDataURL = function toDataURL(url) {
		return this._fs.toDataURL(this.toPath(url));
	};

	FileCache.prototype.toURL = function toURL(url) {
		var path = this.toPath(url);
		return this._cached[path] ? this._cached[path].toURL : url;
	};

	/**
	 * @param {string} path
	 * @returns {string}
	 */
	FileCache.prototype.toServerURL = function toServerURL(path) {
		path = this._fs.normalize(path);
		return path.indexOf('://') < 0 ? this.serverRoot + path : path;
	};

	/**
	 * Helper to transform remote URL to a local path (for cordova-promise-fs)
	 */
	FileCache.prototype.toPath = function toPath(url) {
		if (this._mirrorMode) {
			var query = url.indexOf('?');
			if (query > -1) {
				url = url.substr(0, query);
			}
			url = this._fs.normalize(url || '');
			var len = this.serverRoot.length;
			if (url.substr(0, len) === this.serverRoot) {
				return this.localRoot + url.substr(len);
			}
			return this.localRoot + url;
		}
		return this.localRoot + this.hash(url) + url.substr(url.lastIndexOf('.'));
	};

	return FileCache;
}());
