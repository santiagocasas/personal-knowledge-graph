import { createClient } from "anytypeHelper@v1";

function unique(values) {
  var seen = {};
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var value = values[i];
    if (!value || seen[value]) continue;
    seen[value] = true;
    out.push(value);
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

export function main() {
  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: env.ANYTYPE_SPACE_ID
  });

  var pages = client.getObjects("page", { limit: 100, resolveRefs: false });
  var bookmarks = client.getObjects("bookmark", { limit: 100, resolveRefs: false });
  var guidePages = [];
  var duplicateNames = {};
  var relatedEdges = 0;
  var pageBacklinkEdges = 0;

  for (var i = 0; i < pages.length; i++) {
    var page = pages[i];
    if (page.archived) continue;
    if ((page.name || "").indexOf("Web Topic:") !== 0) continue;
    guidePages.push(page);
    duplicateNames[page.name] = (duplicateNames[page.name] || 0) + 1;
    relatedEdges += asIds(page.related_bookmarks).length;
    pageBacklinkEdges += asIds(page.backlinks).length;
  }

  var duplicateGuides = [];
  for (var name in duplicateNames) {
    if (duplicateNames.hasOwnProperty(name) && duplicateNames[name] > 1) {
      duplicateGuides.push({ name: name, count: duplicateNames[name] });
    }
  }

  var activeBookmarks = 0;
  var bookmarkGuideEdges = 0;
  var bookmarkBacklinkEdges = 0;
  for (var b = 0; b < bookmarks.length; b++) {
    var bookmark = bookmarks[b];
    if (bookmark.archived) continue;
    activeBookmarks += 1;
    bookmarkGuideEdges += asIds(bookmark.topic_guides).length;
    bookmarkBacklinkEdges += asIds(bookmark.backlinks).length;
  }

  return JSON.stringify({
    pages_total: pages.length,
    bookmarks_total: bookmarks.length,
    active_bookmarks: activeBookmarks,
    topic_pages: guidePages.length,
    duplicate_topic_pages: duplicateGuides,
    page_related_bookmark_edges: relatedEdges,
    page_backlink_edges: pageBacklinkEdges,
    bookmark_topic_guide_edges: bookmarkGuideEdges,
    bookmark_backlink_edges: bookmarkBacklinkEdges
  });
}
