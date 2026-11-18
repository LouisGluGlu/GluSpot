/* GluSpot Apple Music page bridge.

   Talks to MusicKit on https://music.apple.com/ through the documented JS API
   and the documented Apple Music REST paths. The music user token is hashed
   inside this page and is never posted to the host. Playback mode is not a
   field this file reads or writes.

   The signed-in library list on music.apple.com is the root playlist folder's
   children, not a bare /v1/me/library/playlists page. Names often live on the
   resources map while data rows are only id and type. MusicKit may return that
   document directly, or wrap it in {data: document}.
*/
var gluspotAppleLibrary = (function () {
  var ROOT = "/v1/me/library/playlist-folders/p.playlistsroot/children";
  var PLAYLISTS = "/v1/me/library/playlists";

  function pageQuery(args) {
    var limit = Number(args && args.limit);
    var offset = Number(args && args.offset);
    if (!isFinite(limit)) limit = 100;
    if (!isFinite(offset)) offset = 0;
    return {
      limit: Math.max(1, Math.min(100, Math.floor(limit))),
      offset: Math.max(0, Math.min(100000, Math.floor(offset)))
    };
  }

  function documentOf(payload) {
    if (Array.isArray(payload)) return {data: payload};
    if (!payload || typeof payload !== "object") return {};
    var inner = payload.data;
    if (inner && typeof inner === "object" && !Array.isArray(inner) &&
        (Array.isArray(inner.data) || inner.resources || inner.results || inner.next || inner.meta)) {
      return inner;
    }
    if (payload.body && typeof payload.body === "object" && !Array.isArray(payload.body)) return payload.body;
    return payload;
  }

  function libraryRows(payload) {
    var doc = documentOf(payload);
    var resources = doc.resources && typeof doc.resources === "object" && !Array.isArray(doc.resources) ? doc.resources : {};
    var data = Array.isArray(doc.data) ? doc.data : [];
    var rows = [];
    for (var index = 0; index < data.length; index += 1) {
      var row = data[index];
      if (!row || typeof row !== "object") continue;
      var named = row.attributes && typeof row.attributes.name === "string" && row.attributes.name;
      if (!named && row.type && resources[row.type] && resources[row.type][row.id]) row = resources[row.type][row.id];
      if (row && typeof row === "object") rows.push(row);
    }
    return rows;
  }

  function songRows(payload) {
    var rows = libraryRows(payload);
    if (rows.length) return rows;
    var doc = documentOf(payload);
    var results = doc.results && typeof doc.results === "object" ? doc.results : doc;
    var songs = results && results.songs && results.songs.data;
    return Array.isArray(songs) ? songs : [];
  }

  function nextOffset(doc, pageOffset) {
    var next = doc && typeof doc.next === "string" ? doc.next : "";
    var match = /(?:^|[?&])offset=(\d+)/.exec(next);
    if (!match) return null;
    var value = Number(match[1]);
    return isFinite(value) && value > pageOffset ? value : null;
  }

  async function readPage(request, path, limit, offset) {
    try {
      return await request(path, {platform: "web", limit: limit, offset: offset});
    } catch (error) {
      return await request(path, {platform: "web", offset: offset});
    }
  }

  async function readTrackPage(request, path, limit, offset) {
    // Library songs often omit artwork until the catalog relationship is included.
    var included = {platform: "web", limit: limit, offset: offset, include: "catalog"};
    try {
      return await request(path, included);
    } catch (error) {
      try {
        return await request(path, {platform: "web", offset: offset, include: "catalog"});
      } catch (again) {
        return await readPage(request, path, limit, offset);
      }
    }
  }

  async function collectAt(request, path, query, into, seen, depth) {
    if (depth > 4) return false;
    var pageOffset = depth === 0 ? query.offset : 0;
    var more = false;
    for (var hop = 0; hop < 8 && into.items.length < query.limit; hop += 1) {
      var ask = Math.min(100, query.limit - into.items.length);
      var payload = await readPage(request, path, ask, pageOffset);
      var doc = documentOf(payload);
      var batch = libraryRows(payload);
      var folders = [];
      var fresh = 0;
      for (var index = 0; index < batch.length; index += 1) {
        var row = batch[index];
        var id = row && row.id ? String(row.id) : "";
        if (!id || seen[id]) continue;
        seen[id] = 1;
        fresh += 1;
        if (row.type === "library-playlist-folders") {
          folders.push(id);
          continue;
        }
        if (row.attributes && typeof row.attributes.name === "string" && row.attributes.name) into.items.push(row);
        if (into.items.length >= query.limit) break;
      }
      if (!fresh) break;
      for (var folder = 0; folder < folders.length && into.items.length < query.limit; folder += 1) {
        var child = "/v1/me/library/playlist-folders/" + encodeURIComponent(folders[folder]) + "/children";
        try {
          if (await collectAt(request, child, query, into, seen, depth + 1)) more = true;
        } catch (error) {
          // One folder failing must not drop the playlists already collected.
        }
      }
      var following = nextOffset(doc, pageOffset);
      if (into.items.length >= query.limit) return following != null || more;
      if (following == null) return more;
      pageOffset = following;
    }
    return more;
  }

  async function collectPlaylists(request, args) {
    // Walk from the start, then slice. Root offsets are not positions in the
    // flattened list once folders are included. The walk itself stops at 100.
    var query = pageQuery(args);
    var into = {items: []};
    var wide = {limit: Math.min(100, query.offset + query.limit), offset: 0};
    var more = false;
    try {
      more = await collectAt(request, ROOT, wide, into, {}, 0) === true;
    } catch (error) {
      into.items = [];
      more = await collectAt(request, PLAYLISTS, wide, into, {}, 0) === true;
    }
    var page = into.items.slice(query.offset, query.offset + query.limit);
    return {items: page, more: page.length > 0 && (more || into.items.length > query.offset + page.length)};
  }

  function isCatalogPlaylistId(value) {
    return /^pl\.[A-Za-z0-9._-]{1,120}$/.test(String(value || ""));
  }

  function tracksPath(playlistId, storefrontId) {
    var id = encodeURIComponent(String(playlistId || ""));
    // Featured and recommended shelves hold catalog playlists (pl.*), which
    // live under the storefront, not the library.
    if (isCatalogPlaylistId(playlistId) && storefrontId) {
      return "/v1/catalog/" + encodeURIComponent(String(storefrontId)) + "/playlists/" + id + "/tracks";
    }
    return PLAYLISTS + "/" + id + "/tracks";
  }

  function playlistRow(row) {
    return row && typeof row === "object" && (row.type === "playlists" || row.type === "library-playlists") &&
      row.id && row.attributes && typeof row.attributes.name === "string" && row.attributes.name;
  }

  function uniquePlaylists(rows, limit) {
    var seen = {};
    var out = [];
    for (var index = 0; index < rows.length && out.length < limit; index += 1) {
      var row = rows[index];
      if (!playlistRow(row) || seen[row.id]) continue;
      seen[row.id] = 1;
      out.push(row);
    }
    return out;
  }

  // /v1/me/recommendations: each shelf lists its playlists under
  // relationships.contents. Albums and stations are skipped.
  function recommendationPlaylists(payload, limit) {
    var doc = documentOf(payload);
    var shelves = Array.isArray(doc.data) ? doc.data : [];
    var rows = [];
    for (var index = 0; index < shelves.length; index += 1) {
      var shelf = shelves[index];
      var contents = shelf && shelf.relationships && shelf.relationships.contents;
      var data = contents && Array.isArray(contents.data) ? contents.data : [];
      for (var item = 0; item < data.length; item += 1) rows.push(data[item]);
    }
    return uniquePlaylists(rows, limit);
  }

  // /v1/catalog/{sf}/charts?types=playlists: results.playlists is a list of charts.
  function chartPlaylists(payload, limit) {
    var doc = documentOf(payload);
    var results = doc.results && typeof doc.results === "object" ? doc.results : {};
    var charts = Array.isArray(results.playlists) ? results.playlists : [];
    var rows = [];
    for (var index = 0; index < charts.length; index += 1) {
      var data = charts[index] && Array.isArray(charts[index].data) ? charts[index].data : [];
      for (var item = 0; item < data.length; item += 1) rows.push(data[item]);
    }
    return uniquePlaylists(rows, limit);
  }

  function searchPlaylists(payload, limit) {
    var doc = documentOf(payload);
    var results = doc.results && typeof doc.results === "object" ? doc.results : {};
    var data = results.playlists && Array.isArray(results.playlists.data) ? results.playlists.data : [];
    return uniquePlaylists(data, limit);
  }

  async function collectTracks(request, playlistId, args, storefrontId) {
    var query = pageQuery(args);
    var path = tracksPath(playlistId, storefrontId);
    var items = [];
    var seen = {};
    var pageOffset = query.offset;
    for (var hop = 0; hop < 8 && items.length < query.limit; hop += 1) {
      var ask = Math.min(100, query.limit - items.length);
      var payload = await readTrackPage(request, path, ask, pageOffset);
      var doc = documentOf(payload);
      var resources = doc.resources && typeof doc.resources === "object" && !Array.isArray(doc.resources) ? doc.resources : {};
      var batch = songRows(payload).map(function (row) { return applyIncludedCatalog(row, resources); });
      var fresh = 0;
      for (var index = 0; index < batch.length; index += 1) {
        var row = batch[index];
        var id = row && row.id ? String(row.id) : "";
        if (id && seen[id]) continue;
        if (id) seen[id] = 1;
        fresh += 1;
        items.push(row);
        if (items.length >= query.limit) break;
      }
      if (!fresh) break;
      var following = nextOffset(doc, pageOffset);
      if (items.length >= query.limit) return {items: items, more: following != null};
      if (following == null) break;
      pageOffset = following;
    }
    return {items: items, more: false};
  }

  function isCatalogSongId(value) {
    return /^[0-9]{2,20}$/.test(String(value || ""));
  }

  function artworkUrlOf(attributes) {
    var artwork = attributes && attributes.artwork;
    return artwork && typeof artwork.url === "string" ? artwork.url : "";
  }

  function catalogId(row) {
    var attributes = row && row.attributes || {};
    var play = attributes.playParams || {};
    if (play.catalogId) return String(play.catalogId);
    var linked = row && row.relationships && row.relationships.catalog && row.relationships.catalog.data;
    if (linked && linked[0] && linked[0].id) return String(linked[0].id);
    var playId = play.id ? String(play.id) : "";
    if (isCatalogSongId(playId) && play.isLibrary !== true) return playId;
    if (row && row.type === "songs" && isCatalogSongId(row.id)) return String(row.id);
    return "";
  }

  function needsCatalog(row) {
    var attributes = row && row.attributes || {};
    return !artworkUrlOf(attributes) || !attributes.artistName || !attributes.albumName || !attributes.durationInMillis;
  }

  function includedCatalog(row, resources) {
    var songs = resources && resources.songs && typeof resources.songs === "object" ? resources.songs : {};
    var linked = row && row.relationships && row.relationships.catalog && row.relationships.catalog.data;
    if (linked && linked[0]) {
      if (linked[0].attributes) return linked[0];
      var linkedId = linked[0].id ? String(linked[0].id) : "";
      if (linkedId && songs[linkedId]) return songs[linkedId];
    }
    var id = catalogId(row);
    return id && songs[id] ? songs[id] : null;
  }

  function applyIncludedCatalog(row, resources) {
    var catalog = includedCatalog(row, resources);
    return catalog ? mergeCatalogSong(row, catalog) : row;
  }

  function mergeCatalogSong(row, catalog) {
    if (!row || !catalog || !catalog.attributes) return row;
    var attributes = Object.assign({}, row.attributes || {});
    var extra = catalog.attributes;
    // An empty artwork object is still present. It must not hide the catalog picture.
    if (!artworkUrlOf(attributes) && artworkUrlOf(extra)) attributes.artwork = extra.artwork;
    if (!attributes.artistName && extra.artistName) attributes.artistName = extra.artistName;
    if (!attributes.albumName && extra.albumName) attributes.albumName = extra.albumName;
    if (!attributes.durationInMillis && extra.durationInMillis) attributes.durationInMillis = extra.durationInMillis;
    var play = Object.assign({}, attributes.playParams || {});
    if (!play.catalogId && catalog.id) play.catalogId = String(catalog.id);
    attributes.playParams = play;
    var merged = Object.assign({}, row, {attributes: attributes});
    return merged;
  }

  async function resolveLyricCatalog(request, songId, knownCatalogId) {
    if (isCatalogSongId(knownCatalogId)) return {id: String(knownCatalogId), reason: "catalog-id"};
    if (isCatalogSongId(songId)) return {id: String(songId), reason: "catalog-id"};
    if (!songId) return {id: "", reason: "no-catalog"};
    var payload;
    try {
      payload = await request("/v1/me/library/songs/" + encodeURIComponent(String(songId)), {include: "catalog"});
    } catch (error) {
      return {id: "", reason: "request-failed"};
    }
    var doc = documentOf(payload);
    var resources = doc.resources && typeof doc.resources === "object" && !Array.isArray(doc.resources) ? doc.resources : {};
    var rows = songRows(payload).map(function (row) { return applyIncludedCatalog(row, resources); });
    var id = rows.length ? catalogId(rows[0]) : "";
    if (!id && resources.songs && typeof resources.songs === "object") {
      var keys = Object.keys(resources.songs);
      if (keys.length === 1 && isCatalogSongId(keys[0])) id = keys[0];
    }
    return {id: id, reason: id ? "include" : "no-catalog"};
  }

  function ttmlFits(value) {
    // The companion speaks one JSON line, and Python drops anything over 1 MiB.
    var bytes = 0;
    for (var index = 0; index < value.length; index += 1) {
      var code = value.charCodeAt(index);
      bytes += code < 0x80 ? 1 : code < 0x800 ? 2 : 3;
      if (code === 34 || code === 92) bytes += 1;
      if (bytes > 500000) return false;
    }
    return true;
  }

  // With extend=ttmlLocalizations Apple adds a second TTML whose head
  // carries the translations (iTunesMetadata/translations). Prefer it.
  function extractLocalizedTtml(payload) {
    var found = "";
    function walk(value, depth) {
      if (found || depth > 8 || value == null || typeof value !== "object") return;
      if (Array.isArray(value)) {
        for (var index = 0; index < value.length; index += 1) walk(value[index], depth + 1);
        return;
      }
      var text = value.ttmlLocalizations;
      if (typeof text === "string" && text.indexOf("<tt") >= 0 && ttmlFits(text)) {
        found = text;
        return;
      }
      var keys = Object.keys(value);
      for (var index = 0; index < keys.length; index += 1) walk(value[keys[index]], depth + 1);
    }
    walk(payload, 0);
    return found;
  }

  function extractTtml(payload) {
    var found = "";
    function walk(value, depth) {
      if (found || depth > 8 || value == null) return;
      if (typeof value === "string") {
        var head = value.replace(/^\s+/, "").charAt(0);
        if (head === "{" || head === "[") {
          // Some MusicKit builds hand back the body as text. Open it so the
          // TTML attribute is returned, not the JSON around it.
          try { walk(JSON.parse(value), depth + 1); } catch (error) {}
          return;
        }
        if (head === "<" && value.indexOf("<tt") >= 0 && ttmlFits(value)) found = value;
        return;
      }
      if (typeof value !== "object") return;
      if (Array.isArray(value)) {
        for (var index = 0; index < value.length; index += 1) walk(value[index], depth + 1);
        return;
      }
      var keys = Object.keys(value);
      for (var index = 0; index < keys.length; index += 1) walk(value[keys[index]], depth + 1);
    }
    walk(payload, 0);
    return found;
  }

  async function attachCatalog(request, storefrontId, items) {
    var ids = [];
    var seen = {};
    for (var index = 0; index < items.length && ids.length < 100; index += 1) {
      var id = catalogId(items[index]);
      if (!id || seen[id] || !needsCatalog(items[index])) continue;
      seen[id] = 1;
      ids.push(id);
    }
    var needingArt = 0;
    for (var scan = 0; scan < items.length; scan += 1) {
      if (!artworkUrlOf(items[scan] && items[scan].attributes)) needingArt += 1;
    }
    if (!ids.length || !storefrontId) {
      if (!storefrontId && ids.length) return {items: items, lookup: "error", reason: "no-storefront"};
      if (needingArt) return {items: items, lookup: "miss", reason: "no-catalog-id"};
      return {items: items, lookup: "ok", reason: "filled"};
    }
    var payload;
    try {
      payload = await request("/v1/catalog/" + encodeURIComponent(storefrontId) + "/songs", {ids: ids.join(",")});
    } catch (error) {
      return {items: items, lookup: "error", reason: "request-failed"};
    }
    var doc = documentOf(payload);
    var resources = doc.resources && typeof doc.resources === "object" && !Array.isArray(doc.resources) ? doc.resources : {};
    var rows = songRows(payload).map(function (row) {
      var full = row && row.type && resources[row.type] && resources[row.type][row.id];
      if (full && !artworkUrlOf(row.attributes) && artworkUrlOf(full.attributes)) return full;
      return row;
    });
    var byId = {};
    for (var row = 0; row < rows.length; row += 1) {
      if (rows[row] && rows[row].id) byId[String(rows[row].id)] = rows[row];
    }
    var merged = items.map(function (item) {
      var id = catalogId(item);
      return id && byId[id] ? mergeCatalogSong(item, byId[id]) : item;
    });
    var unmatched = 0;
    var noArt = 0;
    for (var check = 0; check < merged.length; check += 1) {
      if (artworkUrlOf(merged[check] && merged[check].attributes)) continue;
      var checkId = catalogId(merged[check]);
      if (!checkId) continue;
      if (!byId[checkId]) unmatched += 1;
      else noArt += 1;
    }
    if (unmatched || noArt) {
      return {items: merged, lookup: "miss", reason: noArt && !unmatched ? "catalog-no-art" : "catalog-empty"};
    }
    return {items: merged, lookup: "ok", reason: "filled"};
  }

  function musicKitInstance(host) {
    try {
      if (!host || !host.MusicKit || typeof host.MusicKit.getInstance !== "function") return null;
      var mk = host.MusicKit.getInstance();
      if (!mk || !mk.api || typeof mk.api.music !== "function") return null;
      return mk;
    } catch (error) {
      return null;
    }
  }

  // One library write through MusicKit's own request, which signs it; the
  // page never reads a token. This MusicKit build's api.music(path, params,
  // options) passes options.method and options.body straight into its request
  // (checked on music.apple.com, MusicKit playback/AppleMusic-2646.4.0): an
  // object body is sent as JSON. A "fetchOptions" wrapper is ignored and the
  // call silently becomes a GET, which is what made earlier writes no-ops.
  function describeError(error) {
    if (!error) return "none";
    var parts = [];
    ["name", "errorCode", "status", "message", "description"].forEach(function (key) {
      var value = error[key];
      if (value !== undefined && value !== null && value !== "") parts.push(key + "=" + String(value).slice(0, 120));
    });
    return parts.join(" ") || String(error).slice(0, 160);
  }

  async function writeLibrary(mk, path, body, report, params) {
    var note = typeof report === "function" ? report : function () {};
    var options = {method: "POST"};
    if (body !== undefined && body !== null) options.body = body;
    try {
      var result = await mk.api.music(path, params || {}, options);
      var status = result && typeof result.status === "number" ? result.status : 0;
      if (status && (status < 200 || status >= 300)) throw {name: "HttpStatus", status: status};
      note("write path=" + path.split("?")[0] + " result=ok status=" + (status || "-"));
      return {via: "musickit", status: status, result: result || {}};
    } catch (error) {
      note("write path=" + path.split("?")[0] + " result=error " + describeError(error));
      throw new Error("write_failed " + describeError(error));
    }
  }

  return {
    pageQuery: pageQuery,
    isCatalogPlaylistId: isCatalogPlaylistId,
    tracksPath: tracksPath,
    recommendationPlaylists: recommendationPlaylists,
    chartPlaylists: chartPlaylists,
    searchPlaylists: searchPlaylists,
    documentOf: documentOf,
    libraryRows: libraryRows,
    writeLibrary: writeLibrary,
    describeError: describeError,
    songRows: songRows,
    collectPlaylists: collectPlaylists,
    collectTracks: collectTracks,
    musicKitInstance: musicKitInstance,
    catalogId: catalogId,
    mergeCatalogSong: mergeCatalogSong,
    applyIncludedCatalog: applyIncludedCatalog,
    resolveLyricCatalog: resolveLyricCatalog,
    extractTtml: extractTtml,
    extractLocalizedTtml: extractLocalizedTtml,
    attachCatalog: attachCatalog,
    rootPath: ROOT,
    playlistPath: PLAYLISTS
  };
})();

