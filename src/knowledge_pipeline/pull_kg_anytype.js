import { createClient } from "anytypeHelper@v1";

function boolArg(value) {
  return value === true || value === "true" || value === "1" || value === "yes";
}

function errorText(res) {
  if (!res) return "unknown error";
  if (typeof res.error === "string") return res.error;
  if (res.error && typeof res.error.message === "string") return res.error.message;
  return "unknown error";
}

var relationProperties = {
  person: ["affiliated_with", "member_of", "scopes"],
  institution: ["scopes"],
  paper: ["authored_by", "corporate_authors", "about", "scopes"],
  talk: ["presented_by", "presented_at", "about", "scopes"],
  concept: ["broader", "related_to", "scopes"],
  event: ["scopes"],
  graph_scope: ["members"]
};

export function main(args) {
  args = args || {};
  if (!args.spaceId) throw new Error("Pass spaceId=<id>");
  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: args.spaceId
  });

  if (args.stamps) {
    var stamps = typeof args.stamps === "string" ? JSON.parse(args.stamps) : args.stamps;
    var dryRun = boolArg(args.dryRun);
    var results = [];
    for (var s = 0; s < stamps.length; s++) {
      var stamp = stamps[s];
      var res = dryRun ? { ok: true } : client.updateObject(stamp.anytype_id, {
        properties: { canonical_id: stamp.canonical_id },
        typeKey: stamp.type_key
      });
      results.push({
        anytype_id: stamp.anytype_id,
        canonical_id: stamp.canonical_id,
        ok: !!res.ok,
        error: res.ok ? null : errorText(res)
      });
    }
    return JSON.stringify({
      dry_run: dryRun,
      space_id: args.spaceId,
      stamps: results,
      errors: results.filter(function (row) { return !row.ok; }).length
    });
  }

  var objects = [];
  for (var typeKey in relationProperties) {
    if (!relationProperties.hasOwnProperty(typeKey)) continue;
    var rows = client.getObjects(typeKey, { limit: 100, resolveRefs: false });
    if (rows.error) {
      return JSON.stringify({ space_id: args.spaceId, error: String(rows.error) });
    }
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row.archived) continue;
      var values = {};
      var properties = relationProperties[typeKey];
      for (var p = 0; p < properties.length; p++) {
        var key = properties[p];
        values[key] = Array.isArray(row[key]) ? row[key].slice().sort() : [];
      }
      objects.push({
        anytype_id: row.id,
        type_key: typeKey,
        name: row.name || "",
        canonical_id: row.canonical_id || "",
        relation_values: values
      });
    }
  }
  objects.sort(function (a, b) { return a.anytype_id < b.anytype_id ? -1 : (a.anytype_id > b.anytype_id ? 1 : 0); });
  return JSON.stringify({ space_id: args.spaceId, objects: objects });
}
