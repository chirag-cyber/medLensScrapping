/**
 * ETL Load/Upsert Layer
 * Inserts or updates canonical Medicine entries and their platform Price entries in batches.
 */
const { Medicine, Price } = require("./models");
const {
  buildCanonicalKey,
  buildMatchKeys,
  getMissingDetailFields,
} = require("../transform/transformer");

function mergeUniqueStrings(...groups) {
  return [...new Set(groups.flat().filter(Boolean))];
}

function mergeFaq(existingFaq = [], incomingFaq = []) {
  const merged = [];
  const seen = new Set();

  [...existingFaq, ...incomingFaq].forEach((entry) => {
    const question = entry?.question?.trim();
    const answer = entry?.answer?.trim();
    if (!question || !answer) return;

    const key = `${question.toLowerCase()}::${answer.toLowerCase()}`;
    if (seen.has(key)) return;

    seen.add(key);
    merged.push({ question, answer });
  });

  return merged;
}

function chooseLongerText(existingValue, incomingValue) {
  if (!incomingValue) return existingValue || null;
  if (!existingValue) return incomingValue;
  if (incomingValue === "Unknown Product" && existingValue) return existingValue;
  if (existingValue === "Unknown Product" && incomingValue) return incomingValue;
  return incomingValue.length > existingValue.length ? incomingValue : existingValue;
}

function chooseImageUrl(existingValue, incomingValue) {
  if (incomingValue) return incomingValue;
  return existingValue || null;
}

function getEntityMatchKeys(entity = {}) {
  if (Array.isArray(entity.match_keys) && entity.match_keys.length > 0) {
    return [...new Set(entity.match_keys.filter(Boolean))];
  }

  return buildMatchKeys({
    normalized_name: entity.normalized_name,
    normalized_salt: entity.normalized_salt,
    dosage: entity.dosage,
  });
}

function hasMatchKeyOverlap(groupMatchKeys, recordMatchKeys) {
  return recordMatchKeys.some((key) => groupMatchKeys.has(key));
}

function mergeMatchKeySets(...sets) {
  const merged = new Set();
  sets.forEach((setValue) => {
    if (!setValue) return;
    for (const key of setValue) {
      if (key) merged.add(key);
    }
  });
  return merged;
}

function buildBatchMatchQuery(records) {
  const normalizedNames = [...new Set(records.map((record) => record.normalized_name).filter(Boolean))];
  const saltDosagePairs = [
    ...new Map(
      records
        .filter((record) => record.normalized_salt && record.dosage)
        .map((record) => [
          `${record.normalized_salt}::${record.dosage}`,
          {
            normalized_salt: record.normalized_salt,
            dosage: record.dosage,
          },
        ])
    ).values(),
  ];

  const conditions = [];
  if (normalizedNames.length > 0) {
    conditions.push({ normalized_name: { $in: normalizedNames } });
  }
  saltDosagePairs.forEach((pair) => conditions.push(pair));

  if (conditions.length === 0) return null;
  return { $or: conditions };
}

function pickPrimaryExistingMedicine(existingMedicines = []) {
  if (existingMedicines.length === 0) return null;

  return [...existingMedicines].sort((left, right) => {
    const leftCreatedAt = left.createdAt ? new Date(left.createdAt).getTime() : Number.MAX_SAFE_INTEGER;
    const rightCreatedAt = right.createdAt ? new Date(right.createdAt).getTime() : Number.MAX_SAFE_INTEGER;

    if (leftCreatedAt !== rightCreatedAt) {
      return leftCreatedAt - rightCreatedAt;
    }

    return String(left._id).localeCompare(String(right._id));
  })[0];
}

