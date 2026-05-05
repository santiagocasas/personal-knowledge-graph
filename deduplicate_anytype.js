import { createClient } from "anytypeHelper@v1";

function boolArg(value) {
  return value === true || value === "true" || value === "1" || value === "yes";
}

function cleanUrl(url) {
  return (url || "").trim();
}

export function main(args) {
  args = args || {};
  var dryRun = !boolArg(args.yes);
  var limit = parseInt(args.limit || "0", 10);
  if (isNaN(limit)) limit = 0;

  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: env.ANYTYPE_SPACE_ID
  });

  var objects = client.getObjects("bookmark", { limit: 100, resolveRefs: false });
  var byUrl = {};
  for (var i = 0; i < objects.length; i++) {
    var obj = objects[i];
    if (obj.archived) continue;
    var url = cleanUrl(obj.source);
    if (!url) continue;
    if (!byUrl[url]) byUrl[url] = [];
    byUrl[url].push(obj);
  }

  var duplicateUrls = 0;
  var duplicateObjects = 0;
  var archived = 0;
  var errors = 0;
  var examples = [];

  for (var url in byUrl) {
    if (!byUrl.hasOwnProperty(url)) continue;
    var group = byUrl[url];
    if (group.length < 2) continue;
    duplicateUrls += 1;
    duplicateObjects += group.length - 1;
    examples.push({ url: url, keep: group[0].id, archive: group.slice(1).map(function(o) { return o.id; }) });

    for (var i = 1; i < group.length; i++) {
      if (limit > 0 && archived >= limit) continue;
      if (dryRun) continue;
      var res = client.deleteObject(group[i].id);
      if (res.ok) archived += 1;
      else {
        errors += 1;
        console.log("delete error " + group[i].id + ": " + res.error);
      }
    }
  }

  return JSON.stringify({
    dry_run: dryRun,
    scanned: objects.length,
    duplicate_urls: duplicateUrls,
    duplicate_objects: duplicateObjects,
    archived: archived,
    errors: errors,
    examples: examples.slice(0, 10),
    next_step: dryRun ? "Re-run with yes=true to archive duplicates." : "Duplicates archived."
  });
}
