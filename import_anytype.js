import { createClient } from "anytypeHelper@v1";

function boolArg(value) {
  return value === true || value === "true" || value === "1" || value === "yes";
}

function intArg(value, fallback) {
  var parsed = parseInt(value || "", 10);
  return isNaN(parsed) ? fallback : parsed;
}

function cleanUrl(url) {
  return (url || "").trim();
}

function unique(values) {
  var seen = {};
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var value = (values[i] || "").trim();
    var key = value.toLowerCase();
    if (!value || seen[key]) continue;
    seen[key] = true;
    out.push(value);
  }
  return out;
}

function bookmarkTags(bookmark) {
  var matched = Array.isArray(bookmark.matched_tags) ? bookmark.matched_tags : [];
  var proposed = Array.isArray(bookmark.proposed_tags) ? bookmark.proposed_tags : [];
  return unique(matched.concat(proposed));
}

function bookmarkProperties(bookmark) {
  var props = [
    { key: "source", url: cleanUrl(bookmark.url) },
    { key: "tag", multi_select: bookmarkTags(bookmark) }
  ];

  if (bookmark.llm_description) {
    props.push({ key: "description", text: bookmark.llm_description });
  }

  return props;
}

function normalizeTagName(name) {
  return (name || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function tagLookup(client) {
  var tags = client.listTags("tag");
  var byName = {};
  var byKey = {};
  for (var i = 0; i < tags.length; i++) {
    var tag = tags[i];
    if (tag.name) byName[normalizeTagName(tag.name)] = tag.key;
    if (tag.key) byKey[tag.key] = true;
  }
  return { byName: byName, byKey: byKey };
}

function desiredTagKeys(bookmark, tags) {
  var names = bookmarkTags(bookmark);
  var keys = [];
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    var key = tags.byName[normalizeTagName(name)] || (tags.byKey[name] ? name : null);
    keys.push(key || "__missing__:" + normalizeTagName(name));
  }
  return keys.sort();
}

function existingTagKeys(obj) {
  var keys = Array.isArray(obj.tag) ? obj.tag.slice(0) : [];
  return keys.sort();
}

function sameString(left, right) {
  return (left || "").trim() === (right || "").trim();
}

function sameArray(left, right) {
  if (left.length !== right.length) return false;
  for (var i = 0; i < left.length; i++) {
    if (left[i] !== right[i]) return false;
  }
  return true;
}

function diffBookmark(bookmark, existing, tags) {
  if (!existing) return ["missing"];

  var changes = [];
  if (!sameString(existing.name, bookmark.title || "Untitled")) changes.push("name");
  if (bookmark.llm_description && !sameString(existing.description, bookmark.llm_description)) {
    changes.push("description");
  }
  if (!sameArray(existingTagKeys(existing), desiredTagKeys(bookmark, tags))) changes.push("tags");
  return changes;
}

function indexBookmarks(client) {
  var objects = client.getObjects("bookmark", { limit: 100, resolveRefs: false });
  var byUrl = {};
  var total = 0;

  for (var i = 0; i < objects.length; i++) {
    var obj = objects[i];
    if (obj.archived) continue;
    var url = cleanUrl(obj.source);
    if (!url) continue;
    if (!byUrl[url]) byUrl[url] = [];
    byUrl[url].push(obj);
    total += 1;
  }

  return { byUrl: byUrl, total: total, fetched: objects.length, error: objects.error || null };
}

function plan(bookmarks, index, tags) {
  var result = { create: [], update: [], unchanged: [], skip: [], duplicateUrls: 0, duplicateObjects: 0, changeReasons: {} };

  for (var i = 0; i < bookmarks.length; i++) {
    var bookmark = bookmarks[i];
    var url = cleanUrl(bookmark.url);
    if (!url) {
      result.skip.push(bookmark);
      continue;
    }

    var matches = index.byUrl[url] || [];
    if (matches.length === 0) result.create.push(bookmark);
    else {
      var changes = diffBookmark(bookmark, matches[0], tags);
      if (changes.length > 0) {
        result.update.push(bookmark);
        for (var c = 0; c < changes.length; c++) {
          result.changeReasons[changes[c]] = (result.changeReasons[changes[c]] || 0) + 1;
        }
      } else {
        result.unchanged.push(bookmark);
      }
    }
    if (matches.length > 1) {
      result.duplicateUrls += 1;
      result.duplicateObjects += matches.length - 1;
    }
  }

  return result;
}

function syncBookmarks(client, bookmarks, options) {
  var index = indexBookmarks(client);
  var tags = tagLookup(client);
  var stats = { created: 0, updated: 0, unchanged: 0, skipped: 0, errors: 0 };

  for (var i = 0; i < bookmarks.length; i++) {
    var bookmark = bookmarks[i];
    var url = cleanUrl(bookmark.url);
    var title = bookmark.title || "Untitled";

    if (!url) {
      stats.skipped += 1;
      continue;
    }

    if (options.verbose) console.log("sync " + (i + 1) + "/" + bookmarks.length + ": " + title);

    var existing = (index.byUrl[url] || [])[0] || null;
    var changes = diffBookmark(bookmark, existing, tags);
    var payload = {
      name: title,
      properties: bookmarkProperties(bookmark),
      typeKey: "bookmark"
    };

    var res;
    if (existing) {
      if (changes.length === 0) {
        stats.unchanged += 1;
        continue;
      }
      res = client.updateObject(existing.id, payload);
      if (res.ok) {
        stats.updated += 1;
        tags = tagLookup(client);
      }
    } else {
      res = client.createObject("bookmark", payload);
      if (res.ok) {
        stats.created += 1;
        tags = tagLookup(client);
        if (!index.byUrl[url]) index.byUrl[url] = [];
        index.byUrl[url].push(res.object || { id: res.id, source: url });
      }
    }

    if (!res || !res.ok) {
      stats.errors += 1;
      console.log("error: " + title + " -> " + ((res && res.error) || "unknown error"));
    }
  }

  return stats;
}

function verify(bookmarks, index) {
  var missing = [];
  for (var i = 0; i < bookmarks.length; i++) {
    var bookmark = bookmarks[i];
    var url = cleanUrl(bookmark.url);
    if (!url) continue;
    if (!index.byUrl[url] || index.byUrl[url].length === 0) missing.push(bookmark);
  }
  return { found: bookmarks.length - missing.length, missing: missing.length, missing_examples: missing.slice(0, 10) };
}

export function main(args) {
  args = args || {};
  if (!args.input) throw new Error("Pass categorized bookmarks as input=@data/categorized.json");

  var bookmarks = JSON.parse(args.input);
  var limit = intArg(args.limit, 0);
  if (limit > 0) bookmarks = bookmarks.slice(0, limit);

  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: env.ANYTYPE_SPACE_ID
  });

  var mode = args.mode || "sync";
  var options = { verbose: boolArg(args.verbose), dryRun: boolArg(args.dryRun) };

  var initialIndex = indexBookmarks(client);
  var initialTags = tagLookup(client);
  var initialPlan = plan(bookmarks, initialIndex, initialTags);
  var result = {
    mode: mode,
    loaded: bookmarks.length,
    existing_bookmarks: initialIndex.total,
    report: {
      create: initialPlan.create.length,
      update: initialPlan.update.length,
      unchanged: initialPlan.unchanged.length,
      skip: initialPlan.skip.length,
      duplicate_urls_in_anytype: initialPlan.duplicateUrls,
      duplicate_objects_to_cleanup: initialPlan.duplicateObjects,
      change_reasons: initialPlan.changeReasons
    }
  };

  if (mode === "report" || options.dryRun) return JSON.stringify(result);

  if (mode === "verify") {
    result.verify = verify(bookmarks, initialIndex);
    return JSON.stringify(result);
  }

  result.sync = syncBookmarks(client, bookmarks, options);

  if (boolArg(args.verify)) {
    result.verify = verify(bookmarks, indexBookmarks(client));
  }

  return JSON.stringify(result);
}
