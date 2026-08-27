import { createClient } from "anytypeHelper@v1";

function boolArg(value) {
  return value === true || value === "true" || value === "1" || value === "yes";
}

function intArg(value, fallback) {
  var parsed = parseInt(value || "", 10);
  return isNaN(parsed) ? fallback : parsed;
}

function errorText(res) {
  if (!res) return "unknown error";
  if (typeof res.error === "string") return res.error;
  if (res.error && typeof res.error.message === "string") return res.error.message;
  return "unknown error";
}

function sleepMs(ms) {
  var start = Date.now();
  while (Date.now() - start < ms) {
    // goja does not expose a blocking sleep primitive.
  }
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
    sleepMs(baseDelayMs * Math.pow(2, attempt));
    attempt += 1;
  }
  return res;
}

function sameScalar(left, right) {
  if (typeof right === "number") return Number(left) === right;
  return String(left === null || left === undefined ? "" : left).trim() === String(right === null || right === undefined ? "" : right).trim();
}

function sortedUnique(values) {
  var seen = {};
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var value = values[i];
    if (!value || seen[value]) continue;
    seen[value] = true;
    out.push(value);
  }
  return out.sort();
}

function sameSet(left, right) {
  var a = sortedUnique(Array.isArray(left) ? left : []);
  var b = sortedUnique(Array.isArray(right) ? right : []);
  if (a.length !== b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function projectedProperties(item) {
  var source = item.properties || {};
  var allowed = {
    description: 1,
    orcid: 1,
    institution_kind: 1,
    ror: 1,
    doi: 1,
    arxiv_id: 1,
    ads_bibcode: 1,
    ads_url: 1,
    publication_year: 1,
    publication: 1,
    citation_count: 1,
    author_count: 1,
    ads_keywords: 1,
    source_url: 1,
    author_list: 1,
    skos_uri: 1,
    start_date: 1,
    end_date: 1,
    location: 1,
    website: 1,
    scope_kind: 1
  };
  var props = { canonical_id: item.canonical_id };
  for (var key in source) {
    if (source.hasOwnProperty(key) && allowed[key] && source[key] !== null && source[key] !== "") {
      props[key] = source[key];
    }
  }
  if (item.type_key === "concept" && !props.description && source.scheme) {
    props.description = source.scheme + " concept";
  }
  return props;
}

function scalarDiff(existing, item, properties) {
  if (!existing) return ["missing"];
  var reasons = [];
  if (!sameScalar(existing.name, item.name)) reasons.push("name");
  for (var key in properties) {
    if (properties.hasOwnProperty(key) && !sameScalar(existing[key], properties[key])) reasons.push(key);
  }
  return reasons;
}

function buildIndex(client, typeKeys) {
  var byCanonicalId = {};
  var duplicates = [];
  for (var i = 0; i < typeKeys.length; i++) {
    var rows = client.getObjects(typeKeys[i], { limit: 100, resolveRefs: false });
    for (var j = 0; j < rows.length; j++) {
      var row = rows[j];
      if (row.archived || !row.canonical_id) continue;
      if (byCanonicalId[row.canonical_id]) duplicates.push(row.canonical_id);
      else byCanonicalId[row.canonical_id] = row;
    }
  }
  return { byCanonicalId: byCanonicalId, duplicates: sortedUnique(duplicates) };
}

function relationPlan(manifest, index) {
  var desiredCanonical = {};
  for (var o = 0; o < manifest.objects.length; o++) {
    var item = manifest.objects[o];
    desiredCanonical[item.canonical_id] = {};
    var managed = item.relation_properties || [];
    for (var m = 0; m < managed.length; m++) desiredCanonical[item.canonical_id][managed[m]] = [];
  }
  for (var i = 0; i < manifest.relations.length; i++) {
    var relation = manifest.relations[i];
    if (!desiredCanonical[relation.subject]) desiredCanonical[relation.subject] = {};
    if (!desiredCanonical[relation.subject][relation.property_key]) desiredCanonical[relation.subject][relation.property_key] = [];
    desiredCanonical[relation.subject][relation.property_key].push(relation.object);
  }

  var plans = [];
  for (var subjectId in desiredCanonical) {
    if (!desiredCanonical.hasOwnProperty(subjectId)) continue;
    var subject = index[subjectId];
    var desiredByProperty = desiredCanonical[subjectId];
    var desiredProperties = {};
    var changedProperties = [];
    var unresolved = [];
    for (var propertyKey in desiredByProperty) {
      if (!desiredByProperty.hasOwnProperty(propertyKey)) continue;
      var targetCanonicalIds = sortedUnique(desiredByProperty[propertyKey]);
      var targetIds = [];
      for (var t = 0; t < targetCanonicalIds.length; t++) {
        var target = index[targetCanonicalIds[t]];
        if (target && target.id) targetIds.push(target.id);
        else unresolved.push(targetCanonicalIds[t]);
      }
      targetIds = sortedUnique(targetIds);
      var currentIds = subject && Array.isArray(subject[propertyKey]) ? subject[propertyKey] : [];
      desiredProperties[propertyKey] = targetIds;
      if (!subject || !sameSet(currentIds, targetIds)) changedProperties.push(propertyKey);
    }
    plans.push({
      subject_canonical_id: subjectId,
      subject: subject,
      properties: desiredProperties,
      unresolved: sortedUnique(unresolved),
      changed_properties: changedProperties,
      changed: !subject || unresolved.length > 0 || changedProperties.length > 0
    });
  }
  return plans;
}

export function main(args) {
  args = args || {};
  if (!args.manifest) throw new Error("Pass a projection manifest with manifest=<json>");
  var manifest = typeof args.manifest === "string" ? JSON.parse(args.manifest) : args.manifest;
  var dryRun = boolArg(args.dryRun);
  var retryCount = intArg(args.retryCount, 4);
  var retryDelayMs = intArg(args.retryDelayMs, 1200);
  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: manifest.space_id
  });
  var typeKeys = ["person", "institution", "paper", "talk", "concept", "event", "graph_scope"];
  var indexed = buildIndex(client, typeKeys);
  if (indexed.duplicates.length > 0) {
    return JSON.stringify({ dry_run: dryRun, space_id: manifest.space_id, error: "duplicate canonical IDs in Anytype", duplicate_canonical_ids: indexed.duplicates });
  }

  var stats = {
    objects_loaded: manifest.objects.length,
    relations_loaded: manifest.relations.length,
    planned_create: 0,
    planned_update: 0,
    planned_link_update: 0,
    unchanged: 0,
    created: 0,
    updated: 0,
    links_updated: 0,
    errors: 0
  };
  var details = [];
  var typeByCanonicalId = {};

  for (var i = 0; i < manifest.objects.length; i++) {
    var item = manifest.objects[i];
    typeByCanonicalId[item.canonical_id] = item.type_key;
    var existing = indexed.byCanonicalId[item.canonical_id] || null;
    var properties = projectedProperties(item);
    var diff = scalarDiff(existing, item, properties);
    var action = !existing ? "create" : (diff.length > 0 ? "update" : "unchanged");
    if (action === "create") stats.planned_create += 1;
    else if (action === "update") stats.planned_update += 1;
    else stats.unchanged += 1;

    if (!dryRun && action !== "unchanged") {
      var result;
      if (existing) {
        result = writeWithRetry(function () {
          return client.updateObject(existing.id, {
            name: item.name,
            properties: properties,
            typeKey: item.type_key
          });
        }, retryCount, retryDelayMs);
      } else {
        result = writeWithRetry(function () {
          return client.createObject(item.type_key, { name: item.name, properties: properties });
        }, retryCount, retryDelayMs);
      }
      if (!result || !result.ok) {
        stats.errors += 1;
        details.push({ canonical_id: item.canonical_id, name: item.name, action: action, ok: false, error: errorText(result) });
        continue;
      }
      if (action === "create") stats.created += 1;
      else stats.updated += 1;
      indexed.byCanonicalId[item.canonical_id] = result.object || { id: result.id, canonical_id: item.canonical_id };
    }
    details.push({ canonical_id: item.canonical_id, name: item.name, type_key: item.type_key, action: action, changes: diff, ok: true });
  }

  // Refresh after writes so link comparison sees preserved and newly-created fields.
  if (!dryRun && stats.errors > 0) {
    return JSON.stringify({ dry_run: dryRun, space_id: manifest.space_id, stats: stats, details: details });
  }
  if (!dryRun) indexed = buildIndex(client, typeKeys);
  var linkPlans = relationPlan(manifest, indexed.byCanonicalId);
  for (var p = 0; p < linkPlans.length; p++) {
    var plan = linkPlans[p];
    if (plan.changed) stats.planned_link_update += 1;
    if (dryRun || !plan.changed) continue;
    if (!plan.subject || plan.unresolved.length > 0) {
      stats.errors += 1;
      details.push({ canonical_id: plan.subject_canonical_id, action: "link", ok: false, error: "unresolved relation endpoints: " + plan.unresolved.join(", ") });
      continue;
    }
    var linkRes = writeWithRetry(function () {
      return client.updateObject(plan.subject.id, {
        properties: plan.properties,
        typeKey: typeByCanonicalId[plan.subject_canonical_id]
      });
    }, retryCount, retryDelayMs);
    if (linkRes && linkRes.ok) stats.links_updated += 1;
    else {
      stats.errors += 1;
      details.push({ canonical_id: plan.subject_canonical_id, action: "link", properties: plan.changed_properties, ok: false, error: errorText(linkRes) });
    }
  }

  return JSON.stringify({ dry_run: dryRun, space_id: manifest.space_id, stats: stats, details: details });
}
