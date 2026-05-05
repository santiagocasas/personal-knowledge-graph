import { createClient } from "anytypeHelper@v1";

function boolArg(value) {
  return value === true || value === "true" || value === "1" || value === "yes";
}

function sleepMs(ms) {
  var start = Date.now();
  while (Date.now() - start < ms) {
    // busy wait (goja runtime has no blocking sleep)
  }
}

function errorText(res) {
  if (!res) return "unknown error";
  if (typeof res.error === "string") return res.error;
  if (res.error && typeof res.error.message === "string") return res.error.message;
  return "unknown error";
}

function isRateLimitError(res) {
  var msg = errorText(res).toLowerCase();
  return msg.indexOf("maximum request limit") >= 0 || msg.indexOf("rate_limit") >= 0 || msg.indexOf("429") >= 0;
}

function writeWithRetry(writeFn, retryCount, baseDelayMs) {
  var attempt = 0;
  var res;
  while (attempt <= retryCount) {
    res = writeFn();
    if (res && res.ok) return res;
    if (!isRateLimitError(res) || attempt === retryCount) return res;
    var waitMs = baseDelayMs * Math.pow(2, attempt);
    console.log("rate-limited; retrying in " + waitMs + "ms");
    sleepMs(waitMs);
    attempt += 1;
  }
  return res;
}

function intArg(value, fallback) {
  var parsed = parseInt(value || "", 10);
  return isNaN(parsed) ? fallback : parsed;
}

function cleanUrl(url) {
  return (url || "").trim();
}

function titleForTopic(topic) {
  return "Web Topic: " + (topic || "Misc");
}

function markdownForGuide(guide) {
  if (!guide.markdown) return "## Overview\n\nCurated topic guide.\n";
  return guide.markdown;
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

function guideTags(guide) {
  return unique(Array.isArray(guide.tags) ? guide.tags : []);
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
    if (tag.name) byName[normalizeTagName(tag.name)] = tag.key || tag.name;
    if (tag.key) byKey[normalizeTagName(tag.key)] = tag.key;
  }
  return { byName: byName, byKey: byKey, count: tags.length };
}

function safeGuideTags(guide, tags) {
  var raw = guideTags(guide);
  var kept = [];
  var skipped = [];
  for (var i = 0; i < raw.length; i++) {
    var value = raw[i];
    var key = normalizeTagName(value);
    var resolved = tags.byName[key] || tags.byKey[key];
    if (resolved) kept.push(resolved);
    else skipped.push(value);
  }
  return { kept: unique(kept), skipped: unique(skipped) };
}

function ensureRelationProperties(client) {
  var pageRes = client.createType({
    key: "page",
    properties: [
      { key: "tag", name: "Tag", format: "multi_select" },
      { key: "related_bookmarks", name: "Related bookmarks", format: "objects" }
    ]
  });
  var bookmarkRes = client.createType({
    key: "bookmark",
    properties: [
      { key: "topic_guides", name: "Topic guides", format: "objects" }
    ]
  });
  return { page: pageRes, bookmark: bookmarkRes };
}

function mergeIds(left, right) {
  var seen = {};
  var out = [];
  var all = (Array.isArray(left) ? left : []).concat(Array.isArray(right) ? right : []);
  for (var i = 0; i < all.length; i++) {
    var id = all[i];
    if (!id || seen[id]) continue;
    seen[id] = true;
    out.push(id);
  }
  return out;
}

