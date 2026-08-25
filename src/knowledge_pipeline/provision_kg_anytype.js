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

function parseProfile(value) {
  if (!value) throw new Error("Pass a validated ontology profile with profile=<json>");
  return typeof value === "string" ? JSON.parse(value) : value;
}

function indexByKey(rows) {
  var result = {};
  for (var i = 0; i < rows.length; i++) {
    if (rows[i] && rows[i].key) result[rows[i].key] = rows[i];
  }
  return result;
}

function effectiveProperty(typeKey, property, spaceProperties) {
  var existing = spaceProperties[property.key];
  if (existing && existing.format && property.format && existing.format !== property.format) {
    return {
      key: typeKey + "_" + property.key,
      name: property.name,
      format: property.format,
      renamed_from: property.key
    };
  }
  return property;
}

function planType(typeDef, existingTypes, spaceProperties) {
  var existing = existingTypes[typeDef.key];
  var existingProperties = indexByKey(existing && Array.isArray(existing.properties) ? existing.properties : []);
  var missing = [];
  var renamed = {};

  for (var i = 0; i < typeDef.properties.length; i++) {
    var effective = effectiveProperty(typeDef.key, typeDef.properties[i], spaceProperties);
    if (effective.renamed_from) renamed[effective.renamed_from] = effective.key;
    if (!existingProperties[effective.key]) missing.push(effective.key);
  }

  var metadataChanged = !!existing && existing.name !== typeDef.name;
  var action = !existing ? "create" : (missing.length > 0 || metadataChanged ? "update" : "unchanged");
  return {
    key: typeDef.key,
    name: typeDef.name,
    action: action,
    missing_properties: missing,
    metadata_changed: metadataChanged,
    renamed_properties: renamed
  };
}

export function main(args) {
  args = args || {};
  var profile = parseProfile(args.profile);
  var dryRun = boolArg(args.dryRun);
  var client = createClient({
    apiBaseUrl: env.ANYTYPE_API_URL,
    apiKey: env.ANYTYPE_API_KEY,
    spaceId: profile.space_id
  });

  var existingTypes = indexByKey(client.getTypes({ raw: true }));
  var spaceProperties = indexByKey(client.getProperties());
  var details = [];
  var stats = {
    types_defined: profile.types.length,
    planned_create: 0,
    planned_update: 0,
    unchanged: 0,
    created: 0,
    updated: 0,
    errors: 0
  };

  for (var i = 0; i < profile.types.length; i++) {
    var typeDef = profile.types[i];
    var plan = planType(typeDef, existingTypes, spaceProperties);
    if (plan.action === "create") stats.planned_create += 1;
    else if (plan.action === "update") stats.planned_update += 1;
    else stats.unchanged += 1;

    if (!dryRun && plan.action !== "unchanged") {
      var result = client.createType({
        key: typeDef.key,
        name: typeDef.name,
        plural_name: typeDef.plural_name,
        layout: typeDef.layout,
        properties: typeDef.properties
      });
      plan.ok = !!(result && result.ok);
      if (!plan.ok) {
        plan.error = errorText(result);
        stats.errors += 1;
      } else {
        if (plan.action === "create") stats.created += 1;
        else stats.updated += 1;
        if (result.property_warnings) plan.property_warnings = result.property_warnings;
        if (result.renamed_properties) plan.renamed_properties = result.renamed_properties;
      }
    }
    details.push(plan);
  }

  return JSON.stringify({
    profile: profile.key,
    space_id: profile.space_id,
    dry_run: dryRun,
    stats: stats,
    details: details
  });
}