if (typeof window === "undefined" && typeof module !== "undefined" && module.exports) module.exports = gluspotAppleLibrary;

(function () {
  if (typeof window === "undefined" || !window.chrome || !window.chrome.webview) return;
  if (window.__gluspotAppleBridge) return;
  window.__gluspotAppleBridge = true;

  function post(message) {
    window.chrome.webview.postMessage(message);
  }

  function fail(request, error) {
    post({v: 1, id: request.id, ok: false, error: error || "runtime"});
  }

  function publicText(value) {
    var text = String(value == null ? "" : value);
    text = text.replace(/\b(token|cookie|password|authorization|secret)s?\s*[=:]\s*\S+/ig, "(redacted)");
    text = text.replace(/\b(token|cookie|password|authorization|secret)s?\b/ig, "(redacted)");
    return text.slice(0, 300);
  }

  var reportingPageError = false;
  function reportPageError(value) {
    if (reportingPageError) return;
    reportingPageError = true;
    try { post({v: 1, op: "page_error", message: publicText(value)}); } catch (error) {}
    reportingPageError = false;
  }

  function liveInstance() {
    var mk = gluspotAppleLibrary.musicKitInstance(window);
    if (!mk) throw new Error("runtime");
    return mk;
  }

  async function userMarker(token) {
    if (!token) return "";
    var bytes = new TextEncoder().encode(String(token));
    var digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).slice(0, 8).map(function (value) {
      return value.toString(16).padStart(2, "0");
    }).join("");
  }

  function callMusic(mk) {
    return function (path, query) { return mk.api.music(path, query); };
  }

  async function storefront(mk) {
    var response = await mk.api.music("/v1/me/storefront");
    var row = gluspotAppleLibrary.libraryRows(response)[0];
    var id = row && row.id ? String(row.id) : "";
    if (id && mk.storefrontId !== id) mk.storefrontId = id;
    return id;
  }

  // play() resolves only after buffering. Waiting on it inside the 20 second
  // query clock reloaded the page mid-start. Start it, answer now, and let
  // status polls report progress. A late rejection is logged, not thrown.
  function startPlay(mk) {
    try {
      var pending = mk.play();
      if (pending && typeof pending.catch === "function") {
        pending.catch(function (error) { reportPageError("play: " + (error && error.message)); });
      }
    } catch (error) {
      reportPageError("play: " + (error && error.message));
    }
  }

  function playing(mk) {
    var item = mk.nowPlayingItem || null;
    var duration = item && typeof item.playbackDuration === "number" ? item.playbackDuration : 0;
    if (duration > 10000) duration = duration / 1000;
    var state = mk.playbackState;
    var playingNow = state === 2 || String(state) === "playing";
    // MusicKit: 5 ended, 10 completed. The host advances its own queue then.
    var endedNow = state === 5 || state === 10 || String(state) === "ended" || String(state) === "completed";
    return {
      playing: playingNow,
      ended: endedNow,
      position: typeof mk.currentPlaybackTime === "number" ? mk.currentPlaybackTime : 0,
      duration: duration,
      song_id: item && item.id ? String(item.id) : "",
      preview: duration > 0 && duration <= 35
    };
  }

  async function handle(request) {
    var args = request.args || {};
    if (request.op === "login_window") {
      if (args.show === false) return {shown: false};
      var mkLogin = liveInstance();
      var authorized = false;
      try {
        await mkLogin.authorize();
        authorized = mkLogin.isAuthorized === true;
      } catch (error) {
        reportPageError(error && error.message);
        authorized = mkLogin.isAuthorized === true;
      }
      return {shown: true, authorized: authorized, closed: false};
    }
    var mk = liveInstance();
    if (request.op === "sign_out") {
      await mk.unauthorize();
      return {ok: true};
    }
    if (request.op === "account") {
      var authorized = mk.isAuthorized === true;
      return {
        logged_in: authorized,
        user_id: authorized ? await userMarker(mk.musicUserToken) : "",
        nickname: "Apple Music",
        storefront: authorized ? await storefront(mk) : ""
      };
    }
    if (!mk.isAuthorized) throw new Error("auth_required");
    if (request.op === "library_playlists") return gluspotAppleLibrary.collectPlaylists(callMusic(mk), args);
    if (request.op === "library_tracks") {
      var front = mk.storefrontId || await storefront(mk);
      var trackPage = await gluspotAppleLibrary.collectTracks(callMusic(mk), args.playlist_id, args, front);
      var filled = await gluspotAppleLibrary.attachCatalog(callMusic(mk), front, trackPage.items || []);
      trackPage.items = filled.items;
      trackPage.artwork_lookup = filled.lookup;
      trackPage.artwork_reason = filled.reason;
      return trackPage;
    }
    if (request.op === "song_lyrics") {
      var lyricFront = mk.storefrontId || await storefront(mk);
      var resolved = await gluspotAppleLibrary.resolveLyricCatalog(function (path, query) {
        return mk.api.music(path, query);
      }, args.song_id, args.catalog_id);
      if (!resolved.id) return {ttml: "", kind: "", step: "miss", reason: resolved.reason || "no-catalog"};
      var lyricPaths = [
        "/v1/catalog/" + encodeURIComponent(lyricFront) + "/songs/" + encodeURIComponent(resolved.id) + "/syllable-lyrics",
        "/v1/catalog/" + encodeURIComponent(lyricFront) + "/songs/" + encodeURIComponent(resolved.id) + "/lyrics"
      ];
      var lyricStep = "miss";
      // Ask for the translated localisation first (Chinese, the app's
      // language); a storefront that refuses the parameters gets the plain
      // request, exactly as before.
      var localized = {extend: "ttmlLocalizations", "l[lyrics]": String(args.lang || "zh-Hans")};
      for (var lyricIndex = 0; lyricIndex < lyricPaths.length; lyricIndex += 1) {
        var attempts = [localized, null];
        for (var attempt = 0; attempt < attempts.length; attempt += 1) {
          try {
            var lyricPayload = attempts[attempt]
              ? await mk.api.music(lyricPaths[lyricIndex], attempts[attempt])
              : await mk.api.music(lyricPaths[lyricIndex]);
            var ttml = gluspotAppleLibrary.extractLocalizedTtml(lyricPayload) || gluspotAppleLibrary.extractTtml(lyricPayload);
            if (ttml) {
              return {ttml: ttml, kind: lyricIndex === 0 ? "syllable" : "line", step: "ok", reason: resolved.reason,
                      translated: ttml.indexOf("<translation") >= 0};
            }
          } catch (error) {
            lyricStep = "error";
          }
        }
      }
      return {ttml: "", kind: "", step: lyricStep, reason: resolved.reason};
    }
    if (request.op === "find_songs") {
      var front = mk.storefrontId || await storefront(mk);
      var query = gluspotAppleLibrary.pageQuery(args);
      var found = await mk.api.music("/v1/catalog/" + encodeURIComponent(front) + "/search", {
        types: "songs",
        limit: Math.min(25, query.limit),
        offset: query.offset,
        term: String(args.query || "").slice(0, 200)
      });
      return {items: gluspotAppleLibrary.songRows(found)};
    }
    if (request.op === "for_you_playlists") {
      var mine = await mk.api.music("/v1/me/recommendations", {limit: 10});
      return {items: gluspotAppleLibrary.recommendationPlaylists(mine, 60)};
    }
    if (request.op === "chart_playlists") {
      var chartFront = mk.storefrontId || await storefront(mk);
      var charts = await mk.api.music("/v1/catalog/" + encodeURIComponent(chartFront) + "/charts", {
        types: "playlists", limit: 40
      });
      return {items: gluspotAppleLibrary.chartPlaylists(charts, 60)};
    }
    if (request.op === "find_playlists") {
      var listFront = mk.storefrontId || await storefront(mk);
      var listQuery = gluspotAppleLibrary.pageQuery(args);
      var lists = await mk.api.music("/v1/catalog/" + encodeURIComponent(listFront) + "/search", {
        types: "playlists",
        limit: Math.min(25, listQuery.limit),
        offset: listQuery.offset,
        term: String(args.query || "").slice(0, 200)
      });
      return {items: gluspotAppleLibrary.searchPlaylists(lists, 25)};
    }
    if (request.op === "now_playing") return playing(mk);
    if (request.op === "transport") {
      var action = args.action;
      if (action === "status") return playing(mk);
      if (action === "pause") {
        await mk.pause();
        // pause() resolves before playbackState changes; answer with the
        // settled state so the play button does not flip back for a poll.
        for (var settle = 0; settle < 10 && playing(mk).playing; settle += 1) {
          await new Promise(function (resolve) { setTimeout(resolve, 50); });
        }
        return playing(mk);
      }
      if (action === "seek") { await mk.seekToTime(Number(args.seconds) || 0); return playing(mk); }
      if (action === "resume") { startPlay(mk); return playing(mk); }
      if (action === "play") {
        if (!mk.storefrontId) await storefront(mk);
        await mk.setQueue({song: String(args.song_id || "")});
        startPlay(mk);
        var started = playing(mk);
        started.started = true;
        return started;
      }
      throw new Error("argument");
    }
    if (request.op === "make_playlist") {
      // Writes are real POSTs with a JSON body. (Passing {method, data} as the
      // second argument of mk.api.music only adds query parameters to a GET:
      // nothing was written, yet the call "succeeded".)
      var name = String(args.name || "").slice(0, 40);
      var created = await gluspotAppleLibrary.writeLibrary(mk, "/v1/me/library/playlists",
        {attributes: {name: name, description: "GluSpot"}}, reportPageError);
      var createdRow = gluspotAppleLibrary.libraryRows(created.result)[0];
      var createdId = createdRow && createdRow.id ? String(createdRow.id) : "";
      var createdName = createdRow && createdRow.attributes ? String(createdRow.attributes.name || "") : "";
      if (!/^p\./.test(createdId) || (createdName && createdName !== name)) {
        reportPageError("write create unconfirmed id=" + (createdId ? createdId.slice(0, 2) + "…" : "none")
                        + " named=" + (createdName === name ? "yes" : "no") + " rows=" + gluspotAppleLibrary.libraryRows(created.result).length);
        throw new Error("create_unconfirmed");
      }
      return {ok: true, playlist_id: createdId, name: name, via: created.via};
    }
    if (request.op === "append_tracks") {
      var ids = (Array.isArray(args.song_ids) ? args.song_ids : []).map(String);
      var tracksPath = "/v1/me/library/playlists/" + encodeURIComponent(String(args.playlist_id || "")) + "/tracks";
      try {
        await gluspotAppleLibrary.writeLibrary(mk, tracksPath,
          {data: ids.map(function (id) { return {id: id, type: "songs"}; })}, reportPageError);
        return {ok: true, added: ids.length, via: "catalog"};
      } catch (catalogError) {
        // Some accounts only take library songs here (as the playlist importers
        // found): add the songs to the library first, then add their library ids.
        await gluspotAppleLibrary.writeLibrary(mk, "/v1/me/library", null, reportPageError, {"ids[songs]": ids.join(",")});
        var front = mk.storefrontId || await storefront(mk);
        var libraryIds = [];
        for (var index = 0; index < ids.length; index += 1) {
          var linked = await mk.api.music("/v1/catalog/" + encodeURIComponent(front) + "/songs/" + encodeURIComponent(ids[index]) + "/library");
          var linkedRow = gluspotAppleLibrary.libraryRows(linked)[0];
          if (linkedRow && linkedRow.id) libraryIds.push(String(linkedRow.id));
        }
        if (!libraryIds.length) throw catalogError;
        await gluspotAppleLibrary.writeLibrary(mk, tracksPath,
          {data: libraryIds.map(function (id) { return {id: id, type: "library-songs"}; })}, reportPageError);
        return {ok: true, added: libraryIds.length, via: "library"};
      }
    }
    throw new Error("unsupported");
  }

  window.addEventListener("error", function (event) {
    reportPageError(event && event.message);
  });
  if (window.console && typeof window.console.error === "function") {
    var previousError = window.console.error;
    window.console.error = function () {
      var parts = [];
      for (var index = 0; index < arguments.length; index += 1) parts.push(publicText(arguments[index]));
      reportPageError(parts.join(" "));
      return previousError.apply(window.console, arguments);
    };
  }
  var waiting = [];
  var musickitAnnounced = false;

  function finish(request) {
    handle(request).then(function (body) {
      post({v: 1, id: request.id, ok: true, body: body || {}});
    }).catch(function (error) {
      var code = String(error && error.message || "runtime");
      if (["auth_required", "timeout", "runtime", "unsupported", "argument"].indexOf(code) < 0) code = "runtime";
      fail(request, code);
    });
  }

  function pump() {
    if (!gluspotAppleLibrary.musicKitInstance(window)) {
      setTimeout(pump, 250);
      return;
    }
    if (!musickitAnnounced) {
      musickitAnnounced = true;
      // The host learns the page is ready from this callback. It does not
      // read a script return value.
      post({v: 1, op: "ready"});
    }
    var queued = waiting;
    waiting = [];
    for (var index = 0; index < queued.length; index += 1) finish(queued[index]);
  }

  window.chrome.webview.addEventListener("message", function (event) {
    var request = event.data || {};
    if (!request.id || request.v !== 1) return;
    if (!gluspotAppleLibrary.musicKitInstance(window)) {
      waiting.push(request);
      return;
    }
    finish(request);
  });
  pump();
})();