function mergeExistingMedicineSnapshots(existingMedicines = []) {
  return existingMedicines.reduce(
    (accumulator, medicine) => ({
      canonical_key: accumulator.canonical_key || medicine.canonical_key || null,
      name: chooseLongerText(accumulator.name, medicine.name),
      normalized_name: accumulator.normalized_name || medicine.normalized_name || "",
      salt: chooseLongerText(accumulator.salt, medicine.salt),
      normalized_salt: accumulator.normalized_salt || medicine.normalized_salt || null,
      primary_salt_key: accumulator.primary_salt_key || medicine.primary_salt_key || null,
      salt_tokens: mergeUniqueStrings(accumulator.salt_tokens || [], medicine.salt_tokens || []),
      dosage: accumulator.dosage || medicine.dosage || null,
      pack_size: accumulator.pack_size || medicine.pack_size || null,
      image_url: chooseImageUrl(accumulator.image_url, medicine.image_url),
      manufacturer: chooseLongerText(accumulator.manufacturer, medicine.manufacturer),
      description: chooseLongerText(accumulator.description, medicine.description),
      side_effects: mergeUniqueStrings(
        accumulator.side_effects || [],
        medicine.side_effects || []
      ),
      faq: mergeFaq(accumulator.faq || [], medicine.faq || []),
      source_platforms: mergeUniqueStrings(
        accumulator.source_platforms || [],
        medicine.source_platforms || []
      ),
      last_ingested_at: accumulator.last_ingested_at || medicine.last_ingested_at || null,
    }),
    {
      canonical_key: null,
      name: null,
      normalized_name: "",
      salt: null,
      normalized_salt: null,
      primary_salt_key: null,
      salt_tokens: [],
      dosage: null,
      pack_size: null,
      image_url: null,
      manufacturer: null,
      description: null,
      side_effects: [],
      faq: [],
      source_platforms: [],
      last_ingested_at: null,
    }
  );
}

function buildMedicineUpdate(existingMedicine, transformedRecord) {
  const sourcePlatforms = mergeUniqueStrings(
    existingMedicine?.source_platforms || [],
    transformedRecord.platforms || [transformedRecord.platform]
  );
  const saltTokens = mergeUniqueStrings(
    existingMedicine?.salt_tokens || [],
    transformedRecord.salt_tokens || []
  );

  return {
    name: chooseLongerText(existingMedicine?.name, transformedRecord.raw_name),
    normalized_name:
      transformedRecord.normalized_name || existingMedicine?.normalized_name || "",
    salt: chooseLongerText(existingMedicine?.salt, transformedRecord.raw_salt),
    normalized_salt:
      transformedRecord.normalized_salt || existingMedicine?.normalized_salt || null,
    primary_salt_key:
      transformedRecord.primary_salt_key || existingMedicine?.primary_salt_key || null,
    salt_tokens: saltTokens,
    dosage: transformedRecord.dosage || existingMedicine?.dosage || null,
    pack_size: transformedRecord.pack_size || existingMedicine?.pack_size || null,
    image_url: chooseImageUrl(existingMedicine?.image_url, transformedRecord.image_url),
    manufacturer: chooseLongerText(existingMedicine?.manufacturer, transformedRecord.manufacturer),
    description: chooseLongerText(
      existingMedicine?.description,
      transformedRecord.description
    ),
    side_effects: mergeUniqueStrings(
      existingMedicine?.side_effects || [],
      transformedRecord.side_effects || []
    ),
    faq: mergeFaq(existingMedicine?.faq || [], transformedRecord.faq || []),
    source_platforms: sourcePlatforms,
    last_ingested_at: new Date(),
  };
}