function sameSet(left, right) {
  var a = unique(left).sort();
  var b = unique(right).sort();
  if (a.length !== b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function tagValues(value) {
  if (!Array.isArray(value)) return [];
  var out = [];
  for (var i = 0; i < value.length; i++) {
    var item = value[i];
    if (typeof item === "string") out.push(normalizeTagName(item));
    else if (item && item.name) out.push(normalizeTagName(item.name));
    else if (item && item.key) out.push(normalizeTagName(item.key));
  }
  return out;
}

function asIds(value) {
  if (!Array.isArray(value)) return [];
  var out = [];
  for (var i = 0; i < value.length; i++) {
    var item = value[i];
    if (typeof item === "string") out.push(item);
    else if (item && item.id) out.push(item.id);
  }
  return out;
}

function pageDiff(existing, pageMarkdown, tagNames, bookmarkIds) {
  var reasons = [];
  if (!existing) return ["new"];
  if (((existing.markdown || "").trim()) !== ((pageMarkdown || "").trim())) reasons.push("markdown");
  if (!sameSet(tagValues(existing.tag), tagValues(tagNames))) reasons.push("tags");
  if (!sameSet(asIds(existing.related_bookmarks), bookmarkIds)) reasons.push("related_bookmarks");
  return reasons;
}

function findPageByName(client, name) {
  var results = client.search({ query: name, types: ["page"], limit: 10 });
  for (var i = 0; i < results.length; i++) {
    if ((results[i].name || "").trim() === name.trim()) return results[i];
  }
  return null;
}

function indexBookmarksByUrl(client) {
  var rows = client.getObjects("bookmark", { limit: 100, resolveRefs: false });
  var index = {};
  for (var i = 0; i < rows.length; i++) {
    var obj = rows[i];
    if (obj.archived) continue;
    var url = cleanUrl(obj.source);
    if (!url) continue;
    if (!index[url]) index[url] = [];
    index[url].push(obj);
  }
  return index;
}

export function main(args) {
  args = args || {};
  if (!args.input) throw new Error("Pass guides manifest with input=@data/topic_guides.json");

  var guides = JSON.parse(args.input);
  var limit = intArg(args.limit, 0);
  if (limit > 0) guides = guides.slice(0, limit);

  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: env.ANYTYPE_SPACE_ID
  });

  var dryRun = boolArg(args.dryRun);
  var includeLinksInPages = boolArg(args.withObjectLinks);
  var retryCount = intArg(args.retryCount, 4);
  var retryDelayMs = intArg(args.retryDelayMs, 1200);
  var bookmarkIndex = indexBookmarksByUrl(client);
  var tags = tagLookup(client);
  var setup = dryRun
    ? { page: { ok: true, dry_run: true }, bookmark: { ok: true, dry_run: true } }
    : ensureRelationProperties(client);
  var stats = {
    guides_loaded: guides.length,
    planned_pages_create: 0,
    planned_pages_update: 0,
    pages_created: 0,
    pages_updated: 0,
    page_errors: 0,
    pages_unchanged: 0,
    bookmarks_matched: 0,
    bookmarks_unmatched: 0,
    page_tags_available: tags.count,
    page_tags_skipped: 0,
    bookmark_links_updated: 0,
    relation_setup_page_ok: !!(setup.page && setup.page.ok),
    relation_setup_bookmark_ok: !!(setup.bookmark && setup.bookmark.ok)
  };

  var details = [];
  var pageIdsByBookmarkId = {};
  for (var i = 0; i < guides.length; i++) {
    var guide = guides[i];
    var topic = guide.topic || "Misc";
    var pageName = titleForTopic(topic);
    var pageMarkdown = markdownForGuide(guide);
    var matched = 0;
    var unmatched = 0;
    var bookmarkIds = [];
    var resolvedTags = safeGuideTags(guide, tags);
    stats.page_tags_skipped += resolvedTags.skipped.length;

    if (includeLinksInPages && Array.isArray(guide.bookmarks)) {
      for (var b = 0; b < guide.bookmarks.length; b++) {
        var bm = guide.bookmarks[b];
        var url = cleanUrl(bm.url);
        var matches = bookmarkIndex[url] || [];
        if (matches.length > 0) {
          matched += 1;
          bookmarkIds.push(matches[0].id);
        } else {
          unmatched += 1;
        }
      }
    }

    var existing = findPageByName(client, pageName);
    var existingFull = existing ? client.getObject(existing.id, { resolveRefs: false }) : null;
    var diff = pageDiff(existingFull, pageMarkdown, resolvedTags.kept, bookmarkIds);
    var action = existing ? "update" : "create";
    if (action === "create") stats.planned_pages_create += 1;
    else if (diff.length > 0) stats.planned_pages_update += 1;
    else stats.pages_unchanged += 1;

    if (!dryRun && diff.length > 0) {
      var result;
      var properties = [
        { key: "tag", multi_select: resolvedTags.kept },
        { key: "related_bookmarks", objects: bookmarkIds }
      ];
      if (existing) {
        result = writeWithRetry(function () {
          return client.updateObject(existing.id, { name: pageName, markdown: pageMarkdown, properties: properties, typeKey: "page" });
        }, retryCount, retryDelayMs);
      } else {
        result = writeWithRetry(function () {
          return client.createObject("page", { name: pageName, markdown: pageMarkdown, properties: properties });
        }, retryCount, retryDelayMs);
      }

      if (!result.ok) {
        stats.page_errors += 1;
        details.push({ topic: topic, action: action, ok: false, error: errorText(result) });
        continue;
      }
      if (existing) stats.pages_updated += 1;
      else stats.pages_created += 1;

      var pageId = existing ? existing.id : (result.id || (result.object && result.object.id));
      if (pageId && includeLinksInPages) {
        for (var m = 0; m < bookmarkIds.length; m++) {
          var bookmarkId = bookmarkIds[m];
          if (!pageIdsByBookmarkId[bookmarkId]) pageIdsByBookmarkId[bookmarkId] = [];
          pageIdsByBookmarkId[bookmarkId].push(pageId);
        }
      }
    } else if (!dryRun && existing && diff.length === 0 && includeLinksInPages) {
      for (var u = 0; u < bookmarkIds.length; u++) {
        var unchangedBookmarkId = bookmarkIds[u];
        if (!pageIdsByBookmarkId[unchangedBookmarkId]) pageIdsByBookmarkId[unchangedBookmarkId] = [];
        pageIdsByBookmarkId[unchangedBookmarkId].push(existing.id);
      }
    }

    stats.bookmarks_matched += matched;
    stats.bookmarks_unmatched += unmatched;
    details.push({
      topic: topic,
      action: action,
      ok: true,
      matched: matched,
      unmatched: unmatched,
      changed: diff.length > 0,
      change_reasons: diff,
      tags: resolvedTags.kept.length,
      skipped_tags: resolvedTags.skipped
    });
  }

  if (!dryRun && includeLinksInPages) {
    for (var bookmarkId in pageIdsByBookmarkId) {
      if (!pageIdsByBookmarkId.hasOwnProperty(bookmarkId)) continue;
      var existingBookmark = client.getObject(bookmarkId);
      var current = existingBookmark && Array.isArray(existingBookmark.topic_guides) ? existingBookmark.topic_guides : [];
      var desired = mergeIds(current, pageIdsByBookmarkId[bookmarkId]);
      var linkRes = writeWithRetry(function () {
        return client.updateObject(bookmarkId, {
          properties: [{ key: "topic_guides", objects: desired }],
          typeKey: "bookmark"
        });
      }, retryCount, retryDelayMs);
      if (linkRes && linkRes.ok) stats.bookmark_links_updated += 1;
      else details.push({ bookmark_id: bookmarkId, action: "link_topic_guides", ok: false, error: errorText(linkRes) });
    }
  }

  return JSON.stringify({ dry_run: dryRun, stats: stats, details: details.slice(0, 30) });
}
