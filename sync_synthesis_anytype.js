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

function titleForTopic(topic) {
  return "Topic Guide: " + (topic || "Misc");
}

function markdownForGuide(guide) {
  if (!guide.markdown) return "# " + titleForTopic(guide.topic) + "\n";
  return guide.markdown;
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
  var bookmarkIndex = indexBookmarksByUrl(client);
  var stats = {
    guides_loaded: guides.length,
    pages_created: 0,
    pages_updated: 0,
    page_errors: 0,
    bookmarks_matched: 0,
    bookmarks_unmatched: 0
  };

  var details = [];
  for (var i = 0; i < guides.length; i++) {
    var guide = guides[i];
    var topic = guide.topic || "Misc";
    var pageName = titleForTopic(topic);
    var pageMarkdown = markdownForGuide(guide);
    var matched = 0;
    var unmatched = 0;
    var lines = [];

    if (includeLinksInPages && Array.isArray(guide.bookmarks)) {
      lines.push("", "## Anytype Bookmark Objects", "");
      for (var b = 0; b < guide.bookmarks.length; b++) {
        var bm = guide.bookmarks[b];
        var url = cleanUrl(bm.url);
        var matches = bookmarkIndex[url] || [];
        if (matches.length > 0) {
          matched += 1;
          lines.push("- " + (bm.title || "Untitled") + " (object_id: " + matches[0].id + ")");
        } else {
          unmatched += 1;
          lines.push("- " + (bm.title || "Untitled") + " (not found in Anytype)");
        }
      }
      pageMarkdown = pageMarkdown + "\n" + lines.join("\n") + "\n";
    }

    var existing = findPageByName(client, pageName);
    var action = existing ? "update" : "create";

    if (!dryRun) {
      var result;
      if (existing) {
        result = client.updateObject(existing.id, { name: pageName, markdown: pageMarkdown, typeKey: "page" });
      } else {
        result = client.createObject("page", { name: pageName, markdown: pageMarkdown });
      }

      if (!result.ok) {
        stats.page_errors += 1;
        details.push({ topic: topic, action: action, ok: false, error: result.error || "unknown" });
        continue;
      }
      if (existing) stats.pages_updated += 1;
      else stats.pages_created += 1;
    }

    stats.bookmarks_matched += matched;
    stats.bookmarks_unmatched += unmatched;
    details.push({ topic: topic, action: action, ok: true, matched: matched, unmatched: unmatched });
  }

  return JSON.stringify({ dry_run: dryRun, stats: stats, details: details.slice(0, 30) });
}