function mergeRecordsForMedicine(records) {
  const merged = records.reduce(
    (accumulator, record) => ({
      raw_name: chooseLongerText(accumulator.raw_name, record.raw_name),
      normalized_name: accumulator.normalized_name || record.normalized_name,
      raw_salt: chooseLongerText(accumulator.raw_salt, record.raw_salt),
      normalized_salt: accumulator.normalized_salt || record.normalized_salt,
      primary_salt_key: accumulator.primary_salt_key || record.primary_salt_key,
      salt_tokens: mergeUniqueStrings(accumulator.salt_tokens || [], record.salt_tokens || []),
      dosage: accumulator.dosage || record.dosage,
      pack_size: accumulator.pack_size || record.pack_size,
      image_url: chooseImageUrl(accumulator.image_url, record.image_url),
      manufacturer: chooseLongerText(accumulator.manufacturer, record.manufacturer),
      description: chooseLongerText(accumulator.description, record.description),
      side_effects: mergeUniqueStrings(
        accumulator.side_effects || [],
        record.side_effects || []
      ),
      faq: mergeFaq(accumulator.faq || [], record.faq || []),
      platforms: mergeUniqueStrings(accumulator.platforms || [], [record.platform]),
      price:
        typeof accumulator.price === "number" && !Number.isNaN(accumulator.price)
          ? accumulator.price
          : typeof record.price === "number" && !Number.isNaN(record.price)
            ? record.price
            : null,
      source_type:
        accumulator.source_type === "product" || record.source_type === "product"
          ? "product"
          : accumulator.source_type || record.source_type || "information",
    }),
    {
      raw_name: null,
      normalized_name: null,
      raw_salt: null,
      normalized_salt: null,
      primary_salt_key: null,
      salt_tokens: [],
      dosage: null,
      pack_size: null,
      image_url: null,
      manufacturer: null,
      description: null,
      side_effects: [],
      faq: [],
      platforms: [],
      price: null,
      source_type: "information",
    }
  );

  const canonicalKey = buildCanonicalKey({
    normalized_name: merged.normalized_name,
    normalized_salt: merged.normalized_salt,
    dosage: merged.dosage,
  });

  return {
    ...merged,
    canonical_key: canonicalKey,
    match_keys: getEntityMatchKeys({
      normalized_name: merged.normalized_name,
      normalized_salt: merged.normalized_salt,
      dosage: merged.dosage,
    }),
  };
}

function buildCompletenessCandidate(existingMedicine, mergedRecord, hasExistingPrice) {
  return {
    raw_name: chooseLongerText(existingMedicine?.name, mergedRecord.raw_name),
    raw_salt: chooseLongerText(existingMedicine?.salt, mergedRecord.raw_salt),
    image_url: chooseImageUrl(existingMedicine?.image_url, mergedRecord.image_url),
    manufacturer: chooseLongerText(existingMedicine?.manufacturer, mergedRecord.manufacturer),
    description: chooseLongerText(existingMedicine?.description, mergedRecord.description),
    dosage: mergedRecord.dosage || existingMedicine?.dosage || null,
    side_effects: mergeUniqueStrings(
      existingMedicine?.side_effects || [],
      mergedRecord.side_effects || []
    ),
    source_type:
      mergedRecord.source_type === "product" || hasExistingPrice ? "product" : "information",
    price:
      typeof mergedRecord.price === "number" && !Number.isNaN(mergedRecord.price)
        ? mergedRecord.price
        : hasExistingPrice
          ? 0
          : null,
  };
}

function choosePreferredPriceRecord(existingRecord, incomingRecord) {
  if (!existingRecord) return incomingRecord;

  const existingHasUrl = !!existingRecord.url;
  const incomingHasUrl = !!incomingRecord.url;

  if (incomingHasUrl && !existingHasUrl) return incomingRecord;

  const existingHasPrice =
    typeof existingRecord.price === "number" && !Number.isNaN(existingRecord.price);
  const incomingHasPrice =
    typeof incomingRecord.price === "number" && !Number.isNaN(incomingRecord.price);

  if (incomingHasPrice && !existingHasPrice) return incomingRecord;
  return incomingRecord;
}

function buildMatchingGroups(records, existingMedicines) {
  const groups = existingMedicines.map((medicine) => ({
    existingMedicines: [medicine],
    records: [],
    matchKeys: new Set(getEntityMatchKeys(medicine)),
  }));

  records.forEach((record) => {
    const recordMatchKeys = getEntityMatchKeys(record);
    const matchedGroups = groups.filter((group) =>
      hasMatchKeyOverlap(group.matchKeys, recordMatchKeys)
    );

    if (matchedGroups.length === 0) {
      groups.push({
        existingMedicines: [],
        records: [record],
        matchKeys: new Set(recordMatchKeys),
      });
      return;
    }

    const primaryGroup = matchedGroups[0];
    primaryGroup.records.push(record);
    primaryGroup.matchKeys = mergeMatchKeySets(primaryGroup.matchKeys, new Set(recordMatchKeys));

    matchedGroups.slice(1).forEach((groupToMerge) => {
      primaryGroup.existingMedicines.push(...groupToMerge.existingMedicines);
      primaryGroup.records.push(...groupToMerge.records);
      primaryGroup.matchKeys = mergeMatchKeySets(primaryGroup.matchKeys, groupToMerge.matchKeys);

      const groupIndex = groups.indexOf(groupToMerge);
      if (groupIndex >= 0) groups.splice(groupIndex, 1);
    });
  });

  return groups.filter((group) => group.records.length > 0);
}

