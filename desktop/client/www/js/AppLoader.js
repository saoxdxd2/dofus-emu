'use strict';

window.AppLoader = (function (Promise) {
	function checkHttpStatus(response) {
		if (response.status >= 200 && response.status < 300) {
			return response;
		}

		var error = new Error(response.statusText);
		error.response = response;
		throw error;
	}

	function hash(files) {
		var keys = Object.keys(files);
		keys.sort();
		var str = '';
		keys.forEach(function (key) {
			if (files[key] && files[key].version) {
				str += '@' + files[key].version;
			}
		});
		return String(window.CordovaFileCache.hash(str));
	}

	/**
	 * On Android 9.0 "file:///android_asset/www/"
	 * @type {string}
	 */
	var BUNDLE_ROOT = location.href.replace(location.hash, '');
	BUNDLE_ROOT = BUNDLE_ROOT.substr(0, BUNDLE_ROOT.lastIndexOf('/') + 1);

	if (/ip(?:hone|ad|od)/iu.test(navigator.userAgent)) {
		BUNDLE_ROOT = location.pathname.substr(location.pathname.indexOf('/www/'));
		BUNDLE_ROOT = BUNDLE_ROOT.substr(0, BUNDLE_ROOT.lastIndexOf('/') + 1);
		BUNDLE_ROOT = 'cdvfile://localhost/bundle' + BUNDLE_ROOT;
	}

	var loaderIds = {};

	/**
	 * @param id
	 * @param options
	 * @constructor
	 */
	function AppLoader(id, options) {
		if (!options) {
			throw new Error('CordovaAppLoader has no options!');
		}
		if (!options.fs) {
			options.fs = new window.CordovaFileSystem(/* { concurrency: 10 } */);
		}
		if (!options.serverRoot) {
			throw new Error('CordovaAppLoader has no "serverRoot" option.');
		}
		if (!id || loaderIds[id]) {
			throw new Error('Loader id already exist or is undefined');
		}
		this._id = id;
		loaderIds[id] = true;

		this.allowServerRootFromManifest = options.allowServerRootFromManifest === true;
		this.cacheBuster = Boolean(options.cacheBuster);

		// initialize variables
		this.manifest = JSON.parse(localStorage.getItem(this._id + '_manifest')) || { files: {}, load: [] };
		this.newManifest = null;
		this._lastUpdateFiles = localStorage.getItem(this._id + '_last_update_files');

		// normalize serverRoot and set remote manifest url
		options.serverRoot = options.serverRoot || '';
		if (Boolean(options.serverRoot) && options.serverRoot[options.serverRoot.length - 1] !== '/') {
			options.serverRoot += '/';
		}
		this.newManifestUrl = options.manifestUrl || options.serverRoot + (options.manifest || 'manifest.json');
		// initialize a file cache
		if (options.mode) {
			options.mode = 'mirror';
		}

		/** @type {FileCache} */
		this.cache = new window.CordovaFileCache(options);

		// private stuff
		this.corruptNewManifest = false;
		this._toBeCopied = [];
		this._toBeDeleted = [];
		this._toBeDownloaded = [];
		this._updateReady = false;
		this._checkTimeout = options.checkTimeout || 60 * 1000;
	}

	AppLoader.prototype._createFilemap = function (files) {
		var result = {};
		var normalize = this.cache._fs.normalize;
		Object.keys(files).forEach(function (key) {
			files[key].filename = normalize(files[key].filename);
			result[files[key].filename] = files[key];
		});
		return result;
	};

	AppLoader.prototype.check = function (newManifest) {
		var that = this,
			manifest = this.manifest;
		if (typeof newManifest === 'string') {
			that.newManifestUrl = newManifest;
			newManifest = undefined;
		}

		var gotNewManifest = new Promise(function (resolve, reject) {
			if (typeof newManifest === 'object') {
				resolve(newManifest);
			} else {
				var url = that.newManifestUrl + (that.cacheBuster ? '?' + Date.now() : '');
				window
					.fetch(url)
					.then(checkHttpStatus)
					.then(function (res) {
						return res.json();
					})
					.then(resolve)
					.catch(reject);
			}
		});

		return new Promise(function (resolve, reject) {
			Promise.all([gotNewManifest, that.cache.list()]).then(
				function (values) {
					var newManifest = values[0];
					var newFilesHash = hash(newManifest.files);

					// Prevent end-less update loop, check if new manifest
					// has been downloaded before (but fails)

					// Check if the newFiles match the previous files (last_update_files)
					if (newFilesHash === that._lastUpdateFiles) {
						// YES! So we're doing the same update again!

						// Check if our current Manifest has indeed the "last_update_files"
						var currentFiles = hash(that.manifest.files);
						if (that._lastUpdateFiles !== currentFiles) {
							// No! So we've updated, yet they don't appear in our manifest. This means:
							console.warn(
								'New manifest available, but an earlier update attempt failed. Will not download.'
							);
							that.corruptNewManifest = true;
							return resolve(null);
						}
						// Yes, we've updated and we've succeeded.
						return resolve(false);
					}

					// Check if new manifest is valid
					if (!newManifest.files) {
						reject(new Error('Downloaded Manifest has no "files" attribute.'));
						return;
					}

					// We're good to go check! Get all the files we need
					var cachedFiles = values[1]; // files in cache
					var oldFiles = that._createFilemap(manifest.files); // files in current manifest
					var newFiles = that._createFilemap(newManifest.files); // files in new manifest
					// Create COPY and DOWNLOAD lists
					that._toBeDownloaded = [];
					that._toBeCopied = [];
					that._toBeDeleted = [];

					Object.keys(newFiles)
						// Find files that have changed version or are missing
						.filter(function (file) {
							// if new file, or...
							return (
								!oldFiles[file] ||
								// version has changed, or...
								oldFiles[file].version !== newFiles[file].version ||
								// not in cache for some reason
								!that.cache.isCached(file)
							);
						})
						// Add them to the correct list
						.forEach(function (file) {
							that._toBeDownloaded.push(file);
						});

					// Delete files
					that._toBeDeleted = cachedFiles
						.map(function (file) {
							return file.substr(that.cache.localRoot.length);
						})
						.filter(function (file) {
							// Everything that is not in new manifest, or....
							return (
								!newFiles[file] ||
								// Files that will be downloaded, or...
								that._toBeDownloaded.indexOf(file) >= 0 ||
								// Files that will be copied
								that._toBeCopied.indexOf(file) >= 0
							);
						});

					var changes = that._toBeDeleted.length + that._toBeDownloaded.length;
					// Note: if we only need to copy files, we can keep serving from bundle!
					// So no update is needed!
					if (changes > 0) {
						// Save the new Manifest
						that.newManifest = newManifest;
						that.newManifest.root = that.cache.localInternalURL;
						resolve(true);
					} else {
						resolve(false);
					}
				},
				function (err) {
					reject(err);
				}
			); // end of .then
		}); // end of new Promise
	};

	AppLoader.prototype.canDownload = function () {
		return Boolean(this.newManifest) && !this._updateReady;
	};

	AppLoader.prototype.canUpdate = function () {
		return this._updateReady;
	};

	AppLoader.prototype.download = function (onProgress) {
		var that = this;
		if (!that.canDownload()) {
			return new Promise(function (resolve) {
				resolve(null);
			});
		}
		// we will delete files, which will invalidate the current manifest...
		localStorage.removeItem(this._id + '_manifest');
		// only attempt this once - set 'last_update_files'
		localStorage.setItem(this._id + '_last_update_files', hash(this.newManifest.files));
		this.manifest.files = {};
		return that.cache
			.remove(that._toBeDeleted, true)
			.then(function () {
				// TODO: when we copy?
				return Promise.all(
					that._toBeCopied.map(function (file) {
						// return that.cache._fs.download(BUNDLE_ROOT + file, that.cache.localRoot + file);
						return window
							.fetch(BUNDLE_ROOT + file)
							.then(checkHttpStatus)
							.then(function (response) {
								return response.text();
							})
							.then(function (body) {
								return that.cache._fs.write(that.cache.localRoot + file, body, '', function (error) {
									if (error) {
										console.error(error);
									}
								});
							});
					})
				);
			})
			.then(function () {
				if (that.allowServerRootFromManifest && that.newManifest.serverRoot) {
					that.cache.serverRoot = that.newManifest.serverRoot;
				}
				that.cache.add(that._toBeDownloaded);
				return that.cache.download(onProgress);
			})
			.then(
				function () {
					that._toBeDeleted = [];
					that._toBeDownloaded = [];
					that._updateReady = true;
					return that.newManifest;
				},
				function onRejected(files, err) {
					// on download error, remove files...
					console.error('download Error', files, err);
					if (Boolean(files) && files.length) {
						that.cache.remove(files);
					}
					return files;
				}
			);
	};

	AppLoader.prototype.update = function (reload) {
		if (this._updateReady) {
			// update manifest
			localStorage.setItem(this._id + '_manifest', JSON.stringify(this.newManifest));
			this.manifest = JSON.parse(JSON.stringify(this.newManifest));
			this.newManifest = null;
			this._updateReady = false;
			if (reload !== false) {
				location.reload();
			}
			return true;
		}
		return false;
	};

	AppLoader.prototype.clear = function () {
		localStorage.removeItem(this._id + '_last_update_files');
		localStorage.removeItem(this._id + '_manifest');
		return this.cache.clear();
	};

	AppLoader.prototype.reset = function () {
		return this.clear().then(
			function () {
				location.reload();
			},
			function () {
				location.reload();
			}
		);
	};

	return AppLoader;
}(window.Promise));
