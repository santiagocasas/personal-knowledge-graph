import { createClient } from "anytypeHelper@v1";

function boolArg(value) {
  return value === true || value === "true" || value === "1" || value === "yes";
}

function isOldTopicGuide(page) {
  var name = (page.name || "").trim();
  return !page.archived && name.indexOf("Topic Guide:") === 0;
}

export function main(args) {
  args = args || {};
  var dryRun = boolArg(args.dryRun);
  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: env.ANYTYPE_SPACE_ID
  });

  var pages = client.getObjects("page", { limit: 100, resolveRefs: false });
  var candidates = [];
  for (var i = 0; i < pages.length; i++) {
    if (isOldTopicGuide(pages[i])) candidates.push(pages[i]);
  }

  candidates.sort(function (a, b) {
    return (a.name || "").localeCompare(b.name || "");
  });

  var archived = 0;
  var errors = [];
  if (!dryRun) {
    for (var c = 0; c < candidates.length; c++) {
      var res = client.deleteObject(candidates[c].id);
      if (res && res.ok) archived += 1;
      else errors.push({ id: candidates[c].id, name: candidates[c].name, error: res && res.error ? res.error : "unknown error" });
    }
  }

  return JSON.stringify({
    dry_run: dryRun,
    scanned_pages: pages.length,
    candidates: candidates.length,
    archived: archived,
    errors: errors,
    names: candidates.map(function (page) { return page.name; })
  });
}