async function bulkUpsertProducts(transformedRecords, options = {}) {
  const { batchSize = 25 } = options;
  const candidates = transformedRecords.filter(
    (record) => Array.isArray(record?.match_keys) && record.match_keys.length > 0
  );

  if (candidates.length === 0) {
    return {
      medicinesTouched: 0,
      priceEntriesUpserted: 0,
    };
  }

  let medicinesTouched = 0;
  let priceEntriesUpserted = 0;
  const incompleteMedicines = [];

  for (let index = 0; index < candidates.length; index += batchSize) {
    const batchRecords = candidates.slice(index, index + batchSize);
    const existingMedicineQuery = buildBatchMatchQuery(batchRecords);
    const existingMedicines = existingMedicineQuery
      ? await Medicine.find(existingMedicineQuery, null, { lean: true })
      : [];

    const existingMedicineIds = existingMedicines.map((medicine) => medicine._id).filter(Boolean);
    const existingPrices =
      existingMedicineIds.length > 0
        ? await Price.find(
            { medicine_id: { $in: existingMedicineIds } },
            { medicine_id: 1, platform: 1, price: 1, url: 1, source_type: 1 },
            { lean: true }
          )
        : [];

    const existingPriceMedicineIds = new Set(
      existingPrices.map((priceEntry) => String(priceEntry.medicine_id))
    );
    const existingPricesByMedicineId = new Map();
    existingPrices.forEach((priceEntry) => {
      const medicineId = String(priceEntry.medicine_id);
      const bucket = existingPricesByMedicineId.get(medicineId) || [];
      bucket.push(priceEntry);
      existingPricesByMedicineId.set(medicineId, bucket);
    });

    const groups = buildMatchingGroups(batchRecords, existingMedicines);
    const medicineOperations = [];
    const completeGroups = [];

    groups.forEach((group) => {
      const targetMedicine = pickPrimaryExistingMedicine(group.existingMedicines);
      const mergedExistingMedicine = mergeExistingMedicineSnapshots(group.existingMedicines);
      const mergedRecord = mergeRecordsForMedicine(group.records);

      if (!mergedRecord.canonical_key) {
        incompleteMedicines.push({
          canonical_key: null,
          raw_name: mergedRecord.raw_name,
          platforms: mergedRecord.platforms || [],
          missingFields: ["matchingKey"],
        });
        return;
      }

      const hasExistingPrice = group.existingMedicines.some((medicine) =>
        existingPriceMedicineIds.has(String(medicine._id))
      );
      const completenessCandidate = buildCompletenessCandidate(
        mergedExistingMedicine,
        mergedRecord,
        hasExistingPrice
      );
      const missingFields = getMissingDetailFields(completenessCandidate);

      if (missingFields.length > 0) {
        incompleteMedicines.push({
          canonical_key: targetMedicine?.canonical_key || mergedRecord.canonical_key,
          raw_name: mergedRecord.raw_name,
          platforms: mergedRecord.platforms || [],
          missingFields,
        });
        return;
      }

      const medicineUpdate = buildMedicineUpdate(mergedExistingMedicine, mergedRecord);

      if (targetMedicine?._id) {
        medicineOperations.push({
          updateOne: {
            filter: { _id: targetMedicine._id },
            update: { $set: medicineUpdate },
          },
        });
      } else {
        medicineOperations.push({
          updateOne: {
            filter: { canonical_key: mergedRecord.canonical_key },
            update: {
              $set: medicineUpdate,
              $setOnInsert: {
                canonical_key: mergedRecord.canonical_key,
              },
            },
            upsert: true,
          },
        });
      }

      completeGroups.push({
        existingMedicineId: targetMedicine?._id ? String(targetMedicine._id) : null,
        existingMedicineIds: group.existingMedicines
          .map((medicine) => String(medicine._id))
          .filter(Boolean),
        canonicalKey: targetMedicine?.canonical_key || mergedRecord.canonical_key,
        records: group.records,
      });
    });

    if (medicineOperations.length > 0) {
      await Medicine.bulkWrite(medicineOperations, { ordered: false });
      medicinesTouched += completeGroups.length;
    }

    if (completeGroups.length === 0) {
      continue;
    }

    const canonicalKeysToResolve = [
      ...new Set(
        completeGroups
          .filter((group) => !group.existingMedicineId)
          .map((group) => group.canonicalKey)
          .filter(Boolean)
      ),
    ];

    const insertedMedicines =
      canonicalKeysToResolve.length > 0
        ? await Medicine.find(
            { canonical_key: { $in: canonicalKeysToResolve } },
            { _id: 1, canonical_key: 1 },
            { lean: true }
          )
        : [];
    const insertedIdByCanonicalKey = new Map(
      insertedMedicines.map((medicine) => [medicine.canonical_key, String(medicine._id)])
    );

    const priceOperations = [];
    const duplicateMedicineIdsToDelete = new Set();

    completeGroups.forEach((group) => {
      const medicineId =
        group.existingMedicineId || insertedIdByCanonicalKey.get(group.canonicalKey);
      if (!medicineId) return;

      const preferredPriceByPlatform = new Map();
      const duplicateMedicineIds = (group.existingMedicineIds || []).filter(
        (existingMedicineId) => existingMedicineId !== medicineId
      );
      const targetExistingPrices = existingPricesByMedicineId.get(medicineId) || [];
      const duplicateExistingPrices = duplicateMedicineIds.flatMap(
        (duplicateMedicineId) => existingPricesByMedicineId.get(duplicateMedicineId) || []
      );

      targetExistingPrices.forEach((priceEntry) => {
        preferredPriceByPlatform.set(priceEntry.platform, priceEntry);
      });
      duplicateExistingPrices.forEach((priceEntry) => {
        if (!preferredPriceByPlatform.has(priceEntry.platform)) {
          preferredPriceByPlatform.set(priceEntry.platform, priceEntry);
        }
      });

      group.records.forEach((record) => {
        if (
          typeof record.price !== "number" ||
          Number.isNaN(record.price) ||
          !record.platform ||
          !record.url
        ) {
          return;
        }

        const currentRecord = preferredPriceByPlatform.get(record.platform);
        preferredPriceByPlatform.set(
          record.platform,
          choosePreferredPriceRecord(currentRecord, record)
        );
      });

        preferredPriceByPlatform.forEach((record, platform) => {
        priceOperations.push({
          updateOne: {
            filter: {
              medicine_id: medicineId,
              platform,
            },
            update: {
              $set: {
                price: record.price,
                url: record.url,
                source_type: record.source_type,
              },
            },
            upsert: true,
          },
        });
      });

      duplicateMedicineIds.forEach((duplicateMedicineId) =>
        duplicateMedicineIdsToDelete.add(duplicateMedicineId)
      );
    });

    if (priceOperations.length > 0) {
      await Price.bulkWrite(priceOperations, { ordered: false });
      priceEntriesUpserted += priceOperations.length;
    }

    if (duplicateMedicineIdsToDelete.size > 0) {
      const duplicateMedicineIds = [...duplicateMedicineIdsToDelete];
      await Price.deleteMany({ medicine_id: { $in: duplicateMedicineIds } });
      await Medicine.deleteMany({ _id: { $in: duplicateMedicineIds } });
    }
  }

  return {
    medicinesTouched,
    priceEntriesUpserted,
    rejectedIncompleteMedicines: incompleteMedicines.length,
    incompleteMedicines,
  };
}

async function upsertProduct(transformedRecord) {
  const summary = await bulkUpsertProducts([transformedRecord], { batchSize: 1 });
  return summary.medicinesTouched > 0 ? transformedRecord.canonical_key : null;
}

module.exports = {
  bulkUpsertProducts,
  upsertProduct,
};
